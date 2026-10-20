#!/usr/bin/env python3
"""avito-scraper — the offline suite.

One file of plain functions with fixtures cut from real captures
(`fixtures/`, trimmed and scrubbed — see fixtures/README.md). No pytest, no
conftest. `tests/test_smoke.py` wraps this as a single pytest test so `pytest`
works as an entry point without a second copy of the checks.

It must pass with NO engine library installed at all: every
`import playwright_scraper` / `selenium_scraper` / `puppeteer_scraper` is
guarded and records a skip. For that to mean anything each engine imports its
driver at MODULE level, which is asserted below by walking the AST.

    python3 smoke_test.py
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import csv
import dataclasses
import inspect
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, HERE)

PASSES: List[str] = []
FAILURES: List[str] = []
SKIPS: List[str] = []

LISTING = "https://www.avito.ru/moskva/telefony/mobilnye_telefony/apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc"
ITEM_COMPANY = "https://www.avito.ru/moskva/telefony/iphone_17_256_gb_sim_esim_8245354778"
ITEM_PRIVATE = "https://www.avito.ru/moskva/telefony/iphone_14_pro_max_128_gb_sim_esim_8384126179"
SELLER = "https://www.avito.ru/brands/seller1"

# Assembled from parts so the repo's own secret scanners do not read it as a
# committed credential. They are right to refuse one.
PLACEHOLDER_PROXY = "http://" + "EXAMPLE-USER" + ":" + "EXAMPLE-PASS" + "@proxy.invalid:2334"
GATEWAY_PROXY = ("http://" + "uEXAMPLE-zone-custom-region-RU" + ":" + "EXAMPLE-PASS"
                 + "@eu.proxy.rucaptcha.com:2334")


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
        print("PASS %s" % name)
    else:
        FAILURES.append("%s %s" % (name, detail))
        print("FAIL %s %s" % (name, detail))


def eq(name, got, want):
    check(name, got == want, "(got %r, want %r)" % (got, want))


def skip(label):
    SKIPS.append(label)
    print("SKIP %s" % label)


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Page states
# ---------------------------------------------------------------------------

def test_page_states():
    import product_parser as P
    cases = [
        ("listing_dom.html", LISTING + "?p=2", "content"),
        ("listing_state.html", LISTING, "content"),
        ("listing_empty.html", "https://www.avito.ru/moskva?q=zqxjvzqxjvzqxjv", "empty"),
        ("item_company.html", ITEM_COMPANY, "content"),
        ("item_private.html", ITEM_PRIVATE, "content"),
        ("seller.html", SELLER, "content"),
        ("wall_captcha.html", LISTING, "captcha"),
    ]
    for name, url, want in cases:
        # WITHOUT a status: the Selenium case, where markup is all there is.
        eq("state(%s)" % name, P.detect_page_state(fixture(name), url=url), want)
    eq("the wall is a captcha with its 429 too",
       P.detect_page_state(fixture("wall_captcha.html"), status=429, url=LISTING), "captcha")


def test_a_wall_without_a_captcha_form_is_a_challenge_to_wait_out():
    """The proof-of-work variant (HTTP 439) clears itself in a browser. No
    capture of it was kept — the document is DERIVED from the real wall by
    removing its captcha form, which is exactly the difference the classifier
    keys on."""
    import product_parser as P
    wall = fixture("wall_captcha.html")
    derived = re.sub(r'<form[^>]*js-firewall-form.*?</form>', "", wall, flags=re.S)
    check("derivation removed the form element", "<form" not in derived)
    eq("wall without a captcha form -> challenge",
       P.detect_page_state(derived, url=LISTING), "challenge")


def test_a_wall_is_never_content_even_with_cards_in_it():
    import product_parser as P
    poisoned = fixture("wall_captcha.html").replace(
        "</body>", '<div data-marker="item" data-item-id="1"></div></body>')
    eq("wall title wins over a stray card", P.detect_page_state(poisoned, url=LISTING), "captcha")


def test_extension_markers_are_not_a_captcha():
    """The Scraping Browser's extension injects `.../captcha/...` scripts into
    every page it loads (2scraper family rule)."""
    import product_parser as P
    html = fixture("listing_dom.html").replace(
        "</head>", '<script src="chrome-extension://kjmk/content/captcha/geetest/interceptor.js">'
                   '</script></head>')
    eq("a served listing with injected hunters is content",
       P.detect_page_state(html, url=LISTING), "content")


def test_substitute_cards_are_not_search_results():
    """Measured: a search with no matches is served as HTTP 200 with fifty
    cards under «Похожие объявления…». Read as results they make a complete-
    looking run about the wrong question."""
    import product_parser as P
    html = fixture("listing_empty.html")
    check("the fixture does carry cards", html.count('data-marker="item"') >= 2)
    eq("but none of them are results", P.parse_listing(html, url=LISTING), [])


# ---------------------------------------------------------------------------
# 2. Listing values
# ---------------------------------------------------------------------------

def test_listing_dom_values():
    import product_parser as P
    rows = P.parse_listing(fixture("listing_dom.html"), url=LISTING + "?p=2")
    eq("4 rows", len(rows), 4)
    eq("ids in page order", [r.sku for r in rows],
       ["8199454701", "8275958708", "8354606253", "8349145903"])
    eq("positions 1..4", [r.position for r in rows], [1, 2, 3, 4])
    eq("displayed prices", [r.price for r in rows], [18290.0, 144990.0, 39990.0, 109990.0])
    check("currency RUB on every row", all(r.currency == "RUB" for r in rows))
    check("source dom", all(r.price_source == "dom" for r in rows))
    check("urls are canonical (no tracking query)",
          all(r.url.startswith("https://www.avito.ru/moskva/") and "?" not in r.url for r in rows))
    check("region_slug on every row", all(r.region_slug == "moskva" for r in rows))
    eq("review counts read BEFORE the word", [r.seller_reviews for r in rows],
       [1524, 2659, 73, 1393])
    eq("ratings", [r.seller_rating for r in rows], [5.0, 5.0, 4.5, 5.0])
    eq("seller type not invented from a bare link", {r.seller_type for r in rows}, {None})
    check("seller names present (scrubbed placeholders)",
          all((r.seller_name or "").startswith("Продавец") for r in rows))
    eq("summary params", rows[0].summary_params, "Б/у")
    check("promoted is a bool", all(isinstance(r.promoted, bool) for r in rows))
    check("no old price invented in the DOM rendering",
          all(r.original_price is None and r.discount_pct is None for r in rows))


def test_listing_state_values():
    import product_parser as P
    rows = {r.sku: r for r in P.parse_listing(fixture("listing_state.html"), url=LISTING)}
    eq("3 rows", sorted(rows), ["7924194781", "8245354778", "8384126179"])
    disc = rows["8245354778"]
    eq("DISPLAYED price, not the pre-discount field", disc.price, 62990.0)
    eq("old price is the site's own", disc.original_price, 69989.0)
    eq("discount is the site's own percent", disc.discount_pct, 10.0)
    eq("labelled company", disc.seller_type, "company")
    eq("published_at from the timestamp", disc.published_at, "2026-09-09T09:57:42+00:00")
    eq("city", disc.city, "Москва")
    eq("source state", disc.price_source, "state")
    check("delivery is a bool here", isinstance(disc.delivery, bool))
    private = rows["8384126179"]
    eq("no profile link -> private", private.seller_type, "private")
    eq("and no name", private.seller_name, None)
    eq("unlabelled shop is NOT called private", rows["7924194781"].seller_type, None)


def test_recommendation_carousel_is_not_results():
    """2026-10-10: «Подобрали для вас» sits inside the grid with item-shaped cards."""
    import product_parser as P
    html = fixture("listing_dom.html")
    carousel = ('<div data-marker="itemsCarousel"><h2>Подобрали для вас</h2>'
                '<div data-marker="item" data-item-id="9999999999">'
                '<a data-marker="item-title" href="/moskva/odezhda/hudi_9999999999">'
                '<h3>Худи</h3></a><meta itemprop="price" content="3290"></div></div>')
    i = html.index('data-marker="item"')
    i = html.rindex("<", 0, i)
    rows = P.parse_listing(html[:i] + carousel + html[i:], url=LISTING + "?p=2")
    eq("carousel card dropped, results kept", [r.sku for r in rows],
       ["8199454701", "8275958708", "8354606253", "8349145903"])


def test_the_bare_price_field_is_pre_discount():
    """The trap this repo is built around: Avito's bare price is the OLD one."""
    import product_parser as P
    catalog = P.listing_state(fixture("listing_state.html"))
    item = next(i for i in catalog["items"] if str(i["id"]) == "8245354778")
    eq("the bare field says 69 989", item["priceDetailed"]["value"], 69989)
    row = next(r for r in P.parse_listing(fixture("listing_state.html"), url=LISTING)
               if r.sku == "8245354778")
    check("the row does not take it", row.price != item["priceDetailed"]["value"])


def test_no_row_has_original_at_or_below_its_price():
    import product_parser as P
    rows = P.parse_listing(fixture("listing_state.html"), url=LISTING) + \
        P.parse_listing(fixture("listing_dom.html"), url=LISTING)
    bad = [r.sku for r in rows if r.original_price is not None and r.original_price <= (r.price or 0)]
    eq("original_price is always above price", bad, [])


def test_pagination_helpers():
    import product_parser as P
    eq("page 1 is the bare url", P.page_url(LISTING + "?p=4", 1), LISTING)
    eq("page 3", P.page_url(LISTING, 3), LISTING + "?p=3")
    eq("context token dropped, other params kept",
       P.page_url("https://www.avito.ru/moskva?q=iphone&context=H4sI&p=2", 5),
       "https://www.avito.ru/moskva?q=iphone&p=5")
    html = fixture("listing_dom.html")
    eq("site's pager total", P.total_pages(html), 30)
    eq("site's next link", P.next_page_href(html), LISTING + "?p=3")


def test_url_helpers():
    import product_parser as P
    eq("item kind", P.page_kind(ITEM_COMPANY), "item")
    eq("seller kind", P.page_kind(SELLER), "seller")
    eq("listing kind", P.page_kind(LISTING), "listing")
    eq("search is a listing", P.page_kind("https://www.avito.ru/moskva?q=iphone"), "listing")
    eq("item id", P.item_id_from_url(ITEM_COMPANY + "?context=x"), "8245354778")
    eq("region slug", P.region_slug(ITEM_COMPANY), "moskva")
    eq("strip query", P.strip_query("/brands/x?src=search_seller_info&iid=1"),
       "https://www.avito.ru/brands/x")
    eq("seller id", P.seller_id_from_url("/brands/bush_market?src=x"), "bush_market")
    ok, _ = P.supported_host(LISTING)
    check("www.avito.ru is supported", ok)
    for host in ("https://m.avito.ru/moskva", "https://www.avito.com/x", "https://example.org/"):
        ok, reason = P.supported_host(host)
        check("refused %s with a reason" % host, not ok and bool(reason))


def test_robots_rules_from_the_snapshot():
    import product_parser as P
    for url, allowed in ((LISTING, True), (LISTING + "?p=3", True),
                         ("https://www.avito.ru/moskva?q=iphone", True),
                         (SELLER, True), (ITEM_COMPANY, True),
                         (LISTING + "?s=104", False), (LISTING + "?f=ASgB", False),
                         ("https://www.avito.ru/items/phone/1", False)):
        eq("robots %s" % url[22:70], P.is_robots_allowed(url), allowed)


def test_numbers():
    import product_parser as P
    eq("nbsp price", P.parse_number("62 990 ₽"), 62990.0)
    eq("comma rating", P.parse_number("4,5"), 4.5)
    eq("no number", P.parse_number("Цена не указана"), None)
    eq("bool is not a number", P.parse_number(True), None)
    eq("count before word", P.count_before_word("1 524 отзыва"), 1524)
    eq("count with nbsp", P.count_before_word("2 659 отзывов"), 2659)
    eq("plain count", P.count_before_word("4 отзыва"), 4)
    eq("no count", P.count_before_word("нет отзывов"), None)


# ---------------------------------------------------------------------------
# 3. Item and seller pages
# ---------------------------------------------------------------------------

def test_item_company_values():
    import product_parser as P
    row = P.parse_item(fixture("item_company.html"), url=ITEM_COMPANY)
    check("parsed", row is not None)
    eq("sku", row.sku, "8245354778")
    eq("displayed price", row.price, 62990.0)
    eq("old price", row.original_price, 69989.0)
    eq("discount", row.discount_pct, 10.0)
    eq("company", row.seller_type, "company")
    eq("seller name kept for a company", row.seller_name, "Продавец 1")
    eq("seller since", row.seller_since, "мая 2019")
    eq("views", (row.views_total, row.views_today), (9425, 112))
    eq("condition from the page's own params", row.summary_params, "Новое")
    eq("params count", len(row.params), 9)
    eq("category path", row.category_path, ["Электроника", "Телефоны", "Apple", "Мобильные телефоны"])
    check("coords", row.lat and row.lng)
    eq("description is text, paragraphs kept",
       row.description.splitlines()[0], "Описание заменено в фикстуре. Текст объявления не публикуется.")


def test_item_private_values():
    import product_parser as P
    row = P.parse_item(fixture("item_private.html"), url=ITEM_PRIVATE)
    eq("private", row.seller_type, "private")
    eq("Avito's placeholder name is not exported", row.seller_name, None)
    check("IMEI is dropped", not any("IMEI" in k for k in (row.params or {})))
    check("the fixture DID carry an IMEI param", "IMEI" in fixture("item_private.html"))
    eq("price", row.price, 25000.0)
    eq("no discount", row.original_price, None)


def test_no_phone_reaches_a_row():
    import product_parser as P
    for name, url in (("item_company.html", ITEM_COMPANY), ("item_private.html", ITEM_PRIVATE)):
        check("%s fixture carries a masked phone" % name, "XXX-XX-XX" in fixture(name))
        row = dataclasses.asdict(P.parse_item(fixture(name), url=url))
        dumped = json.dumps(row, ensure_ascii=False)
        check("%s row has no phone" % name,
              "XXX-XX" not in dumped and not any("phone" in k.lower() for k in row))


def test_buyer_item_is_company_is_not_trusted():
    """`buyerItem.isCompany` was False for a shop in the capture."""
    import product_parser as P
    bi = P.buyer_item(fixture("item_company.html"))
    eq("the misleading flag is still False in the fixture", bi["isCompany"], False)
    eq("the row says company anyway",
       P.parse_item(fixture("item_company.html"), url=ITEM_COMPANY).seller_type, "company")


def test_seller_values():
    import product_parser as P
    seller, rows = P.parse_seller(fixture("seller.html"), url=SELLER)
    eq("name from the page title", seller.seller_name, "Продавец 1")
    eq("since", seller.seller_since, "мая 2019")
    eq("subscribers", seller.subscribers, 1775)
    eq("active ads counter", seller.active_ads, 353)
    eq("rating", seller.seller_rating, 5.0)
    eq("reviews", seller.seller_reviews, 624)
    eq("listings on page", (seller.listings_on_page, len(rows)), (2, 2))
    check("each listing carries the profile's seller",
          all(r.seller_name == "Продавец 1" and r.seller_id == "seller1" for r in rows))
    check("dates as printed", all(r.published_text for r in rows))


def test_seller_tabbed_layout_and_heading_name():
    """2026-10-10, /brands/i1300361: «Активные | 11 | Завершённые | 5» tabs,
    and a fetch whose title did not yield the name."""
    import product_parser as P
    html = fixture("seller.html")
    html = re.sub(r"<title>.*?</title>", "<title>Авито</title>", html, flags=re.S)
    html = re.sub(r"Объявления(\s*</[^>]+>)", r"Активные\1", html, count=1)
    seller, _ = P.parse_seller(html, url=SELLER)
    eq("tabbed counter read", seller.active_ads, 353)
    eq("no title, no heading -> None, not a guess", seller.seller_name, None)
    marker = 'data-marker="profile">'
    html = html.replace(marker, marker + '<h1 data-marker="name Продавец 1">Продавец 1</h1>', 1)
    seller, _ = P.parse_seller(html, url=SELLER)
    eq("name falls back to the profile heading", seller.seller_name, "Продавец 1")


# ---------------------------------------------------------------------------
# 4. The captcha rung
# ---------------------------------------------------------------------------

def test_wall_carries_the_geetest_id():
    import product_parser as P
    eq("captcha_id", P.geetest_captcha_id(fixture("wall_captcha.html")),
       "2d9c743cf7d63dbc9db578a608196bcd")


def test_geetest_task_shape():
    import captcha_solver as C
    task = C.geetest_v4_task("2d9c743cf7d63dbc9db578a608196bcd", LISTING, PLACEHOLDER_PROXY)
    eq("with a proxy -> GeeTestTask", task["type"], "GeeTestTask")
    eq("v4", task["version"], 4)
    eq("captcha_id goes in initParameters", task["initParameters"],
       {"captcha_id": "2d9c743cf7d63dbc9db578a608196bcd"})
    eq("proxy fields", (task["proxyType"], task["proxyAddress"], task["proxyPort"]),
       ("http", "proxy.invalid", 2334))
    eq("proxyless only without one",
       C.geetest_v4_task("2d9c743cf7d63dbc9db578a608196bcd", LISTING, None)["type"],
       "GeeTestTaskProxyless")
    try:
        C.geetest_v4_task("not-an-id", LISTING, None)
        check("a bad captcha_id is refused", False)
    except C.SolverError:
        check("a bad captcha_id is refused", True)


def test_solver_refuses_to_pay_for_a_token_the_site_rejects():
    """Measured: a proxyless GeeTest token was rejected (the site answered with
    a SECOND captcha). So no proxy -> no task, unless explicitly allowed."""
    import captcha_solver as C

    class Boom:
        def post(self, *a, **k):
            raise AssertionError("must not reach the network")
    try:
        C.solve_geetest_v4("k" * 32, "2d9c743cf7d63dbc9db578a608196bcd", LISTING,
                           proxy=None, session=Boom())
        check("proxyless solve refused", False)
    except C.SolverError as exc:
        check("proxyless solve refused before any request", "proxy" in str(exc))


def test_solver_flow_and_key_redaction():
    import captcha_solver as C
    sent = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    class Http:
        def post(self, url, json=None, timeout=None):
            sent.append((url, json))
            if url.endswith("/createTask"):
                return Resp({"errorId": 0, "taskId": 7})
            return Resp({"errorId": 0, "status": "ready", "cost": "0.00299",
                         "solution": {"captcha_id": "2d9c743cf7d63dbc9db578a608196bcd",
                                      "lot_number": "l", "pass_token": "p",
                                      "gen_time": "1", "captcha_output": "o"}})
    sol = C.solve_geetest_v4("SECRETKEY-EXAMPLE", "2d9c743cf7d63dbc9db578a608196bcd",
                             LISTING, proxy=PLACEHOLDER_PROXY, poll_s=0, session=Http())
    eq("solution keys", sorted(k for k in sol if not k.startswith("_")),
       ["captcha_id", "captcha_output", "gen_time", "lot_number", "pass_token"])
    check("the key rides in the BODY, never a URL",
          all("SECRETKEY" not in url for url, _ in sent))
    value = json.loads(C.firewall_response_value(sol))
    eq("firewall value is exactly getValidate() + captcha_id", sorted(value),
       ["captcha_id", "captcha_output", "gen_time", "lot_number", "pass_token"])
    body = C.submit_js_body(sol)
    check("submit fills the page's own field", "input[name=captcha-response]" in body)
    check("and submits the page's own form", ".js-firewall-form" in body)
    check("value is inlined as a JS string literal", 'inp.value = "{' in body)
    check("redaction", "SECRETKEY" not in C._redact("clientKey=SECRETKEY-EXAMPLE oops"))


# ---------------------------------------------------------------------------
# 5. The bridge, with a fake driver
# ---------------------------------------------------------------------------

def _args(**kw):
    base = dict(mode="listing", url=[LISTING], from_file=None, pages=1, category=None,
                max_products=None, delay=0, retries=0, retry_delay=0, concurrency=1,
                timeout=1, format="json", out="unused", allow_empty=False, dump_html=None,
                verbose=False, headless=True, cdp_endpoint=None, proxy=None, solver_proxy=None,
                pool=None, proxy_file=None, proxy_sessions=None, proxy_rotate="per-run",
                proxy_shuffle=False, proxy_block_retries=0, twocaptcha_key=None,
                solve_captcha="when-blocked")
    base.update(kw)
    return argparse.Namespace(**base)


class FakeDriver:
    name = "fake"

    def __init__(self, pages, after_js=None, scrolls=None):
        self.pages = list(pages)
        self.after_js = after_js
        self.scrolls = list(scrolls or [])   # documents a scroll reveals, in order
        self.scrolled = 0
        self.current = ""
        self.js = []
        self.autosolve_armed = False
        self.autosolve_events = []
        self.starts = 0

    def start(self):
        self.starts += 1

    def navigate(self, url):
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        self.current = page
        return 200

    def content(self):
        return self.current

    def count(self, selector):
        from bs4 import BeautifulSoup
        return len(BeautifulSoup(self.current, "html.parser").select(selector))

    def scroll_to_bottom(self):
        self.scrolled += 1
        if self.scrolls:
            self.current = self.scrolls.pop(0)
        return 1000 + len(self.current)

    def run_js(self, body):
        self.js.append(body)
        if self.after_js is not None:
            self.current = self.after_js
        return True

    def sleep(self, ms):
        pass

    def stop(self):
        pass


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


def test_a_transport_fault_is_not_a_block():
    import browser_bridge
    from output_writer import EXIT_REMOTE_API_ERROR
    with tempfile.TemporaryDirectory() as tmp:
        rc = _quiet(browser_bridge.run, _args(out=os.path.join(tmp, "o")),
                    FakeDriver([TimeoutError("Page.goto: Timeout 60000ms exceeded")]))
    eq("a navigation timeout exits 5, not 3", rc, EXIT_REMOTE_API_ERROR)


def test_retries_means_retries():
    import browser_bridge
    d = FakeDriver([TimeoutError("t1"), fixture("listing_dom.html")])
    with tempfile.TemporaryDirectory() as tmp:
        rc = _quiet(browser_bridge.run, _args(out=os.path.join(tmp, "o"), retries=1), d)
    eq("one retry turned a timeout into a result", rc, 0)


def test_unsolved_wall_is_blocked():
    import browser_bridge
    from output_writer import EXIT_BLOCKED
    with tempfile.TemporaryDirectory() as tmp:
        rc = _quiet(browser_bridge.run,
                    _args(out=os.path.join(tmp, "o"), solve_captcha="never"),
                    FakeDriver([fixture("wall_captcha.html")]))
        meta = json.load(open(os.path.join(tmp, "o.latest_attempt.meta.json")))
    eq("an unsolved wall exits 3", rc, EXIT_BLOCKED)
    eq("and the wall is recorded", meta["transport_facts"]["walls"][0]["state"], "captcha")


def test_solved_wall_goes_through_the_page_form():
    import browser_bridge
    import captcha_solver
    calls = {}
    real = captcha_solver.solve_geetest_v4

    def fake_solve(key, cid, url, proxy=None, **kw):
        calls.update(key=key, cid=cid, proxy=proxy)
        return {"captcha_id": cid, "lot_number": "l", "pass_token": "p", "gen_time": "1",
                "captcha_output": "o", "_cost": "0.00299"}   # a string, as the API sends it
    captcha_solver.solve_geetest_v4 = fake_solve
    try:
        d = FakeDriver([fixture("wall_captcha.html")], after_js=fixture("listing_dom.html"))
        with tempfile.TemporaryDirectory() as tmp:
            rc = _quiet(browser_bridge.run,
                        _args(out=os.path.join(tmp, "o"), twocaptcha_key="k",
                              solver_proxy=PLACEHOLDER_PROXY), d)
            meta = json.load(open(os.path.join(tmp, "o.meta.json")))
            rows = json.load(open(os.path.join(tmp, "o.json")))
    finally:
        captcha_solver.solve_geetest_v4 = real
    eq("solved wall -> rows", (rc, len(rows)), (0, 4))
    eq("the wall's own id went to the solver", calls.get("cid"), "2d9c743cf7d63dbc9db578a608196bcd")
    eq("through the browser's exit", calls.get("proxy"), PLACEHOLDER_PROXY)
    check("submitted through the page's own form", any(".js-firewall-form" in j for j in d.js))
    facts = meta["transport_facts"]
    eq("solve recorded as verified", facts["solves"][0]["verified"], True)
    eq("cost summed", facts["solver_cost"], 0.00299)
    eq("cleared_by", facts["walls_cleared_by"], ["solver"])


TINY_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
            "nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg==")


def _wall_showing(variant):
    """The REAL wall, with the server-chosen block un-hidden the way the wall's
    own renderCaptcha() does it. Derived on purpose: every live request so far
    was assigned GeeTest, so no capture of the other two exists."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(fixture("wall_captcha.html"), "html.parser")
    for sel, name in (("#geetest_captcha", "geetest"), ("#inner-captcha", "image"),
                      ("#h-captcha", "hcaptcha")):
        node = soup.select_one(sel)
        node["style"] = "display: %s" % ("block" if name == variant else "none")
    if variant == "image":
        soup.select_one("#inner-captcha img")["src"] = TINY_PNG
    return str(soup)


def test_the_wall_names_its_variant():
    import product_parser as P
    for variant in ("geetest", "image", "hcaptcha"):
        html = _wall_showing(variant)
        eq("variant %s recognised" % variant, P.wall_variant(html), variant)
        eq("variant %s is a captcha page" % variant, P.detect_page_state(html, url=LISTING), "captcha")
    eq("the picture is read off the wall", P.wall_image_src(_wall_showing("image")), TINY_PNG)
    eq("the wait-wall has no variant", P.wall_variant(fixture("wall_wait.html")), None)


def test_image_captcha_goes_through_the_page_form():
    import browser_bridge
    import captcha_solver
    calls = {}
    real = captcha_solver.solve_image

    def fake(key, src, **kw):
        calls["src"] = src
        return {"text": 'ab"12', "_cost": "0.001", "_task_id": 7}
    captcha_solver.solve_image = fake
    try:
        d = FakeDriver([_wall_showing("image")], after_js=fixture("listing_dom.html"))
        with tempfile.TemporaryDirectory() as tmp:
            rc = _quiet(browser_bridge.run, _args(out=os.path.join(tmp, "o"), twocaptcha_key="k",
                                                  solver_proxy=PLACEHOLDER_PROXY), d)
            facts = json.load(open(os.path.join(tmp, "o.meta.json")))["transport_facts"]
    finally:
        captcha_solver.solve_image = real
    eq("image wall solved -> rows", rc, 0)
    eq("the wall's own picture went to the solver", calls.get("src"), TINY_PNG)
    check("answer typed into the page's own #form-input",
          any("#form-input" in j and '"ab\\"12"' in j for j in d.js))
    eq("recorded as an image solve, verified", (facts["solves"][0]["variant"],
                                                facts["solves"][0]["verified"]), ("image", True))
    try:
        captcha_solver.image_task("https://example.invalid/pic.png")
        check("a picture that is not a data: URL is refused", False)
    except captcha_solver.SolverError:
        check("a picture that is not a data: URL is refused", True)


def test_hcaptcha_task_shape():
    """`HCaptchaTask` in the shape 2Captcha confirmed on 2026-10-09."""
    import captcha_solver as C
    import product_parser as P
    html = _wall_showing("hcaptcha")
    key = P.wall_hcaptcha_sitekey(html)
    eq("sitekey read off the wall", key, "070db171-ddb9-4c93-b7f6-d25d3c9d7e28")
    eq("no rqdata on avito's wall", P.wall_hcaptcha_rqdata(html), None)
    t = C.hcaptcha_task(key, LISTING, PLACEHOLDER_PROXY)
    eq("task fields", {k: t[k] for k in ("type", "websiteURL", "websiteKey", "isInvisible")},
       {"type": "HCaptchaTask", "websiteURL": LISTING, "websiteKey": key, "isInvisible": False})
    eq("through the browser's proxy", (t["proxyType"], t["proxyAddress"], t["proxyPort"]),
       ("http", "proxy.invalid", 2334))
    check("no enterprisePayload without rqdata", "enterprisePayload" not in t)
    eq("rqdata goes in enterprisePayload", C.hcaptcha_task(key, LISTING, None, rqdata="RQ-EXAMPLE")
       ["enterprisePayload"], {"rqdata": "RQ-EXAMPLE"})
    try:
        C.hcaptcha_task("not-a-key", LISTING, None)
        check("a bad sitekey is refused", False)
    except C.SolverError:
        check("a bad sitekey is refused", True)


def test_hcaptcha_goes_through_the_page_form():
    import browser_bridge
    import captcha_solver
    calls = {}
    real = captcha_solver.solve_hcaptcha

    def fake(key, sitekey, url, proxy=None, rqdata=None, **kw):
        calls.update(sitekey=sitekey, proxy=proxy, rqdata=rqdata)
        return {"token": "P1_EXAMPLE.token", "_cost": "0.00299", "_task_id": 9}
    captcha_solver.solve_hcaptcha = fake
    try:
        d = FakeDriver([_wall_showing("hcaptcha")], after_js=fixture("listing_dom.html"))
        with tempfile.TemporaryDirectory() as tmp:
            rc = _quiet(browser_bridge.run, _args(out=os.path.join(tmp, "o"), twocaptcha_key="k",
                                                  solver_proxy=PLACEHOLDER_PROXY), d)
            solve = json.load(open(os.path.join(tmp, "o.meta.json")))["transport_facts"]["solves"][0]
    finally:
        captcha_solver.solve_hcaptcha = real
    eq("hCaptcha wall solved -> rows", rc, 0)
    eq("the wall's own sitekey, through the browser's exit",
       (calls.get("sitekey"), calls.get("proxy")), ("070db171-ddb9-4c93-b7f6-d25d3c9d7e28", PLACEHOLDER_PROXY))
    check("token into #h-captcha-response of the page's own form",
          any("#h-captcha-response" in j and "P1_EXAMPLE.token" in j and ".js-firewall-form" in j
              for j in d.js))
    eq("recorded as an hCaptcha solve, verified", (solve["variant"], solve["verified"]), ("hcaptcha", True))


FAKE_SELLER_HASH = "ab" * 16   # not a real id; this file is outside the secret scan


def _seller_page(n_cards, active_ads=353, with_hash=True):
    """The REAL seller fixture with its card cloned to `n_cards` distinct ids,
    and (optionally) the profile's own link carrying a sellerId — trimmed out
    of the committed fixture because a 32-hex id reads as a credential."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(fixture("seller.html"), "html.parser")
    cards = soup.select(P_SELLER_CARD())
    proto, proto_id = str(cards[0]), cards[0]["data-item-id"]
    for c in cards:
        c.decompose()
    holder = soup.body
    for i in range(n_cards):
        holder.append(BeautifulSoup(proto.replace(proto_id, str(900000000 + i)), "html.parser"))
    html = str(soup).replace("353", str(active_ads))
    if with_hash:
        html = html.replace("</body>", '<a href="/brands/seller1/items/all?sellerId=%s">x</a></body>'
                            % FAKE_SELLER_HASH)
    return html


def P_SELLER_CARD():
    import product_parser
    return product_parser.SELLER_CARD_SELECTOR


def _run_seller_with(driver, **kw):
    import browser_bridge
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "o")
        rc = _quiet(browser_bridge.run, _args(mode="seller", url=[SELLER], out=out, **kw), driver)
        meta = json.load(open(out + ".latest_attempt.meta.json"))
        rows = json.load(open(out + "_listings.json")) if os.path.exists(out + "_listings.json") else []
        seller = json.load(open(out + ".json")) if os.path.exists(out + ".json") else []
    return rc, meta, rows, seller


def test_seller_feed_scrolls_to_the_profile_counter():
    """Owner decision 2026-10-09 (option A): a seller's listings beyond the
    profile's 15 come from scrolling its feed."""
    d = FakeDriver([_seller_page(15, active_ads=40), _seller_page(15, active_ads=40)],
                   scrolls=[_seller_page(30, 40), _seller_page(40, 40)])
    rc, meta, rows, seller = _run_seller_with(d)
    feed = meta["transport_facts"]["seller_feed"]
    eq("seller run ok", rc, 0)
    eq("all 40 listings, not the profile's 15", len(rows), 40)
    eq("distinct ids", len({r["sku"] for r in rows}), 40)
    eq("positions 1..40", [r["position"] for r in rows][:3] + [rows[-1]["position"]], [1, 2, 3, 40])
    eq("the feed URL is the allowed /all one",
       feed["url"], "https://www.avito.ru/brands/seller1/all?sellerId=" + FAKE_SELLER_HASH)
    eq("stopped on reaching the counter", (feed["rounds"], feed["stop"]), (2, "reached 40"))
    check("the feed was armed with its own «Показать все» button",
          feed["show_all_clicks"] >= 1 and any("show_all_button" in j for j in d.js))
    eq("seller row says how many listings were written", seller[0]["listings_on_page"], 40)


def test_seller_feed_respects_max_products_and_stops_when_still():
    d = FakeDriver([_seller_page(15, 353), _seller_page(15, 353)],
                   scrolls=[_seller_page(30, 353), _seller_page(45, 353)])
    rc, meta, rows, _ = _run_seller_with(d, max_products=20)
    eq("--max-products caps the feed", len(rows), 20)
    eq("and the scrolling stopped there", meta["transport_facts"]["seller_feed"]["rounds"], 1)
    d2 = FakeDriver([_seller_page(15, 353), _seller_page(15, 353)],
                    scrolls=[_seller_page(30, 353)])   # then nothing new arrives
    rc, meta, rows, _ = _run_seller_with(d2)
    feed = meta["transport_facts"]["seller_feed"]
    eq("a feed that stops growing ends the scroll", feed["stop"], "feed stopped growing")
    eq("with what it had", len(rows), 30)
    eq("after the still rounds, not forever", feed["rounds"], 4)


def test_seller_without_an_id_keeps_its_first_listings():
    # Profile and its /items page both without a sellerId.
    d = FakeDriver([_seller_page(15, 353, with_hash=False), _seller_page(15, 353, with_hash=False)])
    rc, meta, rows, _ = _run_seller_with(d)
    from output_writer import EXIT_PARTIAL
    eq("no sellerId -> the profile's 15, reported PARTIAL (15 of 353)", (rc, len(rows)),
       (EXIT_PARTIAL, 15))
    eq("status partial", (meta["status"], meta["stop_reason"]), ("partial", "seller_listings_short"))
    eq("and the reason is recorded", meta["transport_facts"]["seller_feed"]["stop"],
       "no sellerId on the profile")


def test_shop_sellerid_comes_from_its_items_page():
    """2026-10-10, /brands/a3boots.shop: the profile has no sellerId; its
    `/brands/<slug>/items` page does, and the `/all` feed then works."""
    import product_parser as P
    eq("items URL has no ?s= (robots)", P.seller_items_url(SELLER),
       "https://www.avito.ru/brands/seller1/items")
    check("and robots allows it", P.robots_verdict(P.seller_items_url(SELLER))[0])
    d = FakeDriver([_seller_page(15, 40, with_hash=False), _seller_page(12, 40),
                    _seller_page(15, 40)], scrolls=[_seller_page(40, 40)])
    rc, meta, rows, _ = _run_seller_with(d)
    feed = meta["transport_facts"]["seller_feed"]
    eq("all 40 via the feed, exit 0", (rc, len(rows)), (0, 40))
    eq("id found on the items page", feed["sellerId_from"], "items page")
    eq("feed URL", feed["url"], "https://www.avito.ru/brands/seller1/all?sellerId=" + FAKE_SELLER_HASH)


class PaintingDriver(FakeDriver):
    """A page that changes as time passes: `timeline` is [(ms, html), …] —
    from `ms` of accumulated sleep on, `content()` returns that html."""

    def __init__(self, timeline):
        super().__init__([timeline[0][1]])
        self.timeline = timeline
        self.slept = 0

    def sleep(self, ms):
        self.slept += ms

    def content(self):
        html = self.timeline[0][1]
        for at, doc in self.timeline:
            if self.slept >= at:
                html = doc
        self.current = html
        return html


def _skeleton():
    """The real listing with its cards, state and pager removed — what a
    served page looks like before the grid paints (58 KB, 2026-10-10)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(fixture("listing_dom.html"), "html.parser")
    for node in soup.select('[data-marker="item"], [data-marker*="pagination-button"], '
                            'script[data-mfe-state]'):
        node.decompose()
    return str(soup)


def _proof_of_work():
    return re.sub(r'<form[^>]*js-firewall-form.*?</form>', "", fixture("wall_captcha.html"),
                  flags=re.S)


def test_a_skeleton_is_not_an_empty_listing():
    """Audit 2026-10-10: `?q=iphone+15` exited 4 after 0 ms on a 58 KB page
    with the right title and no cards yet."""
    import product_parser as P
    sk = _skeleton()
    check("the skeleton has no cards and no pager",
          not P._has_listing_cards(sk) and "pagination-button" not in sk)
    eq("a skeleton is unknown (wait), not empty", P.detect_page_state(sk, url=LISTING), "unknown")
    check("but it carries the weak sign", P.served_listing_without_cards(sk, LISTING))
    eq("«Похожие объявления» is still empty at once",
       P.detect_page_state(fixture("listing_empty.html"), url=LISTING), "empty")


def test_fetch_waits_for_a_skeleton_to_paint():
    import browser_bridge
    facts = {"walls": [], "solves": [], "rotations": 0}
    d = PaintingDriver([(0, _skeleton()), (2000, fixture("listing_dom.html"))])
    r = _quiet(browser_bridge.fetch_page, d, _args(), LISTING, "listing", facts)
    eq("cards painted after 2 s -> content, not empty", r["state"], "content")
    d = PaintingDriver([(0, _skeleton())])
    r = _quiet(browser_bridge.fetch_page, d, _args(), LISTING, "listing", facts)
    eq("still a skeleton after the full wait -> empty", r["state"], "empty")
    check("and only after the full wait", d.slept >= 20000, "(slept %d ms)" % d.slept)


def test_after_proof_of_work_a_skeleton_is_waited_for():
    """Audit 2026-10-10, point 2: a local run after the proof-of-work got a
    5 KB page and ended «page 1 is a served listing with no cards», exit 4."""
    import browser_bridge
    facts = {"walls": [], "solves": [], "rotations": 0}
    d = PaintingDriver([(0, _proof_of_work()), (3000, _skeleton()),
                        (9000, fixture("listing_dom.html"))])
    r = _quiet(browser_bridge.fetch_page, d, _args(), LISTING, "listing", facts)
    eq("proof-of-work -> skeleton -> cards = content", r["state"], "content")
    eq("the wall was recorded", [w["state"] for w in facts["walls"]], ["challenge"])


def test_out_directory_is_created():
    """Audit 2026-10-10, point 3: README writes to `live/`, absent in a clone."""
    import browser_bridge
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "live", "deeper", "run")
        dump = os.path.join(tmp, "dumps", "page.html")
        rc = _quiet(browser_bridge.run, _args(out=out, dump_html=dump),
                    FakeDriver([fixture("listing_dom.html")]))
        eq("run ok into a missing directory", rc, 0)
        check("rows written", os.path.exists(out + ".json"))
        check("attempt metadata written", os.path.exists(out + ".latest_attempt.meta.json"))
        check("dump written", os.path.exists(os.path.join(tmp, "dumps", "page_p1.html")))


def test_item_log_counts_what_will_be_fetched():
    """Audit 2026-10-10, point 4: `--max-products 2` over 100 urls logged 1/100."""
    import browser_bridge
    urls = [ITEM_COMPANY] * 2 + ["%s%d" % (ITEM_PRIVATE[:-3], n) for n in range(100, 198)]
    buf = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(buf), \
            contextlib.redirect_stderr(buf):
        browser_bridge.run(_args(mode="item", url=urls, max_products=2, verbose=True,
                                 out=os.path.join(tmp, "o")),
                           FakeDriver([fixture("item_company.html")] * 2))
    log = buf.getvalue()
    check("denominator is the cap", "item 1/2:" in log and "item 2/2:" in log, log[:300])
    check("not the file's length", "/100:" not in log)


def test_the_wait_wall_is_a_refusal_not_a_challenge():
    """Measured 2026-10-08: a third firewall variant with neither a captcha
    form nor a proof-of-work — «подождите немного и обновите страницу». It
    does not clear in place; the exit has to change."""
    import product_parser as P
    html = fixture("wall_wait.html")
    eq("wait-wall -> blocked", P.detect_page_state(html, url=LISTING), "blocked")
    eq("and with its 429", P.detect_page_state(html, status=429, url=LISTING), "blocked")
    import page_flow
    check("which rotates the exit", page_flow.should_rotate("blocked"))


def test_a_solve_followed_by_another_wall_is_not_verified():
    """The first version marked a solve `verified` as soon as the page was
    'not the wall' — and on 2026-10-08 an accepted solve was followed by the
    wait-wall while the metadata said verified."""
    import browser_bridge
    import captcha_solver
    real = captcha_solver.solve_geetest_v4

    def fake_solve(key, cid, url, proxy=None, **kw):
        return {"captcha_id": cid, "lot_number": "l", "pass_token": "p", "gen_time": "1",
                "captcha_output": "o", "_cost": "0.00299", "_task_id": 1}
    captcha_solver.solve_geetest_v4 = fake_solve
    try:
        d = FakeDriver([fixture("wall_captcha.html")], after_js=fixture("wall_wait.html"))
        with tempfile.TemporaryDirectory() as tmp:
            rc = _quiet(browser_bridge.run, _args(out=os.path.join(tmp, "o"), twocaptcha_key="k",
                                                  solver_proxy=PLACEHOLDER_PROXY), d)
            solve = json.load(open(os.path.join(tmp, "o.latest_attempt.meta.json")))[
                "transport_facts"]["solves"][0]
    finally:
        captcha_solver.solve_geetest_v4 = real
    eq("solve followed by the wait-wall -> blocked (3)", rc, 3)
    eq("NOT verified", solve["verified"], False)
    eq("and says what came instead", solve.get("after_submit"), "blocked")


def test_a_solver_failure_is_a_warning_not_a_crash():
    import browser_bridge
    import captcha_solver
    real = captcha_solver.solve_geetest_v4

    def failing(*a, **k):
        raise captcha_solver.SolverError("getTaskResult: ERROR_CAPTCHA_UNSOLVABLE (task 84066204532)",
                                         task_id=84066204532, error_code="ERROR_CAPTCHA_UNSOLVABLE")
    captcha_solver.solve_geetest_v4 = failing
    try:
        with tempfile.TemporaryDirectory() as tmp:
            rc = _quiet(browser_bridge.run,
                        _args(out=os.path.join(tmp, "o"), twocaptcha_key="k",
                              solver_proxy=PLACEHOLDER_PROXY),
                        FakeDriver([fixture("wall_captcha.html")]))
            solve = json.load(open(os.path.join(tmp, "o.latest_attempt.meta.json")))[
                "transport_facts"]["solves"][0]
    finally:
        captcha_solver.solve_geetest_v4 = real
    eq("solver failure -> blocked (3), not crash (1)", rc, 3)
    eq("the failed solve is traceable: task id and 2Captcha's own code",
       (solve["task_id"], solve["error_code"]), (84066204532, "ERROR_CAPTCHA_UNSOLVABLE"))


def test_a_dead_exit_rotates_a_local_browser():
    import browser_bridge
    d = FakeDriver([RuntimeError("net::ERR_CONNECTION_CLOSED at https://www.avito.ru/"),
                    fixture("listing_dom.html")])
    args = _args(proxy=GATEWAY_PROXY + "", proxy_block_retries=1)
    args.proxy = args.proxy.replace("region-RU", "region-RU-session-abcdefghi")
    args.solver_proxy = args.proxy
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp:
        args.out = os.path.join(tmp, "o")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = browser_bridge.run(args, d)
    eq("a dead exit was replaced and the page fetched", rc, 0)
    check("the rotation names Chromium's error, not a URL fragment",
          "the exit failed (ERR_CONNECTION_CLOSED)" in err.getvalue(), "(got %r)" % err.getvalue()[-200:])
    eq("a fresh browser was started", d.starts, 2)
    check("the solver follows the browser to the new exit", args.solver_proxy == args.proxy)
    check("on a NEW session", "session-abcdefghi" not in args.proxy)
    check("timeouts are not exit failures",
          not browser_bridge._is_exit_failure("Timeout 60000ms exceeded"))


def test_wrong_mode_and_robots_are_refused_before_fetching():
    import browser_bridge
    for kw, why in ((dict(url=[ITEM_COMPANY]), "item url in listing mode"),
                    (dict(url=[LISTING + "?s=104"]), "robots-disallowed sort"),
                    (dict(url=["https://m.avito.ru/moskva"]), "unmeasured host")):
        try:
            _quiet(browser_bridge.run, _args(**kw), FakeDriver([]))
            check("refused: %s" % why, False)
        except SystemExit as exc:
            check("refused: %s" % why, "refusing" in str(exc))
            eq("refused with exit 2: %s" % why, exc.code, 2)


def test_cli_contract():
    import browser_bridge
    parser = browser_bridge.build_parser("x")
    flags = {a.dest for a in parser._actions}
    for flag in ("url", "from_file", "mode", "pages", "max_products", "delay", "retries",
                 "retry_delay", "concurrency", "format", "out", "allow_empty", "dump_html",
                 "cdp_endpoint", "proxy", "proxy_file", "proxy_sessions", "proxy_rotate",
                 "proxy_shuffle", "proxy_block_retries", "twocaptcha_key", "solve_captcha",
                 "headless", "timeout", "category", "verbose"):
        check("shared parser defines --%s" % flag.replace("_", "-"), flag in flags)
    for gone in ("min_score", "captcha_api"):
        check("no reCAPTCHA-only --%s here" % gone.replace("_", "-"), gone not in flags)
    for argv in (["--pages", "0"], ["--retries", "-1"], ["--delay", "-1"], ["--timeout", "0"],
                 ["--concurrency", "2"], ["--from", "x.json"]):
        try:
            _quiet(browser_bridge.parse_args, parser, ["--url", LISTING] + argv)
            check("refused %s" % " ".join(argv), False)
        except SystemExit:
            check("refused %s" % " ".join(argv), True)


def test_proxy_routing_by_transport():
    import browser_bridge
    import env_config
    saved = {k: os.environ.get(k) for k in env_config.ENV_KEYS}
    for k in env_config.ENV_KEYS:
        os.environ[k] = ""
    try:
        parser = browser_bridge.build_parser("x")
        cdp = "ws://" + "EXAMPLE-LOGIN" + ":" + "EXAMPLE-PASS" + "@cb.2captcha.com:9222"
        pinned = GATEWAY_PROXY.replace("region-RU", "region-RU-session-abcdefghi")
        a = _quiet(browser_bridge.parse_args, parser,
                   ["--url", LISTING, "--cdp-endpoint", cdp, "--proxy", pinned])
        eq("CDP: the proxy is never applied to the remote browser", a.proxy, None)
        eq("CDP: it goes to the solver only", a.solver_proxy, pinned)
        b = _quiet(browser_bridge.parse_args, parser, ["--url", LISTING, "--proxy", GATEWAY_PROXY])
        check("local: a bare gateway login is pinned to one session", "-session-" in b.proxy)
        eq("local: the solver shares the browser's exit", b.solver_proxy, b.proxy)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_both_gateway_brands_are_recognised():
    import proxy_pool
    check("rucaptcha gateway", proxy_pool.is_2captcha_gateway(GATEWAY_PROXY))
    check("2captcha gateway", proxy_pool.is_2captcha_gateway(
        GATEWAY_PROXY.replace("eu.proxy.rucaptcha.com", "eu.proxy.2captcha.com")))
    check("anything else is not", not proxy_pool.is_2captcha_gateway(PLACEHOLDER_PROXY))


# A gateway credential as the 2Captcha dashboard exports it, and pieces of
# one with characters that need percent-encoding in a URL. Assembled from
# parts for the same reason as GATEWAY_PROXY.
GATEWAY_HOST = "ru.proxy." + "2captcha.com"
GATEWAY_LOGIN = "uEXAMPLE-zone-custom-region-RU"
ODD_PASSWORD = "EX@MPLE:p/ss%w#rd"


def _minted(value):
    """tools/browser_profile_client.minted_proxy(60) on AVITO_PROXY=value:
    ("ok", url) or ("exit", message). No .env is read and nothing is sent."""
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "_bpc_under_test", os.path.join(HERE, "tools", "browser_profile_client.py"))
    bpc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bpc)
    bpc.ENV = pathlib.Path(HERE, "no-such-dir", ".env")
    saved = os.environ.get("AVITO_PROXY")
    os.environ["AVITO_PROXY"] = value
    try:
        return "ok", _quiet(bpc.minted_proxy, 60)
    except SystemExit as exc:
        return "exit", str(exc.code)
    except Exception as exc:  # noqa: BLE001 -- the shape of the failure is the check
        return "raised", "%s: %s" % (type(exc).__name__, exc)
    finally:
        if saved is None:
            os.environ.pop("AVITO_PROXY", None)
        else:
            os.environ["AVITO_PROXY"] = saved


def test_a_gateway_url_without_a_login_is_refused_by_name():
    import proxy_pool
    for label, value, secret in (
            ("host-only", "http://" + GATEWAY_HOST + ":2334", None),
            ("empty username", "http://:" + "EXAMPLE-PASS" + "@" + GATEWAY_HOST + ":2334",
             "EXAMPLE-PASS"),
            ("login, no password", "http://" + GATEWAY_LOGIN + "@" + GATEWAY_HOST + ":2334",
             GATEWAY_LOGIN),
            ("three colon fields", "http://" + GATEWAY_HOST + ":2334:" + GATEWAY_LOGIN,
             GATEWAY_LOGIN)):
        kind, msg = _minted(value)
        eq("minted_proxy, %s: exits" % label, kind, "exit")
        check("minted_proxy, %s: says the login is missing" % label,
              "AVITO_PROXY has no login" in msg and "LOGIN:PASSWORD" in msg, "(got %r)" % msg)
        check("minted_proxy, %s: not the misleading not-a-gateway message" % label,
              "the URL given is not one" not in msg)
        if secret:
            check("minted_proxy, %s: the credential is not in the message" % label,
                  secret not in msg)
        try:
            proxy_pool.mint_sessions(value, 1)
            check("mint_sessions, %s: refused" % label, False)
        except proxy_pool.ProxyError as exc:
            check("mint_sessions, %s: refused for the login" % label,
                  "has no login" in str(exc), "(got %r)" % str(exc))
            if secret:
                check("mint_sessions, %s: no credential in the message" % label,
                      secret not in str(exc))


def test_minted_proxy_rebuilds_the_login_in_the_netloc():
    from urllib.parse import urlparse
    import proxy_pool
    url_form = "http://" + GATEWAY_LOGIN + ":" + "EXAMPLE-PASS" + "@" + GATEWAY_HOST + ":2334"
    cases = (
        ("plain login", url_form, "http", GATEWAY_LOGIN, "EXAMPLE-PASS"),
        ("login already pinned",
         url_form.replace(GATEWAY_LOGIN, GATEWAY_LOGIN + "-session-OLDsess01-sessTime-30"),
         "http", GATEWAY_LOGIN, "EXAMPLE-PASS"),
        ("socks5", "socks5" + url_form[4:], "socks5", GATEWAY_LOGIN, "EXAMPLE-PASS"),
        ("%-encoded password",
         url_form.replace("EXAMPLE-PASS", "EX%40MPLE%3Ap"), "http", GATEWAY_LOGIN, "EX@MPLE:p"),
        ("dashboard colon form",
         GATEWAY_HOST + ":2334:" + GATEWAY_LOGIN + ":" + "EXAMPLE-PASS",
         "http", GATEWAY_LOGIN, "EXAMPLE-PASS"),
        ("dashboard colon form with http://",
         "http://" + GATEWAY_HOST + ":2334:" + GATEWAY_LOGIN + ":" + "EXAMPLE-PASS",
         "http", GATEWAY_LOGIN, "EXAMPLE-PASS"),
        ("colon form, password with @ : / % #",
         GATEWAY_HOST + ":2334:" + GATEWAY_LOGIN + ":" + ODD_PASSWORD,
         "http", GATEWAY_LOGIN, ODD_PASSWORD),
    )
    for label, value, scheme, login, password in cases:
        kind, got = _minted(value)
        eq("minted_proxy, %s: a URL" % label, kind, "ok")
        if kind != "ok":
            continue
        p = urlparse(got)
        eq("minted_proxy, %s: scheme kept" % label, p.scheme, scheme)
        eq("minted_proxy, %s: gateway host kept" % label, p.hostname, GATEWAY_HOST)
        eq("minted_proxy, %s: port kept" % label, p.port, 2334)
        user, pw = proxy_pool.credentials_of(p)
        eq("minted_proxy, %s: password intact" % label, pw, password)
        check("minted_proxy, %s: login keeps its base" % label, user.startswith(login + "-"),
              "(got %r)" % user)
        eq("minted_proxy, %s: one sessTime, the requested one" % label,
           (user.count("-sessTime-"), user.endswith("-sessTime-60")), (1, True))
        eq("minted_proxy, %s: one fresh session" % label,
           (user.count("-session-"), "OLDsess01" in user), (1, False))


def test_the_dashboard_colon_form_is_a_proxy_url():
    import captcha_solver
    import proxy_pool
    colon = GATEWAY_HOST + ":2334:" + GATEWAY_LOGIN + ":" + ODD_PASSWORD
    for label, value in (("bare", colon), ("with http://", "http://" + colon)):
        line = proxy_pool.parse_proxy_line(value)
        check("colon form %s: parsed to a URL" % label,
              line.startswith("http://") and line.endswith("@" + GATEWAY_HOST + ":2334"),
              "(got %s)" % proxy_pool.mask(line))
        check("colon form %s: a gateway" % label, proxy_pool.is_2captcha_gateway(value))
        pw = proxy_pool.to_playwright(line)
        eq("colon form %s: playwright gets the decoded login" % label,
           pw.get("username"), GATEWAY_LOGIN)
        eq("colon form %s: playwright gets the decoded password" % label,
           pw.get("password"), ODD_PASSWORD)
        eq("colon form %s: playwright server has no credential" % label,
           pw.get("server"), "http://" + GATEWAY_HOST + ":2334")
        fields = captcha_solver.proxy_task_fields(line)
        eq("colon form %s: solver fields decoded" % label,
           (fields.get("proxyAddress"), fields.get("proxyPort"),
            fields.get("proxyLogin"), fields.get("proxyPassword")),
           (GATEWAY_HOST, 2334, GATEWAY_LOGIN, ODD_PASSWORD))
        for shown in (proxy_pool.mask(value), proxy_pool.mask(line)):
            check("colon form %s: mask hides the password" % label,
                  "p/ss" not in shown and GATEWAY_LOGIN not in shown, "(got %r)" % shown)
        import base64
        import proxy_forwarder
        token = base64.b64encode((GATEWAY_LOGIN + ":" + ODD_PASSWORD).encode())
        check("colon form %s: the forwarder sends the decoded credential" % label,
              token in proxy_forwarder.ProxyForwarder(line)._auth)
        minted = proxy_pool.mint_sessions(value, 2)
        eq("colon form %s: sessions keep the password" % label,
           [pw_ for _, pw_ in (proxy_pool.split_credentials(m)[1] for m in minted)],
           [ODD_PASSWORD, ODD_PASSWORD])
    # A malformed port still says what is wrong without repeating the value.
    bad = "http://" + GATEWAY_HOST + ":23x4:" + GATEWAY_LOGIN + ":" + "EXAMPLE-PASS"
    try:
        proxy_pool.parse_proxy_line(bad)
        check("bad port refused", False)
    except proxy_pool.ProxyError as exc:
        check("bad port refused without the credential",
              "EXAMPLE-PASS" not in str(exc) and GATEWAY_LOGIN not in str(exc),
              "(got %r)" % str(exc))


def test_the_colon_form_reaches_every_engine_as_a_url():
    import browser_bridge
    import env_config
    saved = {k: os.environ.get(k) for k in env_config.ENV_KEYS}
    for k in env_config.ENV_KEYS:
        os.environ[k] = ""
    try:
        parser = browser_bridge.build_parser("x")
        colon = GATEWAY_HOST + ":2334:" + GATEWAY_LOGIN + ":" + "EXAMPLE-PASS"
        b = _quiet(browser_bridge.parse_args, parser, ["--url", LISTING, "--proxy", colon])
        check("colon --proxy: pinned to one session as a URL",
              b.proxy.startswith("http://" + GATEWAY_LOGIN + "-session-")
              and b.proxy.endswith("@" + GATEWAY_HOST + ":2334"))
        try:
            _quiet(browser_bridge.parse_args, browser_bridge.build_parser("x"),
                   ["--url", LISTING, "--proxy", "http://" + GATEWAY_HOST + ":2334"])
            check("host-only gateway --proxy: refused", False)
        except SystemExit as exc:
            eq("host-only gateway --proxy: usage error", exc.code, 2)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_no_javascript_crosses_the_bridge():
    """The one JS body lives in captcha_solver and is written as a function
    BODY (Selenium's dialect); each engine wraps it. The bridge carries none."""
    import browser_bridge
    tree = ast.parse(inspect.getsource(browser_bridge))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree)
    for token in ("() =>", "document.", "execute_script"):
        check("bridge code contains no %r" % token, token not in code)


# ---------------------------------------------------------------------------
# 6. The loopback proxy forwarder
# ---------------------------------------------------------------------------

def test_forwarder_adds_the_credential_upstream_only():
    from proxy_forwarder import ProxyForwarder
    seen = {}
    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)
    port = upstream.getsockname()[1]

    def serve():
        conn, _ = upstream.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(4096)
        seen["request"] = data.decode()
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        conn.close()
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    fwd = ProxyForwarder("http://" + "EXAMPLE-USER" + ":" + "EXAMPLE-PASS" +
                         "@127.0.0.1:%d" % port).start()
    try:
        client = socket.create_connection(("127.0.0.1", fwd.port), timeout=5)
        client.sendall(b"CONNECT www.avito.ru:443 HTTP/1.1\r\nHost: www.avito.ru:443\r\n"
                       b"Proxy-Authorization: Basic bm9wZQ==\r\n\r\n")
        reply = client.recv(4096)
        client.close()
        t.join(5)
    finally:
        fwd.stop()
        upstream.close()
    import base64
    want = base64.b64encode(b"EXAMPLE-USER:EXAMPLE-PASS").decode()
    req = seen.get("request", "")
    check("listener is loopback-only", fwd.url.startswith("http://127.0.0.1:"))
    check("upstream got OUR credential", ("Proxy-Authorization: Basic " + want) in req)
    check("the client's own credential header was dropped", "bm9wZQ==" not in req)
    check("CONNECT passed through", req.startswith("CONNECT www.avito.ru:443"))
    check("tunnel reply relayed", reply.startswith(b"HTTP/1.1 200"))


# ---------------------------------------------------------------------------
# 7. Output contract
# ---------------------------------------------------------------------------

def test_schema_and_exit_codes():
    import output_writer as O
    prefix = ["source", "scraped_at", "url", "sku", "title", "image_url", "price",
              "currency", "category", "price_source"]
    eq("family prefix first and in order", [f.name for f in dataclasses.fields(O.Product)][:10], prefix)
    eq("Item keeps the prefix by construction", [f.name for f in dataclasses.fields(O.Item)][:10], prefix)
    eq("source", O.SOURCE_DEFAULT, "avito.ru")
    eq("modes -> rows", {m: c.__name__ for m, c in O.ROW_CLASS_BY_MODE.items()},
       {"listing": "Product", "item": "Item", "seller": "Seller"})
    check("seller rows are not diffable by sku", "seller" not in O.UNIQUE_BY_SKU_MODES)
    eq("exit codes", (O.EXIT_BLOCKED, O.EXIT_NO_PRODUCTS, O.EXIT_REMOTE_API_ERROR, O.EXIT_PARTIAL),
       (3, 4, 5, 6))
    names = {f.name for f in dataclasses.fields(O.Item)}
    for banned in ("phone", "imei"):
        check("no %s column" % banned, not any(banned in n for n in names))


def test_csv_holds_dicts_and_lists():
    import output_writer as O
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "i.csv")
        O.write_csv([O.Item(sku="1", params={"Цвет": "Чёрный"}, images=["a", "b"])], path, row_cls=O.Item)
        text = open(path, encoding="utf-8").read()
    check("params written as JSON", '{""Цвет"": ""Чёрный""}' in text)
    check("lists joined", "a | b" in text)


def test_empty_result_does_not_overwrite():
    import output_writer as O
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "out")
        _quiet(O.save, [O.Product(sku="1", price=1.0, currency="RUB")], prefix, "json")
        before = open(prefix + ".json").read()
        rc = _quiet(O.save, [], prefix, "json")
        eq("empty result exits 4", rc, O.EXIT_NO_PRODUCTS)
        eq("and left the data alone", open(prefix + ".json").read(), before)


def test_stale_metadata_cannot_report_false_success():
    import output_writer as O
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "out")
        _quiet(O.finish_run, [O.Product(sku="1")], prefix, "json", False, blocked=False,
               stop_reason="completed", pages_requested=1, pages_completed=1,
               start_url="u", final_url="u")
        rc = _quiet(O.finish_run, [], prefix, "json", False, blocked=True, stop_reason="blocked",
                    pages_requested=1, pages_completed=0, start_url="u", final_url="u")
        eq("blocked exits 3", rc, O.EXIT_BLOCKED)
        eq("dataset sidecar unchanged", json.load(open(prefix + ".meta.json"))["status"], "complete")
        attempt = json.load(open(prefix + O.ATTEMPT_META_SUFFIX))
        eq("attempt sidecar records failure", (attempt["status"], attempt["data_updated"]),
           ("failed", False))


def test_scope_identifies_the_listing_not_the_page():
    import output_writer as O
    a = O.scope_fingerprint(LISTING + "?p=2&context=x", "listing", 3)
    b = O.scope_fingerprint(LISTING, "listing", 3)
    eq("p and context do not change the scope", a["listing"], b["listing"])


def test_dedupe_is_page_ordered():
    import output_writer as O
    seen = set()
    out = O.dedupe_by_sku([O.Product(sku="a"), O.Product(sku="b")], seen) + \
        O.dedupe_by_sku([O.Product(sku="b"), O.Product(sku="c")], seen)
    eq("merged in page order", [r.sku for r in out], ["a", "b", "c"])


def test_diff_runs_refuses_seller_mode():
    import diff_runs
    check("diff_runs knows the modes", "seller" not in diff_runs.UNIQUE_BY_SKU_MODES)


# ---------------------------------------------------------------------------
# 8. Credentials and secrets
# ---------------------------------------------------------------------------

def test_credentials_never_reach_free_text():
    import browser_bridge
    secret = "ws://" + "EXAMPLE-LOGIN" + ":" + "EXAMPLE-PASSWORD" + "@cb.2captcha.com:9222"
    masked = browser_bridge.mask_text("connect failed for %s and again for %s" % (secret, secret))
    check("password gone", "EXAMPLE-PASSWORD" not in masked)
    check("masked GLOBALLY", masked.count("***:***@") == 2, "(got %r)" % masked)
    check("host and port kept", "cb.2captcha.com:9222" in masked)


def test_env_keys_and_example():
    import env_config
    eq("env keys", set(env_config.ENV_KEYS),
       {"TWOCAPTCHA_KEY", "AVITO_CDP_ENDPOINT", "AVITO_PROXY", "AVITO_URL"})
    path = os.path.join(HERE, ".env.example")
    documented = {l.split("=", 1)[0].strip() for l in open(path, encoding="utf-8")
                  if l.strip() and not l.lstrip().startswith("#") and "=" in l}
    eq(".env.example documents exactly what is read", documented, set(env_config.ENV_KEYS))


def _scanner():
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import importlib
    return importlib.import_module("scan_secrets")


def test_no_secret_in_tracked_files():
    import subprocess
    scan = _scanner()
    try:
        tracked = subprocess.run(["git", "ls-files", "-z"], cwd=HERE, capture_output=True,
                                 text=True, timeout=60)
    except Exception:
        skip("secret scan (git unavailable)")
        return
    if tracked.returncode != 0:
        skip("secret scan (not a git repo)")
        return
    offenders = []
    for name in [n for n in tracked.stdout.split("\0") if n]:
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            hits = scan.scan_text(name, handle.read())
        offenders += ["%s: %s" % (name, h) for h in hits]
    eq("no tracked file carries a credential", offenders, [])


def test_the_scanner_catches_the_file_that_actually_leaked():
    """The family's 2026-09-22 incident, reconstructed by SHAPE, never values."""
    scan = _scanner()
    leaked = ("TWOCAPTCHA_KEY=" + "0123456789abcdef" * 2 + "\n"
              "AVITO_PROXY=http://uabc123def456" "-zo" "ne-custom-region-RU"
              ":uabc123def456@eu.proxy.rucaptcha.com:2334\n"
              "AVITO_CDP_ENDPOINT=ws://bc9876fedcba" "-zo" "ne-scraping_browser"
              "-country-ru-pid-pdeadbeef:Qq7RtYuIoPaSdF@cb.2captcha.com:9222\n")
    found = " | ".join(scan.scan_text(".env.user-endpoint.bak", leaked))
    for what in (".env file", "backup", "32-hex", "proxy gateway", "Scraping Browser"):
        check("leaked file caught by %s" % what, what in found)
    check("the public GeeTest id alone is not a finding",
          scan.scan_text("x.py", "captchaId = '2d9c743cf7d63dbc9db578a608196bcd'") == [])
    check("a DIFFERENT 32-hex still is",
          scan.scan_text("x.py", "k = '" + "0123456789abcdef" * 2 + "'") != [])


def test_hooks_are_versioned_and_wired():
    for name in ("pre-commit", "pre-push"):
        path = os.path.join(HERE, ".githooks", name)
        check(".githooks/%s exists and is executable" % name,
              os.path.exists(path) and os.access(path, os.X_OK))
        if os.path.exists(path):
            check(".githooks/%s runs the shared scanner" % name,
                  "scan_secrets.py" in open(path, encoding="utf-8").read())
    installer = os.path.join(HERE, "tools", "install_hooks.sh")
    check("installer uses core.hooksPath",
          os.path.exists(installer) and "core.hooksPath" in open(installer, encoding="utf-8").read())


def test_only_one_secret_scanner_implementation():
    body = open(os.path.join(HERE, ".github", "ci_checks.py"), encoding="utf-8").read()
    check("ci_checks delegates to the shared scanner", "scan_secrets.py" in body)


def test_gitignore_covers_every_env_variant_and_working_files():
    lines = {l.strip() for l in open(os.path.join(HERE, ".gitignore"), encoding="utf-8")}
    for pattern in (".env", ".env.*", "!.env.example", "*.bak", "live/", "proxylist*.txt"):
        check("gitignore has %r" % pattern, pattern in lines)


def test_attempt_sidecar_records_the_exit_code_on_every_ending():
    """README: the attempt sidecar is written on EVERY ending "with
    `exit_code`". Only the transport-failure path set it; a complete run, a
    block (3), an empty listing (4), a page that failed (5) and a partial run
    (6) all wrote `exit_code: null` (manual test 2026-10-19)."""
    import output_writer as O
    row = O.Product(sku="1", title="t", url=LISTING)
    cases = (([row], False, "completed", 0), ([], True, "blocked", 3),
             ([], False, "pagination_exhausted", 4), ([], False, "pages_failed", 5),
             ([row], False, "pages_failed", 6))
    with tempfile.TemporaryDirectory() as tmp:
        for rows, blocked, reason, want in cases:
            prefix = os.path.join(tmp, "run_%s" % want)
            rc = _quiet(O.finish_run, rows, prefix, "json", False, blocked=blocked,
                        stop_reason=reason, pages_requested=2, pages_completed=1,
                        start_url=LISTING, final_url=LISTING)
            eq("finish_run returns %d (%s)" % (want, reason), rc, want)
            meta = json.load(open(prefix + O.ATTEMPT_META_SUFFIX, encoding="utf-8"))
            eq("attempt sidecar exit_code (%s)" % reason, meta.get("exit_code"), want)


def test_the_scanner_reads_this_suite_too():
    """The suite was exempt from the secret scan wholesale, so the file most
    likely to receive a pasted credential was the one nobody read. Its own
    credential-shaped samples are assembled from pieces instead, like the
    banned tokens elsewhere in this file."""
    scan = _scanner()
    check("smoke_test.py is not exempt from the scan", "smoke_test.py" not in scan.SKIP)
    text = open(os.path.join(HERE, "smoke_test.py"), encoding="utf-8").read()
    eq("this suite holds nothing credential-shaped", scan.scan_text("smoke_test.py", text), [])
    planted = text + "\nKEY = '" + "0123456789abcdef" * 2 + "'\n"
    check("a key pasted into the suite is caught", scan.scan_text("smoke_test.py", planted) != [])


def test_gitignore_covers_dumps_of_any_extension():
    """`--dump-html X.ext` writes X_p<N>.ext, and each extension used to need
    its own line (.html, .txt, .htm): `--dump-html snap.dat` left an
    unignored page capture one `git add -A` from a commit. Asked of git
    itself, since .gitignore precedence is easy to misread."""
    def git(*a):
        return subprocess.run(["git", *a], cwd=HERE, capture_output=True, text=True, timeout=30)
    try:
        if git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            raise OSError
    except (OSError, subprocess.SubprocessError):
        skip("gitignore dump shapes (not a git checkout)")
        return
    for name in ("snap_p1.dat", "x_p12.md", "out_p3.html", "dump_p2.txt", "live2/page_p1.json"):
        check("gitignored dump: %s" % name, git("check-ignore", "-q", "--no-index", name).returncode == 0)
    tracked_but_ignored = [n for n in git("ls-files", "-ci", "--exclude-standard").stdout.splitlines() if n]
    eq("no tracked file matches an ignore pattern", tracked_but_ignored, [])


def test_fixtures_carry_no_session_or_personal_material():
    """Guarded with PATTERNS, so the NEXT capture is caught too."""
    scan = _scanner()
    patterns = [
        ("a context token", re.compile(r"context=H4sI")),
        ("a 64-hex user key", re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")),
        ("an unmasked phone", re.compile(r"\b8 9\d\d \d{3}-\d\d-\d\d\b")),
        ("a url with a password", re.compile(r"://[^/@\s\"']+:[^/@\s\"']+@")),
    ]
    readme = os.path.join(FIXTURES, "README.md")
    check("fixtures/README.md says what is not verbatim", os.path.exists(readme))
    for name in sorted(os.listdir(FIXTURES)):
        if not name.endswith(".html"):
            continue
        text = fixture(name)
        for label, pattern in patterns:
            check("%s carries no %s" % (name, label), pattern.search(text) is None)
        hex_hits = [h for h in scan.HEX32.findall(text) if h not in scan.PUBLIC_HEX]
        eq("%s carries no 32-hex beyond the site's public id" % name, hex_hits, [])


# ---------------------------------------------------------------------------
# 9. Engines
# ---------------------------------------------------------------------------

ENGINES = ("playwright_scraper", "selenium_scraper", "puppeteer_scraper")


def test_engines_import_their_driver_at_module_level():
    wanted = {"playwright_scraper": "playwright", "selenium_scraper": "selenium",
              "puppeteer_scraper": "pyppeteer"}
    for name in ENGINES:
        tree = ast.parse(open(os.path.join(HERE, name + ".py"), encoding="utf-8").read())
        top = {getattr(n, "module", None) or "" for n in tree.body
               if isinstance(n, (ast.Import, ast.ImportFrom))}
        top |= {a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names}
        check("%s imports %s at module level" % (name, wanted[name]),
              any(t.split(".")[0] == wanted[name] for t in top))


def test_engine_parity():
    for name in ENGINES:
        try:
            module = __import__(name)
        except ImportError:
            skip("%s (engine library absent)" % name)
            continue
        drivers = [v for k, v in vars(module).items() if k.endswith("Driver") and isinstance(v, type)]
        check("%s defines one driver" % name, len(drivers) == 1)
        for method in ("start", "navigate", "content", "count", "run_js", "scroll_to_bottom",
                       "sleep", "stop"):
            check("%s.%s" % (name, method), callable(getattr(drivers[0], method, None)))
        check("%s has main()" % name, callable(getattr(module, "main", None)))
        d = drivers[0](_args())
        for attr in ("autosolve_armed", "autosolve_events"):
            check("%s driver exposes %s" % (name, attr), hasattr(d, attr))


def test_playwright_autosolve():
    try:
        import playwright_scraper
    except ImportError:
        skip("autosolve (playwright absent)")
        return

    class FakeCdp:
        def __init__(self, fail=False):
            self.sent, self.handlers, self.fail = [], {}, fail

        def send(self, method, params=None):
            if self.fail:
                raise RuntimeError("%s wasn't found" % method)
            self.sent.append((method, params))

        def on(self, event, handler):
            self.handlers[event] = handler

    class Ctx:
        def __init__(self, cdp):
            self.cdp = cdp

        def new_cdp_session(self, page):
            return self.cdp

    def driver(cdp, solve="when-blocked"):
        d = playwright_scraper.PlaywrightDriver(_args(solve_captcha=solve, cdp_endpoint="ws://x"))
        d._context, d._page = Ctx(cdp), object()
        return d
    cdp = FakeCdp()
    d = driver(cdp)
    d._arm_autosolve()
    eq("only setAutoSolve is sent (Captcha.enable does not exist — measured)",
       cdp.sent, [("Captcha.setAutoSolve", {"autoSolve": True})])
    check("armed", d.autosolve_armed)
    cdp.handlers["Captcha.solveFailed"]({})
    eq("events are recorded", d.autosolve_events[0]["event"], "Captcha.solveFailed")
    d2 = driver(FakeCdp(fail=True))
    d2._arm_autosolve()
    check("an endpoint without the domain is a note, not a crash",
          not d2.autosolve_armed and d2.autosolve_notes)
    cdp3 = FakeCdp()
    driver(cdp3, solve="never")._arm_autosolve()
    eq("--solve-captcha never arms nothing", cdp3.sent, [])


def test_selenium_keeps_the_credential_off_argv():
    try:
        import selenium_scraper
    except ImportError:
        skip("selenium guards (engine absent)")
        return
    import browser_bridge
    try:
        selenium_scraper.SeleniumDriver(_args(cdp_endpoint="ws://" + "EXAMPLE-USER" + ":"
                                              + "EXAMPLE-PASS" + "@cb.2captcha.com:9222")).start()
        check("selenium refuses --cdp-endpoint", False)
    except browser_bridge.BridgeError as exc:
        check("selenium refuses --cdp-endpoint, naming the reason",
              "debuggerAddress" in str(exc) and "EXAMPLE-PASS" not in str(exc))
    captured = {}

    class FakeChrome:
        def __init__(self, options=None):
            captured["args"] = list(options.arguments)

        def set_page_load_timeout(self, t):
            pass

        def quit(self):
            pass
    real = selenium_scraper.webdriver.Chrome
    selenium_scraper.webdriver.Chrome = FakeChrome
    try:
        d = selenium_scraper.SeleniumDriver(_args(proxy=PLACEHOLDER_PROXY))
        d.start()
        argv = " ".join(captured["args"])
        check("password not in Chrome's argv", "EXAMPLE-PASS" not in argv)
        check("user not in Chrome's argv", "EXAMPLE-USER" not in argv)
        check("Chrome points at the loopback forwarder", "--proxy-server=http://127.0.0.1:" in argv)
        d.stop()
        check("the forwarder stops with the driver", d._forwarder is None)
    finally:
        selenium_scraper.webdriver.Chrome = real


def test_pyppeteer_connect_timeout_says_what_it_may_hide():
    """pyppeteer never surfaces the Scraping Browser's refusal: on 2026-10-20
    a `500 proxy_error` reached the user as a bare 'TimeoutError: ' after 30 s,
    and only a Playwright connect showed the reason. The message now says so."""
    try:
        import puppeteer_scraper
    except ImportError:
        skip("pyppeteer guards (engine absent)")
        return
    import asyncio
    import browser_bridge

    async def never(**kw):
        await asyncio.sleep(3600)
    real_connect, real_timeout = puppeteer_scraper.connect, puppeteer_scraper.CDP_CONNECT_TIMEOUT_S
    puppeteer_scraper.connect, puppeteer_scraper.CDP_CONNECT_TIMEOUT_S = never, 0.05
    try:
        d = puppeteer_scraper.PuppeteerDriver(_args(cdp_endpoint="ws://" + "u:p" + "@cb.2captcha.com:9222"))
        try:
            d.start()
            check("a silent endpoint raises", False)
        except browser_bridge.BridgeError as exc:
            msg = str(exc)
            for word in ("profile_locked", "proxy_error", "playwright_scraper.py", "--mint-session"):
                check("pyppeteer timeout message names %s" % word, word in msg, "(got %r)" % msg)
        finally:
            d.stop()
    finally:
        puppeteer_scraper.connect, puppeteer_scraper.CDP_CONNECT_TIMEOUT_S = real_connect, real_timeout


def test_pyppeteer_keeps_the_credential_off_argv():
    try:
        import puppeteer_scraper
    except ImportError:
        skip("pyppeteer guards (engine absent)")
        return
    captured = {}

    class Page:
        async def authenticate(self, creds):
            captured["auth"] = creds

    class Browser:
        async def newPage(self):
            return Page()

        async def close(self):
            pass

    async def fake_launch(**options):
        captured["args"] = options["args"]
        return Browser()
    real = puppeteer_scraper.launch
    puppeteer_scraper.launch = fake_launch
    try:
        d = puppeteer_scraper.PuppeteerDriver(_args(proxy=PLACEHOLDER_PROXY))
        d.start()
        d.stop()
    finally:
        puppeteer_scraper.launch = real
    argv = " ".join(captured.get("args", []))
    check("password not in argv", "EXAMPLE-PASS" not in argv)
    check("address in argv", "--proxy-server=http://proxy.invalid:2334" in argv)
    eq("credential through page.authenticate", captured.get("auth"),
       {"username": "EXAMPLE-USER", "password": "EXAMPLE-PASS"})


# ---------------------------------------------------------------------------
# 10. Structure
# ---------------------------------------------------------------------------

MODULES = ["avito_cli", "robots_snapshot", "product_parser", "output_writer", "page_flow", "browser_bridge", "captcha_solver",
           "env_config", "proxy_pool", "proxy_forwarder", "diff_runs", "fingerprint_client",
           "playwright_scraper", "selenium_scraper", "puppeteer_scraper",
           "tools/browser_profile_client", "tools/scan_secrets", "tools/autosolve_probe"]


def test_unresolved_names():
    """`compileall` proves a file PARSES, not that its names RESOLVE. This
    repo's own profile tool shipped `os.chmod` without `import os` and died on
    the first `--write-env` — after writing half of `.env`."""
    import builtins

    def bind_args(node, bound):
        a = getattr(node, "args", None)
        if a is None:
            return
        for group in (a.posonlyargs, a.args, a.kwonlyargs):
            for arg in group:
                bound.add(arg.arg)
        for extra in (a.vararg, a.kwarg):
            if extra:
                bound.add(extra.arg)
    for name in MODULES:
        path = os.path.join(HERE, name + ".py")
        if not os.path.exists(path):
            check("%s exists" % name, False)
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        bound = set(dir(builtins)) | {"__name__", "__file__", "__doc__"}
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                (bound if isinstance(node.ctx, ast.Store) else used).add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if not isinstance(node, ast.Lambda):
                    bound.add(node.name)
                bind_args(node, bound)
            elif isinstance(node, ast.ClassDef):
                bound.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.Global):
                bound.update(node.names)
            elif isinstance(node, ast.comprehension):
                for t in ast.walk(node.target):
                    if isinstance(t, ast.Name):
                        bound.add(t.id)
        eq("%s has no unresolved names" % name, sorted(used - bound), [])


def test_dockerfile_copies_every_module_the_entrypoints_import():
    path = os.path.join(HERE, "Dockerfile")
    text = open(path, encoding="utf-8").read()
    local = {n[:-3] for n in os.listdir(HERE) if n.endswith(".py")}
    needed, frontier = set(), ["playwright_scraper"]
    while frontier:
        name = frontier.pop()
        if name in needed:
            continue
        needed.add(name)
        tree = ast.parse(open(os.path.join(HERE, name + ".py"), encoding="utf-8").read())
        for node in ast.walk(tree):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) else []
            frontier += [m for m in mods if m in local]
    copied, continuing = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if continuing or s.upper().startswith("COPY"):
            copied.append(s)
            continuing = s.endswith("\\")
    copied = " ".join(copied)
    eq("Dockerfile COPYs every module the entrypoint reaches",
       sorted(m for m in needed if (m + ".py") not in copied), [])
    check("image bakes in no .env", ".env" not in copied)
    check("image carries no suite or fixtures", "smoke_test" not in copied and "fixtures" not in copied)


def test_banned_wordings():
    banned = ("cloud browser", "antidetect browser", "2scraper antidetect", "gate.2prx.com")
    for name in os.listdir(HERE):
        if name.endswith((".py", ".md")) and name != "smoke_test.py":
            text = open(os.path.join(HERE, name), encoding="utf-8", errors="replace").read().lower()
            for phrase in banned:
                check("%s does not say %r" % (name, phrase), phrase not in text)


def test_no_site_specific_drift_from_a_sibling_repo():
    """This repo was scaffolded from homedepot-scraper; its words must not
    survive in shipped code (2scraper family rule: copied core is untested core)."""
    import subprocess
    # Substrings, plus whole words that are ordinary English but name another
    # site's domain here. The first version scanned a hand-kept list of file
    # kinds and missed `requirements.txt` ("bershka-scraper"), `.gitignore`
    # and the Claude review prompt ("Player and Transfer", "in EUR") — found
    # by an external audit, 2026-10-07. So: every file git tracks.
    foreign = ("homedepot", "home depot", "akamai", "apollo", "bershka", "givenchy", "farfetch",
               "mediamarkt", "transfermarkt", "inditex", "demandware", "storeid",
               "index.html", "landing.md", "claude.md")
    words = re.compile(r"\b(player|players|transfer|transfers|eur)\b")
    try:
        tracked = subprocess.run(["git", "ls-files", "-z"], cwd=HERE, capture_output=True,
                                 text=True, timeout=60)
        names = [n for n in tracked.stdout.split("\0") if n] if tracked.returncode == 0 else []
    except Exception:
        names = []
    if not names:
        skip("sibling-vocabulary scan (not a git checkout)")
        return
    for name in names:
        if name == "smoke_test.py" or name.startswith("fixtures/") and name.endswith(".html"):
            continue
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8", errors="replace").read().lower()
        hits = [w for w in foreign if w in text] + sorted(set(words.findall(text)))
        eq("%s is free of sibling vocabulary" % name, hits, [])


# ---------------------------------------------------------------------------
# 11. Regressions from the external audit of v1.0.0 (2026-10-07)
# ---------------------------------------------------------------------------

def test_robots_rules_ship_as_a_module_and_fail_closed():
    """P0-1: the wheel did not ship robots.snapshot.txt; an installed copy
    read zero rules and allowed every URL."""
    import hashlib
    import product_parser as P
    import robots_snapshot
    eq("snapshot hash matches its content",
       hashlib.sha256(robots_snapshot.ROBOTS_TXT.encode()).hexdigest(), robots_snapshot.SHA256)
    check("parser no longer reads a loose data file",
          "robots.snapshot.txt" not in inspect.getsource(P._robots_rules))
    toml = open(os.path.join(HERE, "pyproject.toml"), encoding="utf-8").read()
    check("robots_snapshot is a packaged module", '"robots_snapshot"' in toml)
    saved_rules, saved_txt = P._ROBOTS, robots_snapshot.ROBOTS_TXT
    try:
        P._ROBOTS, robots_snapshot.ROBOTS_TXT = None, "User-agent: *\n"
        try:
            P._robots_rules()
            check("an empty snapshot fails CLOSED", False)
        except RuntimeError:
            check("an empty snapshot fails CLOSED", True)
    finally:
        P._ROBOTS, robots_snapshot.ROBOTS_TXT = saved_rules, saved_txt


def test_console_script_without_playwright_explains_itself():
    """P0-2: `pip install avito-scraper` gave a command that died with
    ModuleNotFoundError: playwright on its first run."""
    import avito_cli
    toml = open(os.path.join(HERE, "pyproject.toml"), encoding="utf-8").read()
    check("console script goes through the launcher", 'avito-scraper = "avito_cli:main"' in toml)
    saved = {k: v for k, v in sys.modules.items() if k == "playwright_scraper"
             or k == "playwright" or k.startswith("playwright.")}
    for k in saved:
        del sys.modules[k]
    sys.modules["playwright"] = None  # makes `import playwright...` fail
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            rc = avito_cli.main(["--help"])
    finally:
        del sys.modules["playwright"]
        sys.modules.pop("playwright_scraper", None)
        sys.modules.update(saved)
    eq("missing playwright -> exit 2, not a traceback", rc, 2)
    check("and says what to install", "avito-scraper[playwright]" in err.getvalue())


class _StartFails(FakeDriver):
    def start(self):
        raise RuntimeError("BrowserType.connect_over_cdp: WebSocket error: ws://"
                           + "EXAMPLE-LOGIN" + ":" + "EXAMPLE-PASS" + "@cb.2captcha.com:9222/ 500")


def test_every_ending_writes_the_attempt_sidecar():
    """P1-2 / P1-3: a start failure and an unexpected exception returned
    before finish_run, so no attempt metadata; and a parser bug was reported
    as exit 5, like a dead exit."""
    import browser_bridge
    import product_parser as P
    from output_writer import ATTEMPT_META_SUFFIX
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "o")
        rc = _quiet(browser_bridge.run, _args(out=out), _StartFails([]))
        meta = json.load(open(out + ATTEMPT_META_SUFFIX))
        eq("start failure -> exit 5", rc, 5)
        eq("and an attempt sidecar saying so",
           (meta["status"], meta["stop_reason"], meta["exit_code"], meta["data_updated"]),
           ("failed", "transport_error", 5, False))
        check("with the error class", meta["transport_facts"]["error_class"] == "RuntimeError")
        check("and no password in it", "EXAMPLE-PASS" not in json.dumps(meta))

        real = P.parse_listing

        def broken(*a, **k):
            raise TypeError("a parser bug")
        P.parse_listing = broken
        try:
            out2 = os.path.join(tmp, "o2")
            rc = _quiet(browser_bridge.run, _args(out=out2), FakeDriver([fixture("listing_dom.html")]))
        finally:
            P.parse_listing = real
        meta = json.load(open(out2 + ATTEMPT_META_SUFFIX))
        eq("an unexpected exception is a crash (1), not transport (5)", rc, 1)
        eq("recorded as a crash", (meta["stop_reason"], meta["exit_code"]), ("crash", 1))


def test_diff_runs_refuses_runs_it_cannot_vouch_for():
    """P1-4: two JSON files with no sidecars were compared freely."""
    import diff_runs
    import output_writer as O
    with tempfile.TemporaryDirectory() as tmp:
        old, new = os.path.join(tmp, "old.json"), os.path.join(tmp, "new.json")
        for p in (old, new):
            open(p, "w").write("[]")
        args = argparse.Namespace(old=old, new=new)
        check("no metadata -> refused", _quiet(diff_runs._check_comparable, args) is False)
        base = {"status": "complete", "mode": "listing", "pages_completed": 1, "pages_requested": 1}
        for p, cls in ((old, O.Product), (new, O.Item)):
            m = dict(base, scope={"listing": LISTING, "pages": 1,
                                  "schema_version": O.schema_version(cls)})
            open(p[:-5] + ".meta.json", "w").write(json.dumps(m))
        check("different schema versions -> refused",
              _quiet(diff_runs._check_comparable, args) is False)
        m = dict(base, scope={"listing": LISTING, "pages": 1,
                              "schema_version": O.schema_version(O.Product)})
        open(new[:-5] + ".meta.json", "w").write(json.dumps(m))
        check("complete, same listing, same schema -> allowed",
              _quiet(diff_runs._check_comparable, args) is True)


def test_diff_runs_refuses_a_capped_run_against_a_full_one():
    """A full listing run against the same listing capped by --max-products
    read as "127 removed" (manual tests 2026-10-14 and 2026-10-19)."""
    import diff_runs
    import output_writer as O
    with tempfile.TemporaryDirectory() as tmp:
        old, new = os.path.join(tmp, "old.json"), os.path.join(tmp, "new.json")
        for p in (old, new):
            open(p, "w").write("[]")
        args = argparse.Namespace(old=old, new=new)

        def sidecar(path, cap, with_key=True):
            scope = {"listing": LISTING, "pages": 3, "schema_version": O.schema_version(O.Product)}
            if with_key:
                scope["max_products"] = cap
            open(path[:-5] + ".meta.json", "w").write(json.dumps(
                {"status": "complete", "mode": "listing", "pages_completed": 3,
                 "pages_requested": 3, "scope": scope}))

        sidecar(old, None)
        sidecar(new, 20)
        check("full vs --max-products 20 -> refused",
              _quiet(diff_runs._check_comparable, args) is False)
        sidecar(old, 20)
        check("both capped at 20 -> allowed", _quiet(diff_runs._check_comparable, args) is True)
        sidecar(old, None, with_key=False)
        sidecar(new, None)
        check("an older sidecar without the key reads as no cap -> allowed",
              _quiet(diff_runs._check_comparable, args) is True)


def test_diff_runs_refusal_advice_fits_the_reason():
    """A refusal over --max-products told the reader to "re-run the
    incomplete side" when both runs were complete (manual test 2026-10-19,
    step 50). The advice follows the reason now."""
    import diff_runs
    import output_writer as O
    with tempfile.TemporaryDirectory() as tmp:
        old, new = os.path.join(tmp, "old.json"), os.path.join(tmp, "new.json")
        for p in (old, new):
            open(p, "w").write("[]")
        args = argparse.Namespace(old=old, new=new)

        def sidecar(path, cap, status="complete"):
            scope = {"listing": LISTING, "pages": 3, "max_products": cap,
                     "schema_version": O.schema_version(O.Product)}
            open(path[:-5] + ".meta.json", "w").write(json.dumps(
                {"status": status, "mode": "listing", "pages_completed": 3,
                 "pages_requested": 3, "scope": scope}))

        def said():
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                diff_runs._check_comparable(args)
            return out.getvalue()

        sidecar(old, None)
        sidecar(new, 20)
        text = said()
        check("cap refusal advises the same --max-products", "same --max-products" in text, text)
        check("cap refusal does not blame an incomplete side", "incomplete side" not in text, text)
        sidecar(new, None, status="partial")
        text = said()
        check("partial refusal still advises re-running it", "incomplete side" in text, text)


def test_schema_version_is_per_mode():
    """P2-1: the fingerprint covered Product's field names only."""
    import output_writer as O
    versions = {m: O.schema_version(c) for m, c in O.ROW_CLASS_BY_MODE.items()}
    eq("three modes, three schema versions", len(set(versions.values())), 3)
    eq("scope carries the MODE's version", O.scope_fingerprint(LISTING, "item", 1)["schema_version"],
       versions["item"])


def test_zero_rows_exit_by_stop_reason():
    """0 rows is 4 only when the run is known to have read the listing to its
    end; any other reason means the content was never obtained (5). An item
    that no longer exists, or a profile that never parsed, used to exit 4."""
    import output_writer as O
    with tempfile.TemporaryDirectory() as tmp:
        for reason, want in (("completed", 4), ("pagination_exhausted", 4),
                             ("no_new_products", 4), ("single_page_mode", 4),
                             ("pages_failed", 5), ("no_profile", 5),
                             ("transport_error", 5), ("a_reason_added_later", 5)):
            rc = _quiet(O.finish_run, [], os.path.join(tmp, reason), "json", False,
                        blocked=False, stop_reason=reason, pages_requested=1,
                        pages_completed=0, start_url=LISTING, final_url=LISTING)
            eq("0 rows, stop_reason=%s -> exit %d" % (reason, want), rc, want)
        rc = _quiet(O.finish_run, [], os.path.join(tmp, "b"), "json", False,
                    blocked=True, stop_reason="blocked", pages_requested=1,
                    pages_completed=0, start_url=LISTING, final_url=LISTING)
        eq("0 rows, blocked -> exit 3", rc, 3)
    import browser_bridge
    gone = "<html><head><title>Ошибка 404</title></head><body>Такой страницы нет</body></html>"
    with tempfile.TemporaryDirectory() as tmp:
        rc = _quiet(browser_bridge.run, _args(mode="item", url=[ITEM_COMPANY],
                                              out=os.path.join(tmp, "o")),
                    FakeDriver([gone]))
    eq("an item page with no item on it exits 5, not 4", rc, 5)


def test_usage_errors_exit_2():
    """README: exit 2 for a wrong host, a wrong --mode or a robots-disallowed
    URL. They were SystemExit("message"), which exits 1."""
    import browser_bridge
    for argv, why in ((["--url", LISTING + "?s=104"], "robots-disallowed"),
                      (["--url", "https://m.avito.ru/moskva"], "foreign host"),
                      (["--url", ITEM_COMPANY], "item url in listing mode"),
                      (["--mode", "item", "--from", "/nonexistent/rows.json"], "missing --from"),
                      (["--url", LISTING, "--url", LISTING + "/x"], "two urls in listing mode")):
        try:
            _quiet(browser_bridge.parse_args, browser_bridge.build_parser("x"), argv)
            check("parse_args refuses: %s" % why, False)
        except SystemExit as exc:
            eq("parse_args refuses with exit 2: %s" % why, exc.code, 2)
    try:
        _quiet(browser_bridge.run, _args(mode="item", url=None, from_file="/nonexistent.json"),
               FakeDriver([]))
        check("run() refuses an unreadable --from", False)
    except SystemExit as exc:
        eq("run() refuses an unreadable --from with exit 2", exc.code, 2)


def test_output_file_modes():
    """mkstemp creates 0600 and os.replace kept it: every output was
    owner-only. New files follow the umask; an existing file keeps its mode."""
    import output_writer as O
    old = os.umask(0o022)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for umask, want in ((0o022, 0o644), (0o002, 0o664), (0o077, 0o600)):
                os.umask(umask)
                path = os.path.join(tmp, "new_%o.json" % umask)
                O.write_json([O.Product(sku="1")], path)
                eq("new file under umask %03o is %03o" % (umask, want),
                   oct(os.stat(path).st_mode & 0o777), oct(want))
            os.umask(0o022)
            path = os.path.join(tmp, "kept.json")
            open(path, "w").close()
            os.chmod(path, 0o664)
            O.write_json([O.Product(sku="1")], path)
            eq("an existing 664 file stays 664", oct(os.stat(path).st_mode & 0o777), oct(0o664))
            _quiet(O.write_run_meta, os.path.join(tmp, "m"), {"status": "x"})
            eq("the sidecar follows the umask too",
               oct(os.stat(os.path.join(tmp, "m.meta.json")).st_mode & 0o777), oct(0o644))
    finally:
        os.umask(old)


def test_csv_formula_cells_are_neutralised():
    """Titles, locations and seller names are written by whoever posted the
    ad. A CSV cell starting = + - @ TAB CR LF runs as a formula in a
    spreadsheet; JSON keeps the site's bytes."""
    import output_writer as O
    row = O.Product(sku="1", title="=HYPERLINK(\"http://x\")", price=-5.0,
                    currency="RUB", location="+79001234567")
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "o")
        _quiet(O.finish_run, [row], prefix, "both", False, blocked=False,
               stop_reason="completed", pages_requested=1, pages_completed=1,
               start_url=LISTING, final_url=LISTING, extra={"csv_cells_escaped": "caller"})
        with open(prefix + ".csv", newline="", encoding="utf-8") as handle:
            got = list(csv.DictReader(handle))[0]
        data = json.load(open(prefix + ".json", encoding="utf-8"))[0]
        meta = json.load(open(prefix + ".meta.json", encoding="utf-8"))
    eq("title escaped in CSV", got["title"], "'=HYPERLINK(\"http://x\")")
    eq("location escaped in CSV", got["location"], "'+79001234567")
    eq("a negative NUMBER stays a number", got["price"], "-5.0")
    eq("JSON keeps the site's bytes", data["title"], "=HYPERLINK(\"http://x\")")
    eq("sidecar declares the count", meta.get("csv_cells_escaped"), 2)
    eq("the caller's own extra is not overwritten",
       meta["transport_facts"].get("csv_cells_escaped"), "caller")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "i.csv")
        n = O.write_csv([O.Item(sku="1", images=["=1+1", "ok"], params={"a": "b"})],
                        path, row_cls=O.Item)
        with open(path, newline="", encoding="utf-8") as handle:
            got = list(csv.DictReader(handle))[0]
    eq("a joined list is escaped after joining", got["images"], "'=1+1 | ok")
    eq("and counted", n, 1)


def test_worktree_scan_reads_untracked_files_and_skips_venvs():
    """--worktree read tracked files only, so an untracked key-holding file
    was 'clean', and a tree with no git scanned 0 files and said 'clean'."""
    import shutil
    import subprocess
    if not shutil.which("git"):
        skip("worktree scan (git absent)")
        return
    scanner = os.path.join(HERE, "tools", "scan_secrets.py")
    key = "".join("%x" % (i % 16) for i in range(7, 39))  # 32 hex, assembled
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "build", "out", "r")
        os.makedirs(os.path.join(repo, "myenv", "lib"))
        subprocess.run(["git", "init", "-q", repo], check=True)
        open(os.path.join(repo, "ok.py"), "w").write("x = 1\n")
        subprocess.run(["git", "-C", repo, "add", "ok.py"], check=True)
        open(os.path.join(repo, "myenv", "pyvenv.cfg"), "w").write("home = /usr\n")
        open(os.path.join(repo, "myenv", "lib", "vendored.py"), "w").write("# %s\n" % key)

        def scan(cwd):
            r = subprocess.run([sys.executable, scanner, "--worktree"], cwd=cwd,
                               capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr

        rc, out = scan(repo)
        eq("a venv (found by pyvenv.cfg) is not scanned", rc, 0)
        open(os.path.join(repo, "notes.py"), "w").write("k = '%s'\n" % key)
        rc, out = scan(repo)
        check("an UNTRACKED file holding a key fails the scan",
              rc == 1 and "notes.py" in out, "(rc=%s)" % rc)
        os.remove(os.path.join(repo, "notes.py"))
        open(os.path.join(repo, ".env.bak"), "w").write("TWOCAPTCHA_KEY=x\n")
        rc, out = scan(repo)
        check("an untracked .env backup fails the scan",
              rc == 1 and ".env.bak" in out, "(rc=%s)" % rc)
        bare = os.path.join(tmp, "nogit")
        os.makedirs(bare)
        rc, out = scan(bare)
        check("a tree with no git fails loudly instead of reading as clean",
              rc == 1 and "0 files" in out, "(rc=%s)" % rc)


def test_selenium_cdp_endpoint_is_a_usage_question():
    """Selenium cannot use the Scraping Browser. A typed --cdp-endpoint is a
    usage error (2); the one in .env is shared by every engine and is ignored
    with a notice. It used to reach start() and exit 5 'transport_error'."""
    import browser_bridge
    endpoint = "ws://u" + "-zo" + "ne-scraping_browser-country-ru:p@h:9222"
    try:
        _quiet(browser_bridge.parse_args, browser_bridge.build_parser("x"),
               ["--url", LISTING, "--cdp-endpoint", endpoint], cdp_supported=False)
        check("a typed --cdp-endpoint is refused", False)
    except SystemExit as exc:
        eq("a typed --cdp-endpoint is refused with exit 2", exc.code, 2)
    saved = os.environ.get("AVITO_CDP_ENDPOINT")
    os.environ["AVITO_CDP_ENDPOINT"] = endpoint
    try:
        args = _quiet(browser_bridge.parse_args, browser_bridge.build_parser("x"),
                      ["--url", LISTING], cdp_supported=False)
        eq("an endpoint from the environment is dropped", args.cdp_endpoint, None)
        args = _quiet(browser_bridge.parse_args, browser_bridge.build_parser("x"),
                      ["--url", LISTING])
        check("and still used by an engine that can", bool(args.cdp_endpoint))
    finally:
        if saved is None:
            os.environ.pop("AVITO_CDP_ENDPOINT", None)
        else:
            os.environ["AVITO_CDP_ENDPOINT"] = saved
    body = open(os.path.join(HERE, "selenium_scraper.py"), encoding="utf-8").read()
    check("selenium_scraper declares it cannot use CDP", "cdp_supported=False" in body)


def test_no_private_citations_in_shipped_files():
    """Public files must state their reasons in their own words. A pointer to
    an internal document or a bare section number is unreachable to a reader.
    The tokens are assembled so this file does not match itself."""
    import re
    tokens = ["CLAUDE" + ".md", "claude_" + "2scraper", "2scraper_repo" + "_rules",
              "play" + "book " + "§", "template " + "§"]
    section = re.compile("§" + r"\s*\d")
    hits = []
    # In a git checkout, "shipped" means what git could commit: tracked files
    # and untracked ones that are not ignored. Walking the whole directory
    # flagged a contributor's own notes file, excluded through
    # .git/info/exclude, and kept the suite red on a clean tree.
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=HERE, capture_output=True, text=True, timeout=30)
        shippable = {n for n in listed.stdout.split("\0") if n} if listed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        shippable = None
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")
                   and not os.path.isfile(os.path.join(root, d, "pyvenv.cfg"))]
        for name in files:
            if not name.endswith((".py", ".md", ".yml", ".yaml", ".toml", ".txt",
                                  ".cfg", ".example", ".json", ".csv", ".html")) \
                    and name not in ("Dockerfile", ".gitignore", ".dockerignore",
                                     "pre-commit", "pre-push"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, HERE)
            if rel.split(os.sep)[0] in ("live", "build", "dist"):
                continue
            if shippable is not None and rel.replace(os.sep, "/") not in shippable:
                continue
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if any(t in text for t in tokens) or section.search(text):
                hits.append(rel)
    eq("no private citations in shipped files", sorted(hits), [])


def test_paid_solves_are_capped_per_page_across_rotations():
    """One page that stays walled used to buy one solve PER EXIT: 2 with the
    default --proxy-block-retries 1, 4 with 3. The allowance is per page,
    checked before each purchase, and a rotation does not reset it."""
    import browser_bridge
    import page_flow
    import captcha_solver
    real = captcha_solver.solve_geetest_v4
    calls = []

    def fake_solve(key, cid, url, proxy=None, **kw):
        calls.append(proxy)
        return {"captcha_id": cid, "lot_number": "l", "pass_token": "p", "gen_time": "1",
                "captcha_output": "o", "_cost": "0.00299"}
    captcha_solver.solve_geetest_v4 = fake_solve
    try:
        d = FakeDriver([fixture("wall_captcha.html")] * 10)
        args = _args(proxy=GATEWAY_PROXY.replace("region-RU", "region-RU-session-abcdefghi"),
                     proxy_block_retries=3, twocaptcha_key="k")
        args.solver_proxy = args.proxy
        with tempfile.TemporaryDirectory() as tmp:
            args.out = os.path.join(tmp, "o")
            rc = _quiet(browser_bridge.run, args, d)
            facts = json.load(open(os.path.join(tmp, "o.latest_attempt.meta.json")))[
                "transport_facts"]
    finally:
        captcha_solver.solve_geetest_v4 = real
    eq("a walled page exits 3", rc, 3)
    check("the run did rotate exits", facts["rotations"] >= 1, "(%s)" % facts["rotations"])
    eq("one paid solve for one page, whatever the rotations", len(calls),
       page_flow.SOLVES_PER_PAGE)
    check("the refused purchases are counted", facts.get("solves_refused_by_budget", 0) >= 1)
    f = {}
    check("another page has its own allowance",
          browser_bridge._spend_solve(f, LISTING) and browser_bridge._spend_solve(f, LISTING + "?p=2"))
    check("and the same page does not get a second one", not browser_bridge._spend_solve(f, LISTING))


def test_a_captcha_after_the_proof_of_work_is_solved_on_the_same_exit():
    """Manual test 2026-10-19, step 42: the proof-of-work went, the next page
    was not recognisable yet, one more wait showed the captcha — and the run
    rotated to a fresh exit without solving it, because the solve rung sits
    before that wait. Twice out of three exits."""
    import browser_bridge
    import captcha_solver
    wall = fixture("wall_captcha.html")
    challenge = re.sub(r'<form[^>]*js-firewall-form.*?</form>', "", wall, flags=re.S)
    loading = "<html><head><title>avito</title></head><body></body></html>"

    class Unfolding(FakeDriver):
        """Each wait shows the next document: challenge -> loading -> captcha."""
        def __init__(self):
            super().__init__([challenge], after_js=fixture("listing_dom.html"))
            self.timeline = [loading, wall]

        def sleep(self, ms):
            if self.timeline and self.current in (challenge, loading):
                self.current = self.timeline.pop(0)

    real = captcha_solver.solve_geetest_v4
    calls = []

    def fake_solve(key, cid, url, proxy=None, **kw):
        calls.append(proxy)
        return {"captcha_id": cid, "lot_number": "l", "pass_token": "p", "gen_time": "1",
                "captcha_output": "o", "_cost": "0.00299"}
    captcha_solver.solve_geetest_v4 = fake_solve
    try:
        args = _args(url=[LISTING + "?p=2"],
                     proxy=GATEWAY_PROXY.replace("region-RU", "region-RU-session-abcdefghi"),
                     twocaptcha_key="k")
        args.solver_proxy = args.proxy
        with tempfile.TemporaryDirectory() as tmp:
            args.out = os.path.join(tmp, "o")
            rc = _quiet(browser_bridge.run, args, Unfolding())
            facts = json.load(open(os.path.join(tmp, "o.latest_attempt.meta.json")))[
                "transport_facts"]
    finally:
        captcha_solver.solve_geetest_v4 = real
    eq("the late captcha was solved on the same exit", len(calls), 1)
    eq("no rotation for it", facts.get("rotations", 0), 0)
    eq("and the page came back", rc, 0)


def test_profile_locked_at_connect_is_retried_and_nothing_else_is():
    """A run started right after another on the same Scraping Browser profile
    got `500 profile_locked` and exited 5; 30 s later the same step passed
    (manual test 2026-10-20, steps 17 and 24). Retry THAT answer briefly, and
    only that: a timeout or proxy_error is not a lock and must not be
    retried into a held profile."""
    import browser_bridge
    import page_flow

    class Locked(FakeDriver):
        def __init__(self, failures, message):
            super().__init__([fixture("listing_dom.html")])
            self.failures, self.message = failures, message

        def start(self):
            self.starts += 1
            if self.failures:
                self.failures -= 1
                raise RuntimeError(self.message)

    saved = page_flow.PROFILE_LOCKED_RETRY_DELAY_S if hasattr(page_flow, "PROFILE_LOCKED_RETRY_DELAY_S") else None
    page_flow.PROFILE_LOCKED_RETRY_DELAY_S = 0
    locked = ("BrowserType.connect_over_cdp: WebSocket error: ws://***:***@cb.2captcha.com:9222/ "
              "500 Internal Server Error\nprofile_locked\n")
    proxy = locked.replace("profile_locked", "proxy_error")
    try:
        for name, failures, message, want_rc, want_starts in (
                ("locked twice, then free", 2, locked, 0, 3),
                ("locked for good", 99, locked, 5, 1 + page_flow.PROFILE_LOCKED_RETRIES),
                ("proxy_error is not retried", 1, proxy, 5, 1),
                ("a timeout is not retried", 1, "TimeoutError: connect timed out", 5, 1)):
            d = Locked(failures, message)
            with tempfile.TemporaryDirectory() as tmp:
                rc = _quiet(browser_bridge.run, _args(out=os.path.join(tmp, "o")), d)
            eq("%s: exit" % name, rc, want_rc)
            eq("%s: connection attempts" % name, d.starts, want_starts)
    finally:
        if saved is not None:
            page_flow.PROFILE_LOCKED_RETRY_DELAY_S = saved


def test_a_dropped_connection_after_collected_pages_keeps_them():
    """Live, 2026-10-20 (pyppeteer over the Scraping Browser): page 1 gave 50
    rows, page 2 died with ERR_CONNECTION_RESET/CLOSED — and the run exited 5
    with products 0 and no file, throwing the 50 rows away. Rows gathered and
    THEN a failure is a partial run: exit 6, the rows written. A failure before
    any row is still 5."""
    import browser_bridge
    reset = RuntimeError("Page.goto: net::ERR_CONNECTION_RESET at " + LISTING)
    cases = (
        ("listing, drop on page 2", dict(url=[LISTING], pages=3), [fixture("listing_dom.html"), reset], 6, 2),
        ("items, drop on item 2", dict(mode="item", url=[ITEM_COMPANY, ITEM_PRIVATE]),
         [fixture("item_company.html"), reset], 6, 2),
        ("listing, drop on page 1", dict(url=[LISTING], pages=3), [reset], 5, 1),
    )
    for name, kw, pages, want, failed_at in cases:
        with tempfile.TemporaryDirectory() as tmp:
            args = _args(out=os.path.join(tmp, "o"), **kw)
            rc = _quiet(browser_bridge.run, args, FakeDriver(pages))
            meta = json.load(open(os.path.join(tmp, "o.latest_attempt.meta.json")))
            eq("%s: exit code" % name, rc, want)
            eq("%s: sidecar exit_code" % name, meta.get("exit_code"), want)
            if want == 6:
                rows = json.load(open(os.path.join(tmp, "o.json")))
                check("%s: the gathered rows are written" % name, len(rows) >= 1, "(%d rows)" % len(rows))
                eq("%s: sidecar products" % name, meta.get("products"), len(rows))
                eq("%s: status" % name, meta.get("status"), "partial")
                check("%s: the failed page is named" % name,
                      failed_at in (meta.get("pages_failed") or []), "(%s)" % meta.get("pages_failed"))
                check("%s: the transport error is kept" % name,
                      "ERR_CONNECTION_RESET" in json.dumps(meta.get("transport_facts")))
            else:
                check("%s: nothing written" % name, not os.path.exists(os.path.join(tmp, "o.json")))


def test_canary_cannot_go_green_without_scraping():
    """The canary took a green SKIP branch every day for a week: a badge that
    said nothing about the site. Missing credentials and a non-zero live run
    must both fail the job, and the data check must not lean on a
    merchandising share."""
    path = os.path.join(HERE, ".github", "workflows", "canary.yml")
    if not os.path.isdir(os.path.join(HERE, ".github")):
        skip("canary shape (.github absent)")
        return
    body = open(path, encoding="utf-8").read()
    for name in ("TWOCAPTCHA_KEY", "AVITO_PROXY", "AVITO_BROWSER_ACCOUNT_ID"):
        check("a missing %s fails the canary by name" % name,
              re.search(r"::error::[^\n]*" + name, body) is not None)
    check("no live step is skipped on missing credentials",
          "env.READY" not in body and "SKIPPED" not in body)
    check("a non-zero live run fails the canary",
          'if [ "$rc" != "0" ]' in body and "exit 1" in body)
    checks = open(os.path.join(HERE, ".github", "ci_checks.py"), encoding="utf-8").read()
    check("the run check requires sku/url/title on every row",
          '("sku", "url", "title")' in checks)
    check("and no longer a seller-name share", "rows name a seller" not in checks)


def test_canary_endpoint_lives_in_a_tool_and_a_dropped_run_is_retried_once():
    """canary.yml carried 30 lines of Python in a heredoc — nothing could run
    or test it locally (a family rule: workflows call shipped code). It is
    `browser_profile_client.py canary-env` now. And a run that exits 5 (the
    Scraping Browser refused, or a page load died — 2026-10-20, first run
    after the secret was fixed) gets ONE retry on a fresh proxy session; a
    block (3) or no rows (4) does not."""
    import importlib.util
    import base64 as b64
    spec = importlib.util.spec_from_file_location(
        "_bpc_canary", os.path.join(HERE, "tools", "browser_profile_client.py"))
    bpc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bpc)
    given = ("ws://bc" + "1234-zo" + "ne-scraping_browser-country-us-pid-pabc" + ":" + "EXAMPLEPW"
             + "@cb.2captcha.com:9222")
    proxy1 = "http://" + "u1234-zone-custom-region-RU-session-AAAAAAAAA-sessTime-60:EXAMPLEPW" \
             + "@eu.proxy.rucaptcha.com:2334"
    proxy2 = proxy1.replace("AAAAAAAAA", "BBBBBBBBB")
    uri = bpc.repoint_endpoint(given, proxy1)
    from urllib.parse import urlsplit, unquote
    user = unquote(urlsplit(uri).username)
    check("repoint: country forced to ru", "-country-ru-" in user and "-country-us" not in user, user)
    enc = user.split("-proxy-", 1)[1]
    eq("repoint: -proxy- is base64url of the minted proxy",
       b64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)).decode(), proxy1)
    check("repoint: password and host kept", urlsplit(uri).password == "EXAMPLEPW"
          and urlsplit(uri).hostname == "cb.2captcha.com" and urlsplit(uri).port == 9222)
    again = unquote(urlsplit(bpc.repoint_endpoint(uri, proxy2)).username)
    eq("repoint: re-pointing replaces the proxy segment, never stacks it", again.count("-proxy-"), 1)
    check("repoint: and carries the NEW session", "BBBBBBBBB" in b64.urlsafe_b64decode(
        again.split("-proxy-", 1)[1] + "==").decode())
    bare = bpc.repoint_endpoint(given.replace("-country-us", ""), proxy1)
    check("repoint: a login without -country- gets one after the zone",
          "-zo" "ne-scraping_browser-country-ru-" in unquote(urlsplit(bare).username))
    try:
        bpc.repoint_endpoint("ws://" + "nologin:pw" + "@cb.2captcha.com:9222", proxy1)
        check("repoint: a login without -zone- is refused", False)
    except ValueError:
        check("repoint: a login without -zone- is refused", True)

    path = os.path.join(HERE, ".github", "workflows", "canary.yml")
    if not os.path.isdir(os.path.join(HERE, ".github")):
        skip("canary shape (.github absent)")
        return
    body = open(path, encoding="utf-8").read()
    check("canary.yml has no inline Python heredoc", "python3 - <<" not in body)
    check("canary.yml re-points the endpoint with the shipped tool",
          "browser_profile_client.py canary-env" in body)
    check("a live run that exits 5 is retried once", 'if [ "$rc" = "5" ]' in body)
    check("nothing else is retried", 'if [ "$rc" != "0" ]; then' not in body.split("name: Live run")[1]
          .split("name: Assert")[0])


def test_the_canary_run_check_passes_a_good_run():
    # The canary's run check only executes after a successful live scrape, so
    # a crash in its success path stays hidden until the first green run: on
    # 2026-10-16 it died with a NameError on a 150-row run that had worked.
    # Run it here on a synthetic good run, and on a broken one.
    import importlib.util
    import tempfile
    from output_writer import SOURCE_DEFAULT
    spec = importlib.util.spec_from_file_location(
        "ci_checks_under_test", os.path.join(HERE, ".github", "ci_checks.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = [{"sku": str(1000 + i), "url": "https://www.avito.ru/moskva/x/%d" % i,
             "title": "item %d" % i, "price": 100 + i, "original_price": None,
             "region_slug": "moskva", "page": 1 + i // 50, "position": i % 50}
            for i in range(150)]
    meta = {"status": "complete", "pages_completed": 3, "source": SOURCE_DEFAULT,
            "stop_reason": "max_pages"}
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "canary")
        with open(prefix + ".json", "w", encoding="utf-8") as f:
            json.dump(rows, f)
        with open(prefix + ".meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f)
        try:
            failed = mod.run_check(prefix)
        except Exception as exc:  # noqa: BLE001
            failed = ["raised %s: %s" % (type(exc).__name__, exc)]
        check("the canary run check passes a good 150-row run", failed == [],
              "(got %r)" % (failed,))
        rows[0]["price"] = None
        rows[1]["sku"] = ""
        with open(prefix + ".json", "w", encoding="utf-8") as f:
            json.dump(rows, f)
        failed = mod.run_check(prefix)
        check("and fails one with a row missing its sku",
              any("no sku" in x for x in failed), "(got %r)" % (failed,))


def test_the_wait_log_says_what_it_saw():
    """Manual test 2026-10-19, step 39: "proof-of-work cleared after 1s" was
    logged for a page that was merely not the challenge any more — the next
    line was "nothing recognisable yet" — and the final "page 1: blocked
    (HTTP 439, ...)" quoted the FIRST response's status, not the page the run
    stopped on."""
    import browser_bridge
    import product_parser as P
    wall = fixture("wall_captcha.html")
    challenge = re.sub(r'<form[^>]*js-firewall-form.*?</form>', "", wall, flags=re.S)
    loading = "<html><head><title>avito</title></head><body></body></html>"

    class Pages:
        def __init__(self, docs):
            self.docs = list(docs)

        def content(self):
            return self.docs.pop(0) if len(self.docs) > 1 else self.docs[0]

        def sleep(self, ms):
            pass

    for docs, cleared in (([challenge, loading], False),
                          ([challenge, fixture("listing_dom.html")], True)):
        lines = []
        browser_bridge.wait_out_challenge(Pages(docs), LISTING + "?p=2", 5000, log=lines.append)
        said = " | ".join(lines)
        state = P.detect_page_state(docs[-1], url=LISTING + "?p=2")
        check("wait log after the challenge (%s): %s" % (state, "cleared" if cleared else "not cleared"),
              ("cleared" in said) == cleared, "(log %r)" % said)

    src = inspect.getsource(browser_bridge)
    for stop_line in ('"page %d: %s (%d bytes; first response HTTP %s)"',
                      '"item %d: %s (first response HTTP %s)'):
        check("stop line names the FIRST response's status as such: %s" % stop_line[:20],
              stop_line in src)
    check("no stop line prints a bare status", '"page %d: %s (HTTP %s' not in src
          and '"item %d: %s (HTTP %s)' not in src)


def test_accounts_lists_accounts_only():
    """`accounts` printed every dict carrying an "id" — each account's
    project (id=1, seven times), proxy account and default profile — so the
    one number to pass as --account-id was row 17 of 21 with nothing saying
    which (manual test 2026-10-19, step 13). The shape is GET
    /browser/accounts as measured that day; values are invented."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_bpc_accounts", os.path.join(HERE, "tools", "browser_profile_client.py"))
    bpc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bpc)

    def account(acc_id, name, proxy_id, profile_id, count):
        return {"id": acc_id, "name": name, "login": "bc" + "0000" + str(acc_id),
                "password": "x" * 12, "country": "ru", "profilesCount": count,
                "defaultProfileId": profile_id, "proxyAccountId": proxy_id,
                "project": {"id": 1, "code": "2captcha", "name": "2captcha"},
                "proxy": {"id": proxy_id, "login": "u" + "abcd" + "123", "password": "y" * 10},
                "profile": {"id": profile_id, "name": "Default profile", "accountId": acc_id,
                            "project": {"id": 1, "name": "2captcha"}}}
    data = {"status": 1, "count": 2, "project": {"id": 1, "name": "2captcha"},
            "data": {"0": account(192603, "ru-ticket-acc", 2107, 1457373, 3),
                     "1": account(148116, "testing", 2020, 1169204, 1)}}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        bpc.print_accounts(data)
    text = out.getvalue()
    for acc_id in ("192603", "148116"):
        check("accounts: lists account %s" % acc_id, acc_id in text, text)
    for other in ("2107", "2020", "1457373", "1169204"):
        check("accounts: no proxy-account/profile id %s on its own row" % other,
              not re.search(r"(?m)^\s*(?:account\s*)?id[=:\s]+%s\b" % other, text), text)
    eq("accounts: one row per account", len(re.findall(r"(?m)^\s+\d+\s", text)), 2)
    check("accounts: says which number is --account-id", "--account-id" in text, text)
    check("accounts: no credential", "x" * 12 not in text and "y" * 10 not in text, text)


def test_gateway_hints_name_every_accepted_host():
    # The owner's gateway is eu.proxy.rucaptcha.com; the hint named only
    # .proxy.2captcha.com (manual test 2026-10-19, step 36).
    import proxy_pool
    messages = []
    for call in (lambda: proxy_pool.require_gateway_login("http://eu.proxy.rucaptcha.com:2334"),
                 lambda: proxy_pool.mint_sessions("http://user1234:pass5678@proxy.example.com:8080", 1)):
        try:
            call()
            check("gateway hint raised", False)
        except proxy_pool.ProxyError as exc:
            messages.append(str(exc))
    for msg in messages:
        for suffix in proxy_pool._GATEWAY_SUFFIXES:
            check("hint names %s: %s…" % (suffix, msg[:30]), suffix in msg, "(got %r)" % msg)


def test_wrong_mode_refusal_reads_as_english():
    # "that is a listing page, not a item page" (manual test 2026-10-19,
    # step 34): the second article was hard-coded.
    import browser_bridge
    for urls, mode, want in (([LISTING], "item", "not an item page"),
                             ([ITEM_COMPANY], "listing", "that is an item page, not a listing page"),
                             ([SELLER], "listing", "not a listing page")):
        try:
            browser_bridge._check_urls(urls, mode)
            check("refused --mode %s" % mode, False)
        except SystemExit as exc:
            check("refusal wording for --mode %s: %r" % (mode, want), want in str(exc),
                  "(got %r)" % str(exc))
            check("no 'a item' in --mode %s refusal" % mode, " a item " not in str(exc),
                  "(got %r)" % str(exc))


def test_env_report_hides_every_credential_form():
    # `python3 env_config.py` hid a value only when it held "@", so the
    # dashboard's host:port:login:password proxy (no "@") was printed whole,
    # password included (manual test 2026-10-19, step 12).
    import env_config
    login, password = "EXAMPLE" + "LOGIN-zone-custom", "EXAMPLE" + "PASSWORD99"
    forms = {
        "AVITO_PROXY": ["eu.proxy.rucaptcha.com:2334:%s:%s" % (login, password),
                        "http://eu.proxy.rucaptcha.com:2334:%s:%s" % (login, password),
                        "http://%s:%s@eu.proxy.rucaptcha.com:2334" % (login, password)],
        "AVITO_CDP_ENDPOINT": ["ws://%s:%s@cb.2captcha.com:9222" % (login, password)],
        "TWOCAPTCHA_KEY": [password],
    }
    for name, values in forms.items():
        for value in values:
            shown = env_config.describe(name, value)
            check("env report hides the password of %s (%s…)" % (name, value[:12]),
                  password not in shown, "(got %r)" % shown)
            check("env report hides the login of %s (%s…)" % (name, value[:12]),
                  login not in shown, "(got %r)" % shown)
    proxy = env_config.describe("AVITO_PROXY", forms["AVITO_PROXY"][0])
    check("env report keeps the proxy's host and port", "eu.proxy.rucaptcha.com:2334" in proxy,
          "(got %r)" % proxy)
    eq("env report shows a non-secret value", env_config.describe("AVITO_URL", "https://www.avito.ru/x"),
       "set (https://www.avito.ru/x)")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            FAILURES.append("%s raised %s: %s" % (test.__name__, type(exc).__name__, exc))
            print("FAIL %s raised %s: %s" % (test.__name__, type(exc).__name__, exc))
    print("\n%d checks, %d failed, %d skipped"
          % (len(PASSES) + len(FAILURES), len(FAILURES), len(SKIPS)))
    if SKIPS:
        print("skipped: %s" % ", ".join(SKIPS))
    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print("  " + failure)
        return 1
    print("smoke_test: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
