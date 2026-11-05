#!/usr/bin/env python3
"""avito.ru scraper — Playwright engine (the primary browser engine).

    python3 playwright_scraper.py --mode listing \\
        --url https://www.avito.ru/moskva/telefony/mobilnye_telefony/apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc \\
        --pages 3 --out live/iphones

    python3 playwright_scraper.py --mode item --from live/iphones.json --max-products 10
    python3 playwright_scraper.py --mode seller --url https://www.avito.ru/brands/bush_market

With `AVITO_CDP_ENDPOINT` set (the recommended path) it connects to the
Scraping Browser API, which brings its own Russian exit. Without one it
launches a local Chromium on `AVITO_PROXY` — measured 5 of 5 fresh local
sessions met the GeeTest wall, so a local run pays a captcha per session.
"""

from __future__ import annotations

import sys

import browser_bridge
import fingerprint_client
import page_flow
import proxy_pool

# Imported at MODULE level on purpose: the offline suite must be able to skip
# this engine when Playwright is absent, and it can only tell that if the
# import fails here rather than deep inside start() (2scraper family rule).
from playwright.sync_api import sync_playwright


class PlaywrightDriver:
    name = "playwright"

    def __init__(self, args):
        self.args = args
        self._cdp = None
        self.autosolve_armed = False
        self.autosolve_events = []
        self.autosolve_notes = []
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def start(self):
        self._pw = sync_playwright().start()
        over_cdp = bool(self.args.cdp_endpoint)
        if over_cdp:
            # Wrapped so a failure cannot print the endpoint, which carries a
            # password. Playwright repeats it five times in one error — the
            # message plus a four-line call log (2scraper family rule).
            try:
                self._browser = self._pw.chromium.connect_over_cdp(
                    self.args.cdp_endpoint, timeout=self.args.timeout * 1000)
            except Exception as exc:
                why = page_flow.cdp_credential_refusal(str(exc))
                raise browser_bridge.BridgeError(
                    "could not connect over CDP: %s: %s%s"
                    % (type(exc).__name__, browser_bridge.mask_text(str(exc))[:300],
                       "\n  -> " + why if why else "")) from None
            contexts = self._browser.contexts
            self._context = contexts[0] if contexts else self._browser.new_context()
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
        else:
            launch: dict = {"headless": self.args.headless}
            if self.args.proxy:
                # Through Playwright's own fields, never `--proxy-server=`,
                # which would put the credential on the browser's command line
                # where `ps` reads it.
                launch["proxy"] = proxy_pool.to_playwright(self.args.proxy)
            self._browser = self._pw.chromium.launch(**launch)
            # No hardcoded user agent: it drifts from the installed Chromium.
            # A fingerprint, when asked for, is applied HERE only — over CDP
            # the remote browser brings its own (2scraper family rule).
            fp = self._fingerprint() if getattr(self.args, "fingerprint", False) else None
            ctx = {"locale": "ru-RU"}
            if fp:
                ctx.update(fingerprint_client.playwright_context_kwargs(fp))
            self._context = self._browser.new_context(**ctx)
            if fp:
                self._context.add_init_script(fingerprint_client.playwright_init_script(fp))
            self._page = self._context.new_page()
            if fp:
                self._apply_ua_override(self._page, fp)
        self._page.set_default_timeout(self.args.timeout * 1000)
        if over_cdp:
            self._arm_autosolve()

    # --- 2Captcha fingerprint (local browser only) ---------------------------
    # Fetched once per run: a rotation relaunches the browser on a new exit
    # with the SAME identity, and each fetch is billed.
    _fp = None
    fingerprint_facts = None

    def _fingerprint(self):
        if self._fp is None:
            # FingerprintError propagates: run() reports it and exits 5 before
            # any page is fetched without the identity that was asked for.
            self._fp = fingerprint_client.get_fingerprint(
                self.args.twocaptcha_key, tags=self.args.fp_tags,
                country=self.args.fp_country, refresh=self.args.fp_refresh)
            self.fingerprint_facts = {
                "id": self._fp.get("id"), "country": self._fp.get("country"),
                "user_agent": fingerprint_client.fingerprint_user_agent(self._fp),
                "not_in_response": fingerprint_client.missing_fields(self._fp),
                "client_hints": False, "readback": None, "mismatches": None,
            }
            print("[i] fingerprint %s (%s): %s" % (
                self._fp.get("id"), self._fp.get("country"),
                (self.fingerprint_facts["user_agent"] or "")[:90]), file=sys.stderr)
            if self.fingerprint_facts["not_in_response"]:
                print("[!] fingerprint fields not in the response, left as the "
                      "browser's own: %s" % ", ".join(self.fingerprint_facts["not_in_response"]),
                      file=sys.stderr)
        return self._fp

    def _apply_ua_override(self, page, fp):
        """User agent WITH client hints, so Sec-CH-UA* and userAgentData match
        the User-Agent header. Per page: CDP emulation is per target."""
        override = fingerprint_client.user_agent_override(fp)
        if not override:
            return
        try:
            self._context.new_cdp_session(page).send("Emulation.setUserAgentOverride", override)
            self.fingerprint_facts["client_hints"] = True
        except Exception as exc:  # noqa: BLE001
            print("[!] could not set the fingerprint's client hints: %s"
                  % str(exc).splitlines()[0][:120], file=sys.stderr)

    def _read_back_fingerprint(self):
        """Once, after the first page: what the live page reports, compared
        with the fingerprint. Recorded in the run metadata."""
        facts = self.fingerprint_facts
        if not self._fp or facts is None or facts.get("readback") is not None:
            return
        try:
            seen = self._page.evaluate(fingerprint_client.READBACK_JS)
        except Exception as exc:  # noqa: BLE001
            facts["readback"] = {"error": str(exc).splitlines()[0][:120]}
            return
        facts["readback"] = seen
        facts["mismatches"] = fingerprint_client.readback_mismatches(self._fp, seen)
        if facts["mismatches"]:
            print("[!] the page does not report the fingerprint for: %s"
                  % "; ".join(facts["mismatches"]), file=sys.stderr)

    def _arm_autosolve(self):
        """Rung 1: ask the Scraping Browser to solve the wall itself.

        `Captcha.setAutoSolve` is the only method of the `Captcha` domain the
        endpoint accepts — `Captcha.enable` answers "wasn't found" (measured
        2026-10-06). Every failure here is a warning: the paid rung follows.
        """
        if self.args.solve_captcha == "never":
            return
        try:
            self._cdp = self._context.new_cdp_session(self._page)
        except Exception as exc:
            self.autosolve_notes.append("no CDP session: %s"
                                        % browser_bridge.mask_text(str(exc))[:120])
            return
        for event in ("Captcha.detected", "Captcha.solveStarted",
                      "Captcha.solveFinished", "Captcha.solveFailed"):
            try:
                self._cdp.on(event, self._on_captcha_event(event))
            except Exception:
                pass
        try:
            self._cdp.send("Captcha.setAutoSolve", {"autoSolve": True})
            self.autosolve_armed = True
        except Exception as exc:
            self.autosolve_notes.append("Captcha.setAutoSolve unavailable: %s"
                                        % str(exc).splitlines()[0][:100])

    def _on_captcha_event(self, name):
        def handler(payload):
            self.autosolve_events.append({"event": name, "payload": str(payload)[:300]})
            if self.args.verbose:
                print("[captcha] %s" % name, file=sys.stderr)
        return handler

    def navigate(self, url):
        response = self._page.goto(url, wait_until="domcontentloaded",
                                   timeout=self.args.timeout * 1000)
        if self._fp is not None:
            self._read_back_fingerprint()
        return response.status if response else None

    def content(self):
        try:
            return self._page.content()
        except Exception:
            return ""

    def count(self, selector):
        try:
            return self._page.locator(selector).count()
        except Exception:
            return 0

    def run_js(self, body):
        return self._page.evaluate("() => { %s }" % body)

    def scroll_to_bottom(self):
        """Scroll to the end of the document; return its height. A real
        function object, not an evaluated string (CSP-safe, family rule)."""
        return self._page.evaluate(
            "() => { window.scrollTo(0, document.body.scrollHeight); return document.body.scrollHeight; }")

    def sleep(self, ms):
        self._page.wait_for_timeout(ms)

    def stop(self):
        # Over CDP the page and context belong to the remote profile; closing
        # the browser handle ends OUR connection and releases the profile's
        # one-connection lock — a timed-out, unclosed connection held one for
        # 25+ minutes in a sibling repo.
        for closer in ((self._browser,) if self.args.cdp_endpoint
                       else (self._page, self._context, self._browser)):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = self._browser = self._context = self._page = None


def main(argv=None) -> int:
    parser = browser_bridge.build_parser(
        "Scrape avito.ru listings, items and seller profiles with Playwright.")
    # Playwright only: the other two engines do not apply a fingerprint, and
    # argparse refuses the flag there (exit 2) instead of ignoring it.
    fingerprint_client.add_arguments(parser)
    args = browser_bridge.parse_args(parser, argv)
    return browser_bridge.run(args, PlaywrightDriver(args))


if __name__ == "__main__":
    sys.exit(main())
