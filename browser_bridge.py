"""Everything the three browser engines share, so they cannot drift apart.

The engines differ only in how they start a browser, ask it for a URL, read
the document and run one snippet. Everything that decides what a run DOES —
the firewall ladder, pagination, the terminating condition, the exit codes —
lives here, once.

The firewall ladder on avito.ru (measured 2026-10-06, recon/RECON.md)
---------------------------------------------------------------------
A plain HTTP client never gets past it: `curl` from a Russian residential
exit got HTTP 429 on 6 of 6 requests. A browser meets one of two walls:

    challenge  HTTP 439, a proof-of-work the page runs by itself
               (`/web/3/firewallPow/get` + `/verify`). Rung 0: WAIT.
    captcha    HTTP 429 + «Продолжить» → GeeTest v4 slide.
               Rung 1: the Scraping Browser's own auto-solve
                       (`Captcha.setAutoSolve`). Measured 0 of 3 — it reports
                       `Captcha.detected` then `Captcha.solveFailed`. Kept,
                       because it is free to try and may start working.
               Rung 2: 2Captcha `GeeTestTask` v4 THROUGH THE BROWSER'S OWN
                       EXIT, submitted via the page's own form. Measured 3 of
                       3 on the Scraping Browser, 2 of 3 on a local Chromium.
                       A proxyless solve was rejected (1 of 1), so rung 2
                       refuses to run without the browser's proxy.
               Rung 3: a fresh exit (`--proxy-block-retries`), local
                       browsers only — a Scraping Browser profile brings its
                       own exit and cannot be re-pointed mid-run.

Where the run gets the solver's proxy
-------------------------------------
    local browser   the proxy the browser was launched with. A 2Captcha
                    gateway login WITHOUT `-session-` rotates its exit per
                    request, which would put the solver on a different IP than
                    the browser — so such a login is pinned to one session for
                    the run, and the pinned URL goes to both.
    --cdp-endpoint  the remote browser brings its own exit; AVITO_PROXY must
                    be the exact session-pinned proxy the endpoint was BUILT
                    with (`tools/browser_profile_client.py connection
                    --write-env` writes both). It is never applied to the
                    browser — stacking a second exit is a contradiction.

The driver protocol
-------------------
An engine supplies an object with:
    name               str
    start()            bring a browser up (uses args.proxy for a local one)
    navigate(url)      -> Optional[int]  HTTP status if the engine can see one
    content()          -> str            the rendered document
    count(selector)    -> int
    run_js(body)       -> Any            a FUNCTION BODY with an explicit
                                         `return`; each engine wraps it in its
                                         own dialect (Selenium takes it as is,
                                         Playwright/pyppeteer as `() => {…}`)
    sleep(ms)
    stop()
    autosolve_armed, autosolve_events   rung 1 bookkeeping
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

import env_config
import output_writer
import page_flow
import product_parser as P
import proxy_pool
from output_writer import (EXIT_CRASH, EXIT_REMOTE_API_ERROR, Product, finish_run,
                           scope_fingerprint)

logger = logging.getLogger("avito")

_CREDENTIALS_IN_URL_RE = re.compile(r"(\w+://)[^/@\s]+:[^/@\s]+@")

DEFAULT_OUT = "avito"


def mask_text(text: str) -> str:
    """Redact `user:pass@` inside any URL in free text, keeping the text."""
    return _CREDENTIALS_IN_URL_RE.sub(r"\1***:***@", text or "")


class BridgeError(RuntimeError):
    """The BROWSER could not do its job — it never got an answer from the site.

    A navigation timeout, a dropped CDP socket, `ERR_CONNECTION_RESET` at the
    exit (measured once on a local run), a locked profile, a 407 on the
    tunnel: transport faults, not refusals. They map to exit 5, never 3 —
    exit 3 sends the reader to the anti-bot problem when the answer is "run
    it again" (2scraper family rule).
    """


# ---------------------------------------------------------------------------
# Waiting
# ---------------------------------------------------------------------------

def wait_for_content(driver, mode: str, url: str, timeout_ms: int, log=None) -> str:
    """Wait, bounded, for the page to be ready; return what is there at the end.

    Returns rather than raises on a timeout — the caller classifies the
    document, and "did not paint" and "was refused" want different exits.
    A firewall page stops the wait at once: nothing will paint behind it.
    """
    waited, step = 0, 500
    selector = page_flow.READY_SELECTORS.get(mode)
    need = page_flow.MIN_MATCHES.get(mode, 1)
    while waited < timeout_ms:
        try:
            html = driver.content() or ""
            if P.is_wall(html):
                break
            if mode == "item":
                if P.buyer_item(html) is not None:
                    break
            elif selector and driver.count(selector) >= need:
                break
            elif P.detect_page_state(html, url=url) == "empty":
                break
        except Exception:  # a driver may throw mid-navigation
            pass
        driver.sleep(step)
        waited += step
    html = driver.content() or ""
    if log:
        log("  readiness wait: %dms, %d bytes" % (waited, len(html)))
    return html


def wait_out_challenge(driver, url: str, budget_ms: int, log=None) -> str:
    """Rung 0: let the proof-of-work clear itself. Passive on purpose —
    clicking or reloading restarts it."""
    waited = 0
    html = driver.content() or ""
    while waited < budget_ms:
        driver.sleep(page_flow.CHALLENGE_POLL_MS)
        waited += page_flow.CHALLENGE_POLL_MS
        html = driver.content() or html
        if P.detect_page_state(html, url=url) not in ("challenge",):
            if log:
                log("  firewall proof-of-work cleared after %.0fs" % (waited / 1000.0))
            return html
    if log:
        log("  proof-of-work did not clear within %.0fs" % (budget_ms / 1000.0))
    return html


# ---------------------------------------------------------------------------
# The captcha wall
# ---------------------------------------------------------------------------

def _wait_autosolve(driver, url: str, log=None) -> Optional[str]:
    """Rung 1. Returns fresh HTML if the wall cleared, else None.

    Stops early on `Captcha.solveFailed`: measured, that event is final.
    """
    if not getattr(driver, "autosolve_armed", False):
        return None
    failed_before = sum(1 for e in driver.autosolve_events if e["event"] == "Captcha.solveFailed")
    waited = 0
    while waited < page_flow.AUTOSOLVE_BUDGET_MS:
        driver.sleep(1000)
        waited += 1000
        html = driver.content() or ""
        if not P.is_wall(html):
            if log:
                log("  auto-solve cleared the wall after %ds" % (waited // 1000))
            return html
        failed = sum(1 for e in driver.autosolve_events if e["event"] == "Captcha.solveFailed")
        if failed > failed_before:
            if log:
                log("  auto-solve reported solveFailed after %ds" % (waited // 1000))
            return None
    if log:
        log("  auto-solve did not clear the wall within %ds" % (waited // 1000))
    return None


def solve_wall(driver, args, url: str, html: str, facts: Dict[str, Any], log=None) -> Optional[str]:
    """Rungs 1 and 2 on a captcha wall. Returns fresh HTML if it cleared.

    Every solver failure is a WARNING: the run goes on and reports exit 3 if
    the page stays walled (2scraper family rule).
    """
    if getattr(args, "solve_captcha", "when-blocked") == "never":
        return None
    fresh = _wait_autosolve(driver, url, log=log)
    if fresh is not None:
        facts["walls_cleared_by"].append("autosolve")
        return fresh

    variant = P.wall_variant(html) or "geetest"
    facts.setdefault("wall_variants", []).append(variant)
    key = getattr(args, "twocaptcha_key", None)
    if not key:
        print("[!] firewall captcha (%s) and no TWOCAPTCHA_KEY — continuing without "
              "solving." % variant, file=sys.stderr)
        return None
    import captcha_solver
    t0 = time.monotonic()
    try:
        if variant == "image":
            solution = captcha_solver.solve_image(key, P.wall_image_src(html))
            submit = captcha_solver.submit_text_js_body(solution["text"])
        elif variant == "hcaptcha":
            solution = captcha_solver.solve_hcaptcha(
                key, P.wall_hcaptcha_sitekey(html), url,
                proxy=getattr(args, "solver_proxy", None), rqdata=P.wall_hcaptcha_rqdata(html))
            submit = captcha_solver.submit_hcaptcha_js_body(solution["token"])
        else:
            captcha_id = P.geetest_captcha_id(html)
            solution = captcha_solver.solve_geetest_v4(
                key, captcha_id, url, proxy=getattr(args, "solver_proxy", None))
            submit = captcha_solver.submit_js_body(solution)
    except captcha_solver.SolverError as exc:
        print("[!] solver (%s): %s" % (variant, mask_text(str(exc))), file=sys.stderr)
        facts["solves"].append({"ok": False, "variant": variant,
                                "error": mask_text(str(exc))[:200],
                                "task_id": getattr(exc, "task_id", None),
                                "error_code": getattr(exc, "error_code", None),
                                "seconds": round(time.monotonic() - t0, 1)})
        return None
    record = {"ok": True, "variant": variant, "seconds": round(time.monotonic() - t0, 1),
              "cost": solution.pop("_cost", None), "task_id": solution.pop("_task_id", None),
              "verified": False}
    facts["solves"].append(record)
    if log:
        log("  %s solved in %ss — submitting through the page's own form"
            % ("GeeTest" if variant == "geetest" else "Image captcha", record["seconds"]))
    try:
        submitted = driver.run_js(submit)
    except Exception as exc:  # noqa: BLE001
        print("[!] could not submit the solution: %s" % mask_text(str(exc))[:200],
              file=sys.stderr)
        return None
    if not submitted:
        print("[!] the wall's form was gone before the solution arrived.", file=sys.stderr)
    return _after_submit(driver, url, record, facts)


def _after_submit(driver, url: str, record: Dict[str, Any], facts: Dict[str, Any]) -> Optional[str]:
    """What the site served after a submitted solution.

    `verified` means the PAGE came back — cards, an item, a profile, or an
    honest empty listing. "No longer the wall" is not enough: the page passes
    through an intermediate document while it reloads. And the wait-wall
    («подождите немного и обновите страницу») is what the site serves after
    a REJECTED solution: measured 2026-10-09, three deliberately wrong GeeTest
    submissions were each answered `verified: false` and then that wall.
    """
    last = "captcha"
    fresh = ""
    for _ in range(20):
        driver.sleep(1500)
        fresh = driver.content() or ""
        if not fresh:
            continue
        last = P.detect_page_state(fresh, url=url)
        if last in ("content", "empty"):
            record["verified"] = True
            facts["walls_cleared_by"].append("solver")
            return fresh
        if last == "blocked":
            break
    record["after_submit"] = last
    print("[!] after the solved captcha the site served %s, not the page%s."
          % (last, " — the solution was rejected; rotating" if last == "blocked" else ""),
          file=sys.stderr)
    return fresh if last == "blocked" else None


# ---------------------------------------------------------------------------
# One page
# ---------------------------------------------------------------------------

def fetch_page(driver, args, url: str, mode: str, facts: Dict[str, Any], log=None) -> Dict[str, Any]:
    """One page through a browser, with the ladder applied.

    Returns `{"html", "state", "status"}`.
    """
    status = None
    attempts = 1 + (getattr(args, "retries", 0) or 0)
    for attempt in range(1, attempts + 1):
        try:
            status = driver.navigate(url)
            break
        except Exception as exc:  # noqa: BLE001
            # A transport fault: same exit, another try (2scraper family rule — a
            # timeout and a refusal want opposite responses).
            message = mask_text(str(exc)).splitlines()[0][:200] if str(exc) else ""
            if attempt == attempts:
                raise BridgeError("navigation failed after %d attempt(s): %s: %s"
                                  % (attempts, type(exc).__name__, message)) from None
            code = re.search(r"(?:net::)?ERR_[A-Z_]+|Timeout \d+ms exceeded", str(exc))
            print("[i] navigation failed (%s%s) — retry %d/%d in %.0fs"
                  % (type(exc).__name__, ": " + code.group(0) if code else "",
                     attempt, attempts - 1, args.retry_delay), file=sys.stderr)
            time.sleep(args.retry_delay)

    html = wait_for_content(driver, mode, url, page_flow.content_timeout_ms(mode), log=log)
    state = P.detect_page_state(html, status=status, url=url)
    if state in ("challenge", "captcha"):
        facts["walls"].append({"url": url, "state": state, "status": status})

    if state == "challenge":
        if log:
            log("  firewall proof-of-work (HTTP %s) — letting the browser clear it" % status)
        html = wait_out_challenge(driver, url, page_flow.CHALLENGE_SETTLE_MS, log=log)
        state = P.detect_page_state(html, url=url)

    if state == "captcha":
        if log:
            log("  firewall captcha (HTTP %s)" % status)
        fresh = solve_wall(driver, args, url, html, facts, log=log)
        if fresh is not None:
            html = wait_for_content(driver, mode, url, page_flow.content_timeout_ms(mode), log=log)
            state = P.detect_page_state(html, url=url)
            if state == "challenge":
                # A proof-of-work after the solve clears itself like any other.
                html = wait_out_challenge(driver, url, page_flow.CHALLENGE_SETTLE_MS, log=log)
                state = P.detect_page_state(html, url=url)

    if state == "unknown":
        if log:
            log("  nothing recognisable yet — one more wait")
        html = wait_for_content(driver, mode, url, page_flow.content_timeout_ms(mode), log=log)
        state = P.detect_page_state(html, url=url)

    return {"html": html, "state": state, "status": status}


def fetch_with_rotation(driver, args, url: str, mode: str, facts, log=None) -> Dict[str, Any]:
    """`fetch_page`, plus rung 3: a fresh exit for a page that stayed walled.

    A rotation is a FRESH BROWSER (2scraper family rule): cookies a firewall issued to
    exit A replayed from exit B are a stronger signal than either alone.
    """
    retries = getattr(args, "proxy_block_retries", 0) or 0
    while True:
        try:
            result = fetch_page(driver, args, url, mode, facts, log=log)
        except BridgeError as exc:
            # A DEAD EXIT wants a different exit, not another try at the same
            # one (2scraper family rule). Measured 2026-10-06: a session-pinned
            # residential exit went away inside its sessTime and every
            # request through it failed with ERR_CONNECTION_CLOSED /
            # SSL_ERROR_SYSCALL, while a fresh session on the same credential
            # answered at once.
            if retries <= 0 or not _is_exit_failure(str(exc)) or not _fresh_exit_available(args):
                raise
            code = _EXIT_FAILURE_RE.search(str(exc))
            reason = "the exit failed (%s)" % (code.group(0) if code else type(exc).__name__)
        else:
            if not page_flow.should_rotate(result["state"]) or retries <= 0 \
                    or not _fresh_exit_available(args):
                return result
            reason = result["state"]
        retries -= 1
        fresh_proxy = _fresh_exit(args)
        facts["rotations"] += 1
        print("[i] %s on %s — new browser on a fresh exit (%s)"
              % (reason, url, proxy_pool.mask(fresh_proxy)), file=sys.stderr)
        try:
            driver.stop()
        except Exception:  # noqa: BLE001
            pass
        args.proxy = args.solver_proxy = fresh_proxy
        try:
            driver.start()
        except Exception as exc:  # noqa: BLE001
            raise BridgeError("could not restart %s: %s"
                              % (driver.name, mask_text(str(exc))[:200])) from None


# Chromium's names for "the proxy / exit is gone", as opposed to a slow page.
_EXIT_FAILURE_RE = re.compile(
    r"ERR_(?:PROXY_\w+|TUNNEL_CONNECTION_FAILED|CONNECTION_(?:CLOSED|RESET|REFUSED)"
    r"|EMPTY_RESPONSE|SOCKS_\w+|SSL_PROTOCOL_ERROR)")


def _is_exit_failure(message: str) -> bool:
    return bool(_EXIT_FAILURE_RE.search(message or ""))


def _fresh_exit_available(args) -> bool:
    if getattr(args, "cdp_endpoint", None):
        return False
    pool = getattr(args, "pool", None)
    if pool is not None and len(pool) > 1:
        return True
    return proxy_pool.is_2captcha_gateway(getattr(args, "proxy", None))


def _fresh_exit(args) -> Optional[str]:
    """The next exit for a local browser, or None.

    A pool of several exits (`--proxy-file`, `--proxy-sessions`) advances; a
    single 2Captcha gateway credential mints a fresh session. A Scraping
    Browser profile brings its own exit and is never re-pointed mid-run.
    """
    if getattr(args, "cdp_endpoint", None):
        return None
    pool = getattr(args, "pool", None)
    if pool is not None and len(pool) > 1:
        return pool.advance("page stayed walled")
    current = getattr(args, "proxy", None)
    if not current or not proxy_pool.is_2captcha_gateway(current):
        return None
    return proxy_pool.mint_sessions(current, 1)[0]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def _new_facts(driver, args) -> Dict[str, Any]:
    return {
        "transport": driver.name,
        "over_cdp": bool(getattr(args, "cdp_endpoint", None)),
        "pages_requested": args.pages,
        "pages_completed": 0,
        "failed_pages": [],
        "states": [],
        "walls": [],
        "walls_cleared_by": [],
        "solves": [],
        "rotations": 0,
        "blocked": False,
    }


def _input_urls(args) -> List[str]:
    # `--url` is repeatable; AVITO_URL from .env arrives as one string.
    urls = [args.url] if isinstance(args.url, str) else list(args.url or [])
    if getattr(args, "from_file", None):
        with open(args.from_file, encoding="utf-8") as handle:
            data = json.load(handle)
        for row in data if isinstance(data, list) else []:
            if isinstance(row, dict) and row.get("url"):
                urls.append(row["url"])
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _check_urls(urls: List[str], mode: str) -> None:
    if not urls:
        raise SystemExit("nothing to fetch: pass --url (repeatable) or --from FILE.json")
    for u in urls:
        ok, reason = P.supported_host(u)
        if not ok:
            raise SystemExit("refusing %s: %s" % (u, reason))
        allowed, rule = P.robots_verdict(u)
        if not allowed:
            raise SystemExit("refusing %s: avito.ru's robots.txt says %s. Narrow a "
                             "listing by its PATH (city / category), not by "
                             "sort or filter parameters." % (u, rule))
        kind = P.page_kind(u)
        if kind != mode:
            raise SystemExit("refusing %s: that is %s page, not a %s page — use "
                             "--mode %s" % (u, "an item" if kind == "item" else "a " + kind,
                                            mode, kind))


def run(args, driver) -> int:
    """Drive one browser engine end to end and return its exit code."""
    log = (lambda m: print(m, file=sys.stderr)) if getattr(args, "verbose", False) \
        else (lambda m: None)
    urls = _input_urls(args)
    _check_urls(urls, args.mode)
    if args.mode != "item" and len(urls) > 1:
        raise SystemExit("--mode %s takes one --url per run" % args.mode)

    facts = _new_facts(driver, args)
    rows: List[Any] = []
    extra_rows: List[Product] = []
    seen: set = set()
    start_url = urls[0]

    pages_requested = args.pages if args.mode == "listing" else len(urls)

    def failed(code: int, exc: BaseException, stop_reason: str) -> int:
        """EVERY ending writes the attempt sidecar — a run that died before
        finish_run used to leave the previous attempt's metadata in place, so
        a scheduler read yesterday's `complete` as today's result."""
        facts["error_class"] = type(exc).__name__
        facts["error_message"] = mask_text(str(exc))[:500]
        meta = output_writer.run_meta(
            status="failed", stop_reason=stop_reason, pages_requested=pages_requested,
            pages_completed=facts["pages_completed"], start_url=start_url,
            final_url=facts.get("final_url", start_url), products=0,
            pages_failed=facts["failed_pages"], mode=args.mode,
            scope=scope_fingerprint(start_url, args.mode, pages_requested, args.max_products))
        meta.update(run_id=run_id, started_at=started_at, blocked=False,
                    exit_code=code, data_updated=False, transport_facts=facts)
        try:
            output_writer.write_attempt_meta(args.out, meta)
        except OSError as write_exc:
            print("[!] could not write the attempt metadata: %s" % write_exc, file=sys.stderr)
        return code

    import uuid
    from datetime import datetime, timezone
    run_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        driver.start()
    except Exception as exc:  # noqa: BLE001 — a browser that never started is transport
        print("could not start %s: %s: %s"
              % (driver.name, type(exc).__name__, mask_text(str(exc))[:300]), file=sys.stderr)
        _stop(driver)
        return failed(EXIT_REMOTE_API_ERROR, exc, "transport_error")

    try:
        if args.mode == "listing":
            rows = _run_listing(driver, args, start_url, facts, seen, log)
        elif args.mode == "item":
            rows = _run_items(driver, args, urls, facts, log)
        else:
            rows, extra_rows = _run_seller(driver, args, start_url, facts, log)
    except BridgeError as exc:
        # The site was never reached: exit 5 (2scraper family rule).
        print("[!] %s" % exc, file=sys.stderr)
        _stop(driver)
        return failed(EXIT_REMOTE_API_ERROR, exc, "transport_error")
    except Exception as exc:  # noqa: BLE001
        # NOT a transport fault: a parser bug, a TypeError, a file error. It
        # used to be reported as exit 5, indistinguishable from a dead exit,
        # with the traceback thrown away. Exit 1, traceback kept (masked).
        import traceback
        print(mask_text(traceback.format_exc()), file=sys.stderr)
        _stop(driver)
        return failed(EXIT_CRASH, exc, "crash")
    finally:
        _stop(driver)

    facts["autosolve_armed"] = getattr(driver, "autosolve_armed", False)
    facts["autosolve_events"] = getattr(driver, "autosolve_events", [])
    facts["solver_cost"] = round(sum(s.get("cost") or 0 for s in facts["solves"]
                                     if isinstance(s.get("cost"), (int, float))), 5)

    if extra_rows:
        output_writer.save(extra_rows, args.out + "_listings", args.format,
                           allow_empty=False, row_cls=Product)

    return finish_run(
        rows, args.out, args.format, args.allow_empty,
        blocked=facts["blocked"],
        stop_reason=_stop_reason(args, facts, pages_requested),
        pages_requested=pages_requested,
        pages_completed=facts["pages_completed"],
        pages_failed=facts["failed_pages"],
        mode=args.mode,
        start_url=start_url, final_url=facts.get("final_url", start_url),
        scope=scope_fingerprint(start_url, args.mode, pages_requested, args.max_products),
        extra=facts,
    )


def _stop(driver) -> None:
    try:
        driver.stop()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s did not shut down cleanly: %s", driver.name, mask_text(str(exc)))


def _run_listing(driver, args, url, facts, seen, log) -> List[Product]:
    rows: List[Product] = []
    last_page: Optional[int] = None
    for page in range(1, args.pages + 1):
        if last_page is not None and page > last_page:
            log("  the site's pager ends at page %d." % last_page)
            facts["pagination_exhausted"] = True
            break
        target = P.page_url(url, page)
        log("page %d: %s" % (page, target))
        result = fetch_with_rotation(driver, args, target, "listing", facts, log=log)
        state = result["state"]
        facts["states"].append(state)
        facts["final_url"] = target
        _dump(args, result["html"], page)

        if page_flow.counts_as_blocked(state):
            facts["blocked"] = True
            facts["failed_pages"].append(page)
            print("page %d: %s (HTTP %s, %d bytes)"
                  % (page, state, result["status"], len(result["html"])), file=sys.stderr)
            break
        if state == "empty":
            log("  page %d is a served listing with no cards on it." % page)
            facts["pages_completed"] = page
            facts["pagination_exhausted"] = True
            break
        if page == 1:
            last_page = P.total_pages(result["html"])
            facts["total_pages_reported"] = last_page

        page_rows = P.parse_listing(result["html"], url=target, category=args.category)
        for row in page_rows:
            row.page = page
        fresh = output_writer.dedupe_by_sku(page_rows, seen)
        rows.extend(fresh)
        facts["pages_completed"] = page
        log("  %s: %d rows (%d new, %d total)" % (state, len(page_rows), len(fresh), len(rows)))

        # The terminating condition is DATA, never a selector (2scraper family rule).
        if not page_rows or not fresh:
            log("  page %d added no new items — end of listing." % page)
            facts["pagination_exhausted"] = True
            break
        if args.max_products and len(rows) >= args.max_products:
            rows = rows[:args.max_products]
            facts["max_products_reached"] = True
            break
        if page < args.pages:
            time.sleep(args.delay)
    return rows


def _run_items(driver, args, urls, facts, log) -> List[Any]:
    rows: List[Any] = []
    for i, url in enumerate(urls, 1):
        if args.max_products and len(rows) >= args.max_products:
            facts["max_products_reached"] = True
            break
        log("item %d/%d: %s" % (i, len(urls), url))
        result = fetch_with_rotation(driver, args, url, "item", facts, log=log)
        facts["states"].append(result["state"])
        _dump(args, result["html"], i)
        if page_flow.counts_as_blocked(result["state"]):
            facts["blocked"] = True
            facts["failed_pages"].append(i)
            print("item %d: %s (HTTP %s) — stopping; the remaining items would "
                  "meet the same wall" % (i, result["state"], result["status"]), file=sys.stderr)
            break
        item = P.parse_item(result["html"], url=url, category=args.category)
        if item is None:
            facts["failed_pages"].append(i)
            print("item %d: no item data on %s (state %s) — closed or removed?"
                  % (i, url, result["state"]), file=sys.stderr)
        else:
            item.page = i
            rows.append(item)
        facts["pages_completed"] = i
        if i < len(urls):
            time.sleep(args.delay)
    return rows


def _run_seller(driver, args, url, facts, log):
    result = fetch_with_rotation(driver, args, url, "seller", facts, log=log)
    facts["states"].append(result["state"])
    _dump(args, result["html"], 1)
    if page_flow.counts_as_blocked(result["state"]):
        facts["blocked"] = True
        facts["failed_pages"].append(1)
        return [], []
    seller, listings = P.parse_seller(result["html"], url=url)
    if seller is None:
        facts["failed_pages"].append(1)
        return [], []
    facts["pages_completed"] = 1
    want = seller.active_ads or 0
    if args.max_products:
        want = min(want, args.max_products) if want else args.max_products
    if want > len(listings):
        more = _seller_feed(driver, args, url, result["html"], want, facts, log)
        if more is not None:
            listings = more
    if args.max_products:
        listings = listings[:args.max_products]
    if want and len(listings) < want:
        # 2026-10-10: a shop stating 7331 active listings came back as a
        # `complete` run holding 12. Fewer than the profile's own counter is
        # a partial view, and the run says so (exit 6).
        facts["seller_listings_short"] = {"got": len(listings), "want": want}
    seller.listings_on_page = len(listings)
    for i, row in enumerate(listings, 1):
        row.page, row.position = 1, i
    return [seller], listings


# Scrolling the feed: a round adds 15 cards when the site answers (measured
# 2026-10-09: 15 -> 195 in 12 rounds, ~2.7 s each). Three rounds with neither
# a new card nor a taller page mean the feed is exhausted (family rule: the
# terminating condition is data, and one still round is not enough).
SELLER_SCROLL_PAUSE_MS = 2500
SELLER_SCROLL_STILL_ROUNDS = 3
SELLER_SCROLL_MAX_ROUNDS = 400
SELLER_SHOW_ALL_SETTLE_MS = 3000
SELLER_SHOW_ALL_MAX_CLICKS = 3


def _seller_feed(driver, args, profile_url, profile_html, want, facts, log):
    """All of a seller's listings, from the profile's full feed.

    DECISION (owner, 2026-10-09): the profile page renders 15 listings; the
    rest only arrive when the feed `/brands/<slug>/all?sellerId=…` is
    scrolled, and the page's own script then fetches them from
    `/web/1/profile/items`, a path avito.ru's robots.txt disallows. The scraper
    never requests that path itself — it scrolls, and the site's page does —
    but the purpose of the scroll is that data. README says so plainly.

    Returns the parsed rows, or None (keep the profile's 15) when the feed
    cannot be read.
    """
    seller_hash = P.seller_hash_id(profile_html)
    feed = {"want": want, "rounds": 0, "cards": 0, "stop": None}
    facts["seller_feed"] = feed
    if not seller_hash:
        # 2026-10-10, /brands/a3boots.shop: a shop's profile carries no
        # sellerId at all; its `/brands/<slug>/items` page (robots: allowed)
        # does, and the same `/all?sellerId=` feed then works for it.
        items_url = P.seller_items_url(profile_url)
        if P.robots_verdict(items_url)[0]:
            time.sleep(args.delay)
            found = fetch_with_rotation(driver, args, items_url, "seller", facts, log=log)
            if found["state"] == "content":
                seller_hash = P.seller_hash_id(found["html"])
                feed["sellerId_from"] = "items page" if seller_hash else None
    if not seller_hash:
        feed["stop"] = "no sellerId on the profile"
        print("[!] no sellerId on the profile — keeping its first listings only.", file=sys.stderr)
        return None
    feed_url = P.seller_all_url(profile_url, seller_hash)
    feed["url"] = feed_url
    allowed, rule = P.robots_verdict(feed_url)
    if not allowed:
        feed["stop"] = "robots: %s" % rule
        return None
    time.sleep(args.delay)
    result = fetch_with_rotation(driver, args, feed_url, "seller", facts, log=log)
    if result["state"] != "content":
        feed["stop"] = "feed page: %s" % result["state"]
        print("[!] the seller's feed came back %s — keeping the profile's listings only."
              % result["state"], file=sys.stderr)
        return None
    # The page's script has to be up before the button does anything: a
    # click straight after the HTML arrived (the cards are server-rendered,
    # so readiness is immediate) did nothing on 2026-10-09; the same click
    # 4 s later armed the feed. Wait, click, and click again if the feed
    # does not move.
    driver.sleep(SELLER_SHOW_ALL_SETTLE_MS)
    feed["show_all_clicks"] = 0

    def click_show_all():
        try:
            clicked = bool(driver.run_js(P.SELLER_SHOW_ALL_JS_BODY))
        except Exception as exc:  # noqa: BLE001
            feed["show_all_error"] = type(exc).__name__
            return False
        if clicked:
            feed["show_all_clicks"] += 1
            driver.sleep(SELLER_SCROLL_PAUSE_MS)
        return clicked

    click_show_all()
    count = driver.count(P.SELLER_CARD_SELECTOR)
    still, last = 0, (count, None)
    while count < want and feed["rounds"] < SELLER_SCROLL_MAX_ROUNDS:
        try:
            height = driver.scroll_to_bottom()
        except Exception as exc:  # noqa: BLE001
            feed["stop"] = "scroll failed: %s" % type(exc).__name__
            break
        driver.sleep(SELLER_SCROLL_PAUSE_MS)
        feed["rounds"] += 1
        count = driver.count(P.SELLER_CARD_SELECTOR)
        if (count, height) == last:
            still += 1
            if still == 2 and feed["show_all_clicks"] < SELLER_SHOW_ALL_MAX_CLICKS:
                click_show_all()
            if still >= SELLER_SCROLL_STILL_ROUNDS:
                feed["stop"] = "feed stopped growing"
                break
        else:
            still = 0
        last = (count, height)
        if log and feed["rounds"] % 5 == 0:
            log("  seller feed: %d of %d listings after %d scrolls" % (count, want, feed["rounds"]))
    html = driver.content() or ""
    if P.is_wall(html):
        # The feed can meet the firewall mid-scroll; what is on screen is gone.
        feed["stop"] = "the firewall interrupted the feed"
        print("[!] the firewall interrupted the seller feed — keeping the profile's listings only.",
              file=sys.stderr)
        return None
    _, rows = P.parse_seller(html, url=profile_url)
    rows = output_writer.dedupe_by_sku(rows, set())
    feed["cards"] = len(rows)
    feed["stop"] = feed["stop"] or ("reached %d" % want if len(rows) >= want else "feed ended")
    if log:
        log("  seller feed: %d listings (%s)" % (len(rows), feed["stop"]))
    return rows if rows else None


def _stop_reason(args, facts: Dict[str, Any], pages_requested: int) -> str:
    if facts["blocked"]:
        return "blocked"
    if args.mode == "seller":
        if not facts["pages_completed"]:
            return "no_profile"
        return "seller_listings_short" if facts.get("seller_listings_short") else "single_page_mode"
    if facts["failed_pages"]:
        return "pages_failed"
    if facts.get("max_products_reached"):
        return "completed"
    if args.mode == "item":
        return "completed" if facts["pages_completed"] >= pages_requested else "pages_failed"
    if facts.get("pagination_exhausted"):
        return "pagination_exhausted"
    if facts["pages_completed"] >= args.pages:
        return "completed"
    return "no_new_products"


def _dump(args, html: str, page: int) -> None:
    """`--dump-html` writes on success too: a run can return the right count
    with a field silently empty, and then the bytes are the only evidence."""
    if not getattr(args, "dump_html", None):
        return
    root, ext = os.path.splitext(args.dump_html)
    path = "%s_p%d%s" % (root, page, ext or ".html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html or "")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CLI, defined once for all three engines
# ---------------------------------------------------------------------------

def _positive_int(value):
    import argparse
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1, got %s" % value)
    return number


def _non_negative_int(value):
    import argparse
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be >= 0, got %s" % value)
    return number


def _non_negative_float(value):
    import argparse
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be >= 0, got %s" % value)
    return number


def build_parser(description: str):
    """Every flag the browser engines share, defined ONCE.

    Not here, deliberately: `--min-score` and `--captcha-api` (reCAPTCHA-only
    knobs in sibling repos — avito's firewall serves GeeTest v4, which has
    neither), and `--concurrency` above 1 (see parse_args).
    """
    import argparse
    p = argparse.ArgumentParser(
        description=description,
        epilog="Credentials belong in .env, never on a command line — `ps` "
               "reads argv and argv lands in shell history.")
    p.add_argument("--url", action="append", default=None,
                   help="A www.avito.ru URL. Repeatable in --mode item.")
    p.add_argument("--from", dest="from_file", default=None,
                   help="--mode item: a JSON file of rows with `url` (e.g. a "
                        "listing run's output).")
    p.add_argument("--mode", choices=["listing", "item", "seller"], default="listing")
    p.add_argument("--category", default=None,
                   help="Label written into every row's `category`.")
    p.add_argument("--pages", type=_positive_int, default=1)
    p.add_argument("--max-products", type=_positive_int, default=None)
    p.add_argument("--delay", type=_non_negative_float, default=2.0)
    p.add_argument("--retries", type=_non_negative_int, default=1)
    p.add_argument("--retry-delay", type=_non_negative_float, default=5.0)
    p.add_argument("--concurrency", type=_positive_int, default=1)
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--allow-empty", action="store_true")
    p.add_argument("--dump-html", default=None)
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--headful", dest="headless", action="store_false")
    p.add_argument("--timeout", type=_positive_int, default=60)
    p.add_argument("--cdp-endpoint", default=None,
                   help="Connect to the Scraping Browser API instead of launching "
                        "a browser. Falls back to AVITO_CDP_ENDPOINT in .env — "
                        "never pass it on a command line, it holds a password.")
    p.add_argument("--proxy", default=None,
                   help="Prefer AVITO_PROXY in .env. A local browser's exit; with "
                        "--cdp-endpoint, the exit the endpoint was built with, "
                        "used ONLY by the captcha solver.")
    p.add_argument("--proxy-file", default=None)
    p.add_argument("--proxy-sessions", type=_positive_int, default=None)
    p.add_argument("--proxy-rotate", choices=proxy_pool.ROTATE_MODES, default="per-run")
    p.add_argument("--proxy-shuffle", action="store_true")
    p.add_argument("--proxy-block-retries", type=_non_negative_int, default=1,
                   help="Local browsers on a 2Captcha gateway: fresh exits to try "
                        "for a page that stays walled (default 1).")
    p.add_argument("--twocaptcha-key", default=None,
                   help="Prefer TWOCAPTCHA_KEY in .env — `ps` reads argv.")
    p.add_argument("--solve-captcha", choices=["when-blocked", "never"],
                   default="when-blocked")
    return p


def parse_args(parser, argv=None):
    args = parser.parse_args(argv)
    env_config.apply(args)
    if args.concurrency > 1:
        parser.error(
            "--concurrency above 1 is refused on avito.ru: its firewall is a "
            "request-RATE decision («С него идёт очень много запросов»), so "
            "parallel fetches from one exit raise the wall rate, and the "
            "Scraping Browser allows one connection per profile. Run several "
            "processes on different exits instead.")
    if args.from_file and args.mode != "item":
        parser.error("--from is for --mode item")

    if args.cdp_endpoint:
        # Never applied to the remote browser — it brings its own exit.
        args.solver_proxy = args.proxy
        args.proxy = None
        if not args.solver_proxy:
            print("[i] no AVITO_PROXY: if the firewall serves a captcha, the "
                  "solver rung is skipped (a token solved from another exit is "
                  "rejected — measured).", file=sys.stderr)
        elif "-session-" not in args.solver_proxy:
            print("[!] AVITO_PROXY has no -session- pin: it cannot be the exit "
                  "the CDP endpoint was built with, and a solve through it will "
                  "be rejected. Rebuild both with tools/browser_profile_client.py "
                  "connection --write-env.", file=sys.stderr)
        args.pool = None
    else:
        if args.proxy and proxy_pool.is_2captcha_gateway(args.proxy) \
                and "-session-" not in args.proxy and not args.proxy_sessions:
            # A bare gateway login rotates per request; the solver must share
            # the browser's exit, so pin one session for the run.
            args.proxy = proxy_pool.mint_sessions(args.proxy, 1)[0]
        try:
            args.pool = proxy_pool.from_args(args)
        except proxy_pool.ProxyError as exc:
            parser.error(str(exc))
        if args.pool is not None:
            # The engines launch with `args.proxy`; the pool decides which.
            args.proxy = args.pool.current
        args.solver_proxy = args.proxy
    return args
