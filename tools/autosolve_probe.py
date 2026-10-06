#!/usr/bin/env python3
"""Measure rung 1: does the Scraping Browser's own auto-solve clear avito.ru's
firewall captcha?

Each iteration clears the profile's cookies (so the firewall is met again),
arms `Captcha.setAutoSolve`, opens the URL, presses «Продолжить» if the wall
shows it, and records every `Captcha.*` event, the GeeTest requests and the
outcome. `--no-autosolve` is the CONTROL: the same steps with nothing solving,
so a wall that expires on its own is not mistaken for a solve (CLAUDE.md §19).

    python3 tools/autosolve_probe.py --iterations 3
    python3 tools/autosolve_probe.py --iterations 3 --no-autosolve

Measured 2026-10-06: 0 of 3 cleared with auto-solve (`Captcha.detected`, then
`Captcha.solveFailed`), 0 of 3 walls cleared on their own.

Credentials come from .env (AVITO_CDP_ENDPOINT); nothing secret is printed.
ONE connection per iteration, always closed: a profile allows one live
connection, and an unclosed one holds it (CLAUDE.md §20).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import env_config  # noqa: E402
import product_parser as P  # noqa: E402

DEFAULT_URL = "https://www.avito.ru/moskva/telefony/mobilnye_telefony/apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc"
_VENDOR = re.compile(r"geetest|gcaptcha|hcaptcha|firewall", re.I)


def iteration(pw, endpoint, url, autosolve, wait_s):
    t0 = time.monotonic()
    log = {"events": [], "requests": [], "autosolve": autosolve}
    browser = pw.chromium.connect_over_cdp(endpoint, timeout=60000)
    try:
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.clearBrowserCookies")
        for ev in ("Captcha.detected", "Captcha.solveStarted", "Captcha.solveFinished",
                   "Captcha.solveFailed"):
            cdp.on(ev, (lambda name: lambda p: log["events"].append(
                (round(time.monotonic() - t0, 1), name)))(ev))
        try:
            cdp.send("Captcha.setAutoSolve", {"autoSolve": autosolve})
        except Exception as exc:  # noqa: BLE001
            log["setAutoSolve_error"] = str(exc).splitlines()[0][:100]
        page.on("request", lambda r: log["requests"].append(r.url.split("?")[0])
                if _VENDOR.search(r.url) and not r.url.startswith("chrome-extension") else None)
        response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        log["first_status"] = response.status if response else None
        log["first_state"] = P.detect_page_state(page.content(), url=url)
        button = page.get_by_role("button", name=re.compile("Продолжить"))
        if button.count():
            button.first.click()
        deadline = time.monotonic() + wait_s
        outcome = "walled"
        while time.monotonic() < deadline:
            page.wait_for_timeout(2000)
            if P.detect_page_state(page.content(), url=url) == "content":
                outcome = "cleared"
                break
        log["outcome"] = outcome
        log["seconds"] = round(time.monotonic() - t0, 1)
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass
    return log


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--iterations", type=int, default=3)
    ap.add_argument("--wait", type=int, default=120, help="seconds to wait per wall")
    ap.add_argument("--no-autosolve", action="store_true", help="the control arm")
    ap.add_argument("--pause", type=int, default=40,
                    help="seconds between iterations — a just-closed profile can "
                         "still report profile_locked for under a minute (measured)")
    args = ap.parse_args()
    env_config.load_env(ROOT / ".env")
    endpoint = env_config.env_value("AVITO_CDP_ENDPOINT")
    if not endpoint:
        sys.exit("!! AVITO_CDP_ENDPOINT is not set in .env")
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as pw:
        for i in range(args.iterations):
            if i:
                time.sleep(args.pause)
            try:
                log = iteration(pw, endpoint, args.url, not args.no_autosolve, args.wait)
            except Exception as exc:  # noqa: BLE001
                log = {"error": "%s: %s" % (type(exc).__name__,
                                            re.sub(r"ws://[^@\s]+@", "ws://***@", str(exc))[:160])}
            results.append(log)
            print(json.dumps(log, ensure_ascii=False))
    cleared = sum(1 for r in results if r.get("outcome") == "cleared")
    walled = sum(1 for r in results if r.get("first_state") in ("captcha", "challenge"))
    print("\n%s: %d iteration(s), %d met the wall, %d cleared"
          % ("autosolve" if not args.no_autosolve else "CONTROL (no solving)",
             len(results), walled, cleared))
    return 0


if __name__ == "__main__":
    sys.exit(main())
