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
import proxy_pool

# Imported at MODULE level on purpose: the offline suite must be able to skip
# this engine when Playwright is absent, and it can only tell that if the
# import fails here rather than deep inside start() (CLAUDE.md §10).
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
            # message plus a four-line call log (CLAUDE.md §8).
            try:
                self._browser = self._pw.chromium.connect_over_cdp(
                    self.args.cdp_endpoint, timeout=self.args.timeout * 1000)
            except Exception as exc:
                raise browser_bridge.BridgeError(
                    "could not connect over CDP: %s: %s"
                    % (type(exc).__name__, browser_bridge.mask_text(str(exc))[:300])) from None
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
            # No user agent and no fingerprint: a hardcoded UA drifts from the
            # installed Chromium, and over CDP the remote browser brings its
            # own (CLAUDE.md §8).
            self._context = self._browser.new_context(locale="ru-RU")
            self._page = self._context.new_page()
        self._page.set_default_timeout(self.args.timeout * 1000)
        if over_cdp:
            self._arm_autosolve()

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
    args = browser_bridge.parse_args(parser, argv)
    return browser_bridge.run(args, PlaywrightDriver(args))


if __name__ == "__main__":
    sys.exit(main())
