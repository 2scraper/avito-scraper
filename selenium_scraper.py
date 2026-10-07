#!/usr/bin/env python3
"""avito.ru scraper — Selenium engine.

Behaves identically to `playwright_scraper.py`: same flags, same exit codes,
same run metadata. It exists for parity and is demoted in priority, not in
correctness.

Two facts decide how it reaches avito.ru:

* **Selenium cannot use an authenticated remote CDP endpoint.** chromedriver's
  `debuggerAddress` takes a bare host:port with nowhere to put the Scraping
  Browser's password, so `--cdp-endpoint` is refused here, not ignored.
* **It CAN use an authenticated proxy — through a loopback forwarder.**
  Chrome's `--proxy-server` flag carries no credential; sibling repos
  stripped it and warned. On avito.ru that would make Selenium useless: the
  2Captcha gateway login IS the Russian region and the session pin. WebDriver
  BiDi's `add_auth_handler` deadlocked on Selenium 4.50 (measured), so
  Chrome is pointed at `proxy_forwarder.ProxyForwarder` on 127.0.0.1, which
  adds the credential upstream. It never reaches argv or disk.

A local browser meets the GeeTest wall on every fresh session (5 of 5
measured), so a Selenium run on avito.ru pays one solve per session.

    python3 selenium_scraper.py --mode listing \\
        --url https://www.avito.ru/moskva/telefony/mobilnye_telefony/apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc
"""

from __future__ import annotations

import sys
import time

import browser_bridge
import proxy_pool
from proxy_forwarder import ProxyForwarder

# Module level, so the offline suite can tell "engine absent" from "engine
# broken" (2scraper family rule).
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


class SeleniumDriver:
    name = "selenium"

    def __init__(self, args):
        self.args = args
        self._driver = None
        self._forwarder = None
        self.autosolve_armed = False
        self.autosolve_events = []
        self.autosolve_notes = ["selenium has no Scraping Browser path; rung 1 does not apply"]

    def start(self):
        if self.args.cdp_endpoint:
            raise browser_bridge.BridgeError(
                "selenium cannot use --cdp-endpoint: chromedriver's debuggerAddress "
                "takes a bare host:port and has nowhere to put the endpoint's "
                "password. Use playwright_scraper.py or puppeteer_scraper.py for "
                "the Scraping Browser API.")
        options = Options()
        if self.args.headless:
            options.add_argument("--headless=new")
        options.add_argument("--lang=ru-RU")
        if self.args.proxy:
            bare, credentials = proxy_pool.split_credentials(self.args.proxy)
            if credentials:
                self._forwarder = ProxyForwarder(self.args.proxy).start()
                bare = self._forwarder.url
            options.add_argument("--proxy-server=%s" % bare)
        self._driver = webdriver.Chrome(options=options)
        self._driver.set_page_load_timeout(self.args.timeout)

    def navigate(self, url):
        self._driver.get(url)
        # Selenium exposes no response object, so there is no status. The
        # classifier stays correct without one: the wall is recognised by its
        # own title, content by its own markers (product_parser).
        return None

    def content(self):
        try:
            return self._driver.page_source
        except Exception:
            return ""

    def count(self, selector):
        try:
            return len(self._driver.find_elements(By.CSS_SELECTOR, selector))
        except Exception:
            return 0

    def run_js(self, body):
        # Selenium's execute_script takes a function BODY — the bridge's
        # snippet is written in that shape.
        return self._driver.execute_script(body)

    def sleep(self, ms):
        time.sleep(ms / 1000.0)

    def stop(self):
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
        if self._forwarder is not None:
            self._forwarder.stop()
            self._forwarder = None


def main(argv=None) -> int:
    parser = browser_bridge.build_parser(
        "Scrape avito.ru listings, items and seller profiles with Selenium.")
    args = browser_bridge.parse_args(parser, argv)
    return browser_bridge.run(args, SeleniumDriver(args))


if __name__ == "__main__":
    sys.exit(main())
