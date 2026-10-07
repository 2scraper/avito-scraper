"""
captcha_solver.py
-----------------
The paid rung of the ladder: solve avito.ru's firewall captcha through the
2Captcha solver API (https://2captcha.com/api-docs).

What the firewall serves — measured 2026-10-06
----------------------------------------------
`POST /web/5/firewallCaptcha/get` returns the type the server picked:
`{"captcha":{"geeTest":{"type":"geeTest"}}}` on every run measured. The
wall's inline script carries code paths for THREE types — GeeTest, hCaptcha
and Avito's own image captcha — and the server chooses.

    GeeTest v4, slide     implemented — task `GeeTestTask`, `version: 4`,
                          `initParameters.captcha_id`. captcha_id is
                          hard-coded in the wall: 2d9c743cf7d63dbc9db578a608196bcd
    hCaptcha              this repo does not implement it yet (never served
                          in a measured run)
    Avito image captcha   this repo does not implement it yet; it appeared
                          once, as the server's answer to a REJECTED GeeTest
                          token (see below)

These are statements about THIS repo, not about what 2Captcha can solve
(2scraper family rule).

The token is bound to the exit — measured
-----------------------------------------
    GeeTestTaskProxyless             1 run  -> verify answered with a NEW
                                               (image) captcha: rejected
    GeeTestTask, the browser's own   3 runs -> `verified:true`, 50 items
      session-pinned proxy                     (Scraping Browser); 2 of 3
                                               on a local Chromium, 1
                                               ERROR_CAPTCHA_UNSOLVABLE

So the solve MUST go through the same exit the browser uses. `solve_geetest_v4`
refuses to build a proxyless task unless asked to, because paying for a
token the site rejects — and then facing a second captcha — is worse than
reporting the page unsolved (2scraper family rule: detected != paying).

Cost measured: $0.00299 per solve, 10–73 s.

The key travels in the request BODY (`clientKey`), never a URL, so a
`requests` exception cannot print it; error text is redacted anyway.
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional
from urllib.parse import urlparse

import requests

API = "https://api.2captcha.com"

_KEY_IN_TEXT_RE = re.compile(
    r"((?:client)?key|token|api[_-]?key|password)([=:]\s*\"?)([^&\s'\",}]{6,})",
    re.IGNORECASE)
_CREDS_IN_URL_RE = re.compile(r"(\w+://)[^/@\s]+:[^/@\s]+@")


def _redact(text) -> str:
    text = _KEY_IN_TEXT_RE.sub(r"\1\2***", str(text))
    return _CREDS_IN_URL_RE.sub(r"\1***:***@", text)


class SolverError(RuntimeError):
    """The solver could not produce a usable answer. Always a WARNING to the
    caller: the run continues and reports exit 3 if the page stays walled."""


def proxy_task_fields(proxy: Optional[str]) -> dict:
    """The documented proxy fields for a non-proxyless task, or {}.

    `proxyType`, `proxyAddress`, `proxyPort`, `proxyLogin`, `proxyPassword` —
    taken from 2Captcha's documentation, not from this client's vocabulary.
    {} for anything that is not a usable proxy URL.
    """
    if not proxy:
        return {}
    try:
        p = urlparse(proxy)
        port = p.port
    except ValueError:
        return {}
    if not (p.scheme and p.hostname and port):
        return {}
    fields = {"proxyType": p.scheme, "proxyAddress": p.hostname, "proxyPort": port}
    if p.username:
        fields["proxyLogin"] = p.username
    if p.password:
        fields["proxyPassword"] = p.password
    return fields


def geetest_v4_task(captcha_id: str, page_url: str, proxy: Optional[str]) -> dict:
    """The `task` object. Proxyless only when no proxy is given."""
    if not re.fullmatch(r"[0-9a-f]{32}", captcha_id or ""):
        raise SolverError("not a GeeTest v4 captcha_id: %r" % (captcha_id,))
    task = {
        "type": "GeeTestTaskProxyless",
        "websiteURL": page_url,
        "version": 4,
        "initParameters": {"captcha_id": captcha_id},
    }
    fields = proxy_task_fields(proxy)
    if fields:
        task["type"] = "GeeTestTask"
        task.update(fields)
    return task


def solve_geetest_v4(api_key: str, captcha_id: str, page_url: str,
                     proxy: Optional[str] = None, allow_proxyless: bool = False,
                     timeout_s: int = 180, poll_s: float = 5.0,
                     session: Optional[requests.Session] = None) -> dict:
    """Solve and return the solution dict:
    `{captcha_id, lot_number, pass_token, gen_time, captcha_output}`.

    Raises SolverError on every failure, with the key redacted.
    """
    if not api_key:
        raise SolverError("no TWOCAPTCHA_KEY")
    if not proxy_task_fields(proxy) and not allow_proxyless:
        raise SolverError(
            "no proxy for the solver: avito rejects a GeeTest token solved "
            "from a different exit than the browser's (measured), so this "
            "would pay for a token the site refuses. Give the run the same "
            "session-pinned proxy the browser uses (AVITO_PROXY).")
    http = session or requests.Session()
    task = geetest_v4_task(captcha_id, page_url, proxy)
    try:
        created = http.post(API + "/createTask",
                            json={"clientKey": api_key, "task": task},
                            timeout=30).json()
    except Exception as exc:  # noqa: BLE001
        raise SolverError("createTask failed: %s: %s"
                          % (type(exc).__name__, _redact(exc))) from None
    if created.get("errorId"):
        raise SolverError("createTask: %s %s" % (created.get("errorCode"),
                                                 _redact(created.get("errorDescription", ""))))
    task_id = created.get("taskId")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        try:
            got = http.post(API + "/getTaskResult",
                            json={"clientKey": api_key, "taskId": task_id},
                            timeout=30).json()
        except Exception as exc:  # noqa: BLE001
            raise SolverError("getTaskResult failed: %s: %s"
                              % (type(exc).__name__, _redact(exc))) from None
        if got.get("errorId"):
            raise SolverError("getTaskResult: %s" % got.get("errorCode"))
        if got.get("status") == "ready":
            solution = dict(got.get("solution") or {})
            missing = [k for k in ("lot_number", "pass_token", "gen_time", "captcha_output")
                       if not solution.get(k)]
            if missing:
                raise SolverError("solution without %s" % ", ".join(missing))
            solution.setdefault("captcha_id", captcha_id)
            solution["_cost"] = got.get("cost")
            return solution
    raise SolverError("no solution within %ds" % timeout_s)


def firewall_response_value(solution: dict) -> str:
    """What the wall's own code puts into `input[name=captcha-response]`:
    `JSON.stringify(captcha.getValidate())` plus `captcha_id`. The page then
    posts it to `/web/3/firewallCaptcha/verify` itself."""
    keys = ("captcha_id", "lot_number", "pass_token", "gen_time", "captcha_output")
    return json.dumps({k: solution[k] for k in keys if k in solution})


# Submitting through the PAGE'S OWN form rather than calling the verify
# endpoint ourselves: the form handler adds the `X-Cube` header from
# `#cubeResult` and does the reload, exactly as it does for a person.
# Each engine wraps this body in its own dialect (2scraper family rule).
SUBMIT_JS_BODY = (
    "var inp = document.querySelector('input[name=captcha-response]');"
    "var form = document.querySelector('.js-firewall-form');"
    "if (!inp || !form) { return false; }"
    "inp.value = VALUE;"
    "form.dispatchEvent(new Event('submit'));"
    "return true;"
)


def submit_js_body(solution: dict) -> str:
    """The submit snippet with the value inlined as a JSON string literal."""
    return SUBMIT_JS_BODY.replace("VALUE", json.dumps(firewall_response_value(solution)))


def get_balance(api_key: str) -> float:
    try:
        got = requests.post(API + "/getBalance", json={"clientKey": api_key},
                            timeout=20).json()
    except Exception as exc:  # noqa: BLE001
        raise SolverError("getBalance failed: %s" % _redact(exc)) from None
    if got.get("errorId"):
        raise SolverError("getBalance: %s" % got.get("errorCode"))
    return float(got.get("balance", 0))
