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
import dataclasses
import inspect
import io
import json
import os
import re
import socket
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
              "AVITO_PROXY=http://uabc123def456-zone-custom-region-RU"
              ":uabc123def456@eu.proxy.rucaptcha.com:2334\n"
              "AVITO_CDP_ENDPOINT=ws://bc9876fedcba-zone-scraping_browser"
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


def test_fixtures_carry_no_session_or_personal_material():
    """Guarded with PATTERNS, so the NEXT capture is caught too (§10)."""
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


def test_schema_version_is_per_mode():
    """P2-1: the fingerprint covered Product's field names only."""
    import output_writer as O
    versions = {m: O.schema_version(c) for m, c in O.ROW_CLASS_BY_MODE.items()}
    eq("three modes, three schema versions", len(set(versions.values())), 3)
    eq("scope carries the MODE's version", O.scope_fingerprint(LISTING, "item", 1)["schema_version"],
       versions["item"])


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
