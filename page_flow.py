"""
page_flow.py
------------
avito.ru's page-state policy, shared by all three engines as DATA, so an
engine cannot quietly disagree with its twins about whether a page is worth
waiting for, paying for or rotating away from (2scraper family rule).

What a page can answer (product_parser.detect_page_state)
--------------------------------------------------------
    content    parse it
    challenge  the firewall's proof-of-work variant (HTTP 439). The page runs
               `/web/3/firewallPow/get` + `/verify` by itself and reloads to
               content. Measured 2026-10-06: the Scraping Browser cleared it
               with no help, twice. Wait; never click, never reload.
    captcha    the firewall's captcha variant (HTTP 429, «Продолжить»).
               GeeTest v4 slide on every run so far. Solve it — the
               browser's own auto-solve first, then the 2Captcha solver API
               through the SAME exit (browser_bridge.solve_wall).
    empty      a served listing with no cards on it: a correct answer, not a
               fault. Report it, do not retry it.
    blocked    a refusal with no puzzle in it. Rotate the exit.
    unknown    served but not recognisable yet. Retry once.

Why a wall is not "the site is down"
------------------------------------
The firewall's own text says why it appears: "С него идёт очень много
запросов" — too many requests from this address. So it is an ADDRESS
decision, and `rotate` is True for both firewall variants: a fresh exit
is worth more than a second attempt from a scored one.
"""

from typing import Dict

STATE_POLICY: Dict[str, Dict[str, bool]] = {
    "content":   {"retry": False, "solve": False, "wait": False, "rotate": False, "blocked": False},
    "challenge": {"retry": False, "solve": False, "wait": True,  "rotate": True,  "blocked": True},
    "captcha":   {"retry": False, "solve": True,  "wait": True,  "rotate": True,  "blocked": True},
    "empty":     {"retry": False, "solve": False, "wait": False, "rotate": False, "blocked": False},
    "blocked":   {"retry": False, "solve": False, "wait": False, "rotate": True,  "blocked": True},
    "unknown":   {"retry": True,  "solve": False, "wait": False, "rotate": False, "blocked": False},
}

# How long the self-clearing proof-of-work is given before the page is
# re-classified. Measured: the clearance completed inside the 3 s settle of
# the probe; 25 s is the family's challenge budget and is kept.
CHALLENGE_SETTLE_MS = 25000
CHALLENGE_POLL_MS = 1000

# How long the Scraping Browser's own auto-solve gets on a captcha wall
# before the paid rung is tried. Measured 2026-10-06: `Captcha.solveFailed`
# arrived at 82.8 s, 20.4 s and 20.7 s on three runs and never a success, so
# waiting the full 83 s every time buys nothing; 25 s covers the two fast
# failures and a success that would arrive in the same window.
AUTOSOLVE_BUDGET_MS = 25000

# Readiness anchors per mode — what "the page has painted" means.
# A listing is ready when MORE than one card is present: one match can
# resolve on an unrelated card-shaped node long before the grid paints
# (2scraper family rule). An item page is ready when its hydration payload is in.
# A seller page is ready when the profile block is.
READY_SELECTORS = {
    "listing": '[data-marker="item"][data-item-id]',
    "seller": '[data-marker="profile"]',
}
MIN_MATCHES = {"listing": 2, "seller": 1}
CONTENT_TIMEOUT_MS = {"listing": 20000, "item": 15000, "seller": 15000}


def should_wait(state: str) -> bool:
    return STATE_POLICY.get(state, {}).get("wait", False)


def should_solve(state: str) -> bool:
    return STATE_POLICY.get(state, {}).get("solve", False)


def should_retry(state: str) -> bool:
    return STATE_POLICY.get(state, {}).get("retry", False)


def should_rotate(state: str) -> bool:
    """Whether this state means "try a different address", not "try again"
    (2scraper family rule: a timeout and a refusal want opposite responses)."""
    return STATE_POLICY.get(state, {}).get("rotate", False)


def counts_as_blocked(state: str) -> bool:
    return STATE_POLICY.get(state, {}).get("blocked", False)


def content_timeout_ms(mode: str) -> int:
    return CONTENT_TIMEOUT_MS.get(mode, 20000)
