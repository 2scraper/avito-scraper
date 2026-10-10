#!/usr/bin/env python3
"""avito.ru scraper — pyppeteer engine.

Behaves identically to `playwright_scraper.py`. **pyppeteer is effectively
unmaintained** and its own README points at Playwright; it is kept for parity
with the rest of this family and should not be anyone's first choice.

Unlike Selenium it CAN use an authenticated Scraping Browser endpoint:
`browserWSEndpoint` takes a full `ws://user:pass@host:port` and authenticates
on the WebSocket upgrade.

    python3 puppeteer_scraper.py --mode listing \\
        --url https://www.avito.ru/moskva/telefony/mobilnye_telefony/apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc \\
        --pages 2
"""

from __future__ import annotations

import asyncio
import sys
from urllib.parse import urlparse

import browser_bridge

# Module level, so the offline suite can skip this engine when pyppeteer is
# absent instead of importing cleanly and failing on first launch (§10).
from pyppeteer import connect, launch


class PuppeteerDriver:
    name = "pyppeteer"

    def __init__(self, args):
        self.args = args
        self._loop = None
        self._browser = None
        self._page = None
        self.autosolve_armed = False
        self.autosolve_events = []
        self.autosolve_notes = []

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def start(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.set_exception_handler(_quiet_detach)
        if self.args.cdp_endpoint:
            try:
                self._browser = self._run(connect(browserWSEndpoint=self.args.cdp_endpoint))
            except Exception as exc:
                raise browser_bridge.BridgeError(
                    "could not connect over CDP: %s: %s"
                    % (type(exc).__name__, browser_bridge.mask_text(str(exc))[:300])) from None
            pages = self._run(self._browser.pages())
            self._page = pages[0] if pages else self._run(self._browser.newPage())
            self._arm_autosolve()
        else:
            options = {"headless": self.args.headless, "args": ["--lang=ru-RU"]}
            credentials = None
            if self.args.proxy:
                # The address goes on Chromium's command line; the credentials
                # go through the driver's own channel (`page.authenticate`),
                # never into argv where `ps` reads them.
                parts = urlparse(self.args.proxy)
                options["args"].append("--proxy-server=%s://%s:%s"
                                       % (parts.scheme or "http", parts.hostname, parts.port))
                if parts.username:
                    credentials = {"username": parts.username, "password": parts.password or ""}
            self._browser = self._run(launch(**options))
            self._page = self._run(self._browser.newPage())
            if credentials:
                self._run(self._page.authenticate(credentials))

    def _arm_autosolve(self):
        """Rung 1 — see playwright_scraper.PlaywrightDriver._arm_autosolve."""
        if self.args.solve_captcha == "never":
            return
        try:
            session = self._run(self._page.target.createCDPSession())
            for event in ("Captcha.detected", "Captcha.solveStarted",
                          "Captcha.solveFinished", "Captcha.solveFailed"):
                session.on(event, self._recorder(event))
            self._run(session.send("Captcha.setAutoSolve", {"autoSolve": True}))
            self.autosolve_armed = True
        except Exception as exc:  # noqa: BLE001
            self.autosolve_notes.append("Captcha.setAutoSolve unavailable: %s"
                                        % str(exc).splitlines()[0][:100])

    def _recorder(self, name):
        def handler(payload=None):
            self.autosolve_events.append({"event": name, "payload": str(payload)[:300]})
        return handler

    def navigate(self, url):
        response = self._run(self._page.goto(
            url, {"waitUntil": "domcontentloaded", "timeout": self.args.timeout * 1000}))
        return response.status if response else None

    def content(self):
        try:
            return self._run(self._page.content())
        except Exception:
            return ""

    def count(self, selector):
        try:
            return len(self._run(self._page.querySelectorAll(selector)))
        except Exception:
            return 0

    def run_js(self, body):
        return self._run(self._page.evaluate("() => { %s }" % body))

    def scroll_to_bottom(self):
        """Scroll to the end of the document; return its height."""
        return self._run(self._page.evaluate(
            "() => { window.scrollTo(0, document.body.scrollHeight); return document.body.scrollHeight; }"))

    def sleep(self, ms):
        self._run(asyncio.sleep(ms / 1000.0))

    def stop(self):
        try:
            if self._browser is not None:
                if self.args.cdp_endpoint:
                    # Disconnect, do not close: `close()` would shut the
                    # remote profile's browser, not just end our session.
                    self._run(self._browser.disconnect())
                else:
                    self._run(self._browser.close())
        except Exception:
            pass
        finally:
            if self._loop is not None:
                self._loop.close()
            self._browser = self._page = self._loop = None


def _quiet_detach(loop, context):
    """Swallow ONE known pyppeteer noise, report everything else.

    Over the Scraping Browser, pyppeteer tries to detach from targets the
    remote profile already closed and leaves the failed futures unretrieved:
    "Protocol error (Target.detachFromTarget): No session with given id",
    twice per run, measured 2026-10-06 on a run that completed correctly.
    """
    exc = context.get("exception")
    if exc is not None and "Target.detachFromTarget" in str(exc):
        return
    loop.default_exception_handler(context)


def main(argv=None) -> int:
    parser = browser_bridge.build_parser(
        "Scrape avito.ru listings, items and seller profiles with pyppeteer.")
    args = browser_bridge.parse_args(parser, argv)
    return browser_bridge.run(args, PuppeteerDriver(args))


if __name__ == "__main__":
    sys.exit(main())
