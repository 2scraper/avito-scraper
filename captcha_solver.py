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
    Avito image captcha   implemented — `ImageToTextTask` on the picture the
                          wall shows, answer through `#form-input`. Not
                          live-verified: the server never assigned it in a
                          measured run (2026-10-06..09).
    hCaptcha              implemented — `HCaptchaTask` (shape confirmed by
                          2Captcha, 2026-10-09; the public /api-docs/hcaptcha
                          page answered 404 that day), sitekey from the
                          wall's `data-sitekey`, through the browser's own
                          exit, token into `#h-captcha-response`. Not
                          live-verified: never assigned in a measured run.

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
    caller: the run continues and reports exit 3 if the page stays walled.

    Carries the 2Captcha `task_id` and `error_code` when there is one, so a
    failed solve can be traced in the 2Captcha dashboard and support tickets —
    a bare "no solution" was all the first release could say.
    """

    def __init__(self, message, task_id=None, error_code=None):
        super().__init__(message)
        self.task_id = task_id
        self.error_code = error_code


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
                     timeout_s: int = 240, poll_s: float = 5.0,
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
    task = geetest_v4_task(captcha_id, page_url, proxy)
    # 240 s, not 180: measured 2026-10-08, 2Captcha took 217 s and 220 s to
    # give up on this captcha (ERROR_CAPTCHA_UNSOLVABLE). A shorter wait
    # abandons the task before its verdict and reports only "no solution".
    solution, task_id, cost = _run_task(api_key, task, timeout_s, poll_s, session)
    missing = [k for k in ("lot_number", "pass_token", "gen_time", "captcha_output")
               if not solution.get(k)]
    if missing:
        raise SolverError("solution without %s (task %s)" % (", ".join(missing), task_id),
                          task_id=task_id)
    solution.setdefault("captcha_id", captcha_id)
    solution["_cost"] = cost
    solution["_task_id"] = task_id
    return solution


def _run_task(api_key: str, task: dict, timeout_s: float, poll_s: float,
              session: Optional[requests.Session] = None):
    """createTask + poll getTaskResult. Returns (solution, task_id, cost).
    Every failure is a SolverError carrying the task id and 2Captcha's code."""
    if not api_key:
        raise SolverError("no TWOCAPTCHA_KEY")
    http = session or requests.Session()
    try:
        created = http.post(API + "/createTask", json={"clientKey": api_key, "task": task},
                            timeout=30).json()
    except Exception as exc:  # noqa: BLE001
        raise SolverError("createTask failed: %s: %s"
                          % (type(exc).__name__, _redact(exc))) from None
    if created.get("errorId"):
        raise SolverError("createTask: %s %s" % (created.get("errorCode"),
                                                 _redact(created.get("errorDescription", ""))),
                          error_code=created.get("errorCode"))
    task_id = created.get("taskId")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        try:
            got = http.post(API + "/getTaskResult",
                            json={"clientKey": api_key, "taskId": task_id}, timeout=30).json()
        except Exception as exc:  # noqa: BLE001
            raise SolverError("getTaskResult failed: %s: %s"
                              % (type(exc).__name__, _redact(exc)), task_id=task_id) from None
        if got.get("errorId"):
            raise SolverError("getTaskResult: %s (task %s)" % (got.get("errorCode"), task_id),
                              task_id=task_id, error_code=got.get("errorCode"))
        if got.get("status") == "ready":
            return dict(got.get("solution") or {}), task_id, got.get("cost")
    raise SolverError("no solution within %ds (task %s)" % (timeout_s, task_id),
                      task_id=task_id, error_code="TIMEOUT")


def hcaptcha_task(sitekey: str, page_url: str, proxy: Optional[str],
                  rqdata: Optional[str] = None) -> dict:
    """`HCaptchaTask` (API v2). The shape was confirmed by 2Captcha on
    2026-10-09 — its public page /api-docs/hcaptcha answered 404 that day, so
    it is cited from the vendor, not from that page. `enterprisePayload.rqdata`
    only for hCaptcha Enterprise; avito's wall carries no rqdata."""
    if not re.fullmatch(r"[0-9a-f-]{36}", sitekey or ""):
        raise SolverError("not an hCaptcha sitekey: %r" % (sitekey,))
    task = {"type": "HCaptchaTask", "websiteURL": page_url, "websiteKey": sitekey,
            "isInvisible": False}
    task.update(proxy_task_fields(proxy))
    if rqdata:
        task["enterprisePayload"] = {"rqdata": rqdata}
    return task


def solve_hcaptcha(api_key: str, sitekey: str, page_url: str, proxy: Optional[str] = None,
                   rqdata: Optional[str] = None, timeout_s: int = 240, poll_s: float = 5.0,
                   session: Optional[requests.Session] = None) -> dict:
    """Solve the wall's hCaptcha; returns `{token, _cost, _task_id}`.

    Through the browser's own exit when there is one — the same rule GeeTest
    taught here (a token from another IP was rejected). NOT live-verified:
    the server never assigned hCaptcha in a measured run (2026-10-06..09).
    """
    solution, task_id, cost = _run_task(api_key, hcaptcha_task(sitekey, page_url, proxy, rqdata),
                                        timeout_s, poll_s, session)
    token = solution.get("gRecaptchaResponse") or solution.get("token")
    if not token:
        raise SolverError("hCaptcha solution without a token (task %s)" % task_id, task_id=task_id)
    return {"token": token, "_cost": cost, "_task_id": task_id}


SUBMIT_HCAPTCHA_JS_BODY = (
    "var ta = document.querySelector('#h-captcha-response');"
    "var form = document.querySelector('.js-firewall-form');"
    "if (!ta || !form) { return false; }"
    "ta.value = VALUE;"
    "var g = document.querySelector('[name=g-recaptcha-response]'); if (g) { g.value = VALUE; }"
    "form.dispatchEvent(new Event('submit'));"
    "return true;"
)


def submit_hcaptcha_js_body(token: str) -> str:
    """The token into `#h-captcha-response` — the field the wall's submit
    handler reads — then the form's own submit handler."""
    return SUBMIT_HCAPTCHA_JS_BODY.replace("VALUE", json.dumps(token))


def image_task(image_src: str) -> dict:
    """`ImageToTextTask` for the wall's own picture captcha
    (https://2captcha.com/api-docs/normal-captcha). The wall sets the picture
    as a `data:` URL; only that form is accepted here — anything else would
    mean fetching an image outside the browser's session."""
    if not image_src or not image_src.startswith("data:image") or "," not in image_src:
        raise SolverError("image captcha without a data: URL picture")
    return {"type": "ImageToTextTask", "body": image_src.split(",", 1)[1], "case": False}


def solve_image(api_key: str, image_src: str, timeout_s: int = 120, poll_s: float = 4.0,
                session: Optional[requests.Session] = None) -> dict:
    """Solve the wall's picture captcha; returns `{text, _cost, _task_id}`.

    NOT live-verified: every firewall request measured (2026-10-06..09)
    was assigned GeeTest, including the page's own "refresh picture" request.
    The task type and the submit path come from 2Captcha's documentation and
    the wall's own script (`#form-input`, then the form's submit handler).
    No proxy fields: an image task is a picture, not a session.
    """
    solution, task_id, cost = _run_task(api_key, image_task(image_src), timeout_s, poll_s,
                                        session)
    text = (solution.get("text") or "").strip()
    if not text:
        raise SolverError("empty answer (task %s)" % task_id, task_id=task_id)
    return {"text": text, "_cost": cost, "_task_id": task_id}


SUBMIT_TEXT_JS_BODY = (
    "var inp = document.querySelector('#form-input');"
    "var form = document.querySelector('.js-firewall-form');"
    "if (!inp || !form) { return false; }"
    "inp.value = VALUE;"
    "form.dispatchEvent(new Event('submit'));"
    "return true;"
)


def submit_text_js_body(text: str) -> str:
    """The picture-captcha answer into the page's own `#form-input`, then the
    form's own submit handler — the same path a visitor's Enter takes."""
    return SUBMIT_TEXT_JS_BODY.replace("VALUE", json.dumps(text))


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
