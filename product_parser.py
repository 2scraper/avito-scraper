"""
product_parser.py
-----------------
Everything this repo knows about avito.ru's markup lives here (2scraper family rule).
The engines and `browser_bridge.py` decide WHEN to fetch; this module decides
WHAT a fetched page says.

Three page kinds, three sources — measured 2026-10-06
-----------------------------------------------------
    listing   category / search. No JSON-LD. TWO renderings of the same URL:
              * the ordinary one: the grid is in the DOM only —
                `[data-marker="item"][data-item-id]`, 50 per page, each with
                schema.org microdata (`itemprop` price / priceCurrency /
                name / url / ratingValue);
              * the one served right after the firewall's proof-of-work: the
                same 50 cards PLUS a `<script type="mime/invalid"
                data-mfe-state>` JSON whose `catalog.items` carry timestamps,
                the old price of a discounted item and the seller type.
              Both are read; the JSON wins where it exists, and
              `price_source` says which one a row came from.
    item      `window.__staticRouterHydrationData = JSON.parse("…")` →
              `loaderData["catalog-or-main-or-item"].buyerItem`.
    seller    `/brands/<slug-or-hash>`: DOM only. The rendered page holds the
              profile and its first 15 listings.

Price — the trap on this site
-----------------------------
Avito's bare `price` fields are the PRE-discount price. On item 8245354778
the card showed 62 990 ₽ while `priceDetailed.value` (listing JSON) and
`item.price` (item page) both said 69 989. The displayed price is
`iva.PriceStep[price].priceDetailed.value` in the listing JSON,
`item.formattedPrice.value` on the item page and the `itemprop="price"`
microdata in the DOM — all three agreed on 50 of 50 cards. `original_price`
is only ever filled from the site's own "old" figure, never computed.

Private sellers are anonymised by Avito
---------------------------------------
An item page shows a private person as «Пользователь» with `userId 0`, and
their listing card carries no profile link. That is a property of the site,
not a parser gap: `seller_name` is None for them, never the placeholder word.

Never collected
---------------
The item page carries a masked phone («8 958 XXX-XX-XX») and some categories
put a masked IMEI among the parameters. Neither is read; `IMEI` is dropped
from `params` by name.
"""

from __future__ import annotations

import html as _html
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from output_writer import Item, Product, Seller

BASE = "https://www.avito.ru"
HOSTS = ("www.avito.ru", "avito.ru")

# /moskva/telefony/iphone_17_256_gb_sim_esim_8245354778 — the id is the
# trailing digit run of the last path segment. Measured on every item URL in
# the 50-card capture; ids were 10 digits, the floor here is generous.
_ITEM_PATH_RE = re.compile(r"^/[^/]+/[^/]+/[^/?#]*?_(\d{6,})/?$")
_SELLER_PATH_RE = re.compile(r"^/(brands|user)/([^/?#]+)")

# The firewall. Its title is the one string every variant shares (HTTP 429
# with a captcha, HTTP 439 with a proof-of-work), and no served page carries
# it as a title.
WALL_TITLE = "Доступ ограничен"
# The GeeTest v4 id is hard-coded in the wall's inline script:
#     const captchaId = '2d9c743cf7d63dbc9db578a608196bcd'
_GEETEST_ID_RE = re.compile(r"captchaId\s*=\s*['\"]([0-9a-f]{32})['\"]")

_FIREWALL_FORM_RE = re.compile(r"<form\b[^>]*\bjs-firewall-form\b", re.I)
WALL_WAIT_TEXT = "подождите немного и обновите страницу"

PRIVATE_SELLER_PLACEHOLDER = "Пользователь"

# Never exported, even masked (module docstring).
_DROP_PARAMS = ("IMEI",)


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def supported_host(url: str) -> Tuple[bool, str]:
    host = (urlparse(url).hostname or "").lower()
    if host in HOSTS:
        return True, ""
    if host.endswith("avito.ru"):
        return False, ("%s is an Avito host this repo has not measured — only "
                       "www.avito.ru was captured" % host)
    return False, "%s is not avito.ru" % (host or "<no host>")


def page_kind(url: str) -> str:
    """`item`, `seller` or `listing`, from the path alone."""
    path = urlparse(url).path
    if _ITEM_PATH_RE.match(path):
        return "item"
    if _SELLER_PATH_RE.match(path):
        return "seller"
    return "listing"


def item_id_from_url(url: str) -> Optional[str]:
    m = _ITEM_PATH_RE.match(urlparse(url).path)
    return m.group(1) if m else None


def seller_id_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = _SELLER_PATH_RE.match(urlparse(url).path)
    return m.group(2) if m else None


def region_slug(url: Optional[str]) -> Optional[str]:
    """`moskva` from `/moskva/telefony/..._8245354778`, item URLs only."""
    if not url:
        return None
    path = urlparse(url).path
    return path.split("/")[1] if _ITEM_PATH_RE.match(path) else None


def strip_query(url: Optional[str]) -> Optional[str]:
    """Absolute URL without query or fragment.

    Avito decorates its own links with tracking (`?context=H4sI…`,
    `?src=search_seller_info&iid=…`) that changes per page view, so a row's
    URL is the canonical path only — two runs must produce the same string
    for the same item.
    """
    if not url:
        return None
    absolute = urljoin(BASE, url)
    parts = urlparse(absolute)
    return urlunparse(parts._replace(query="", fragment=""))


def page_url(url: str, page: int) -> str:
    """Page N of a listing: `?p=N`, replacing any existing `p`.

    Measured: page 2 fetched as plain `?p=2` returned 50 new ids. The site's
    own next-link adds a `context=` token; it is not needed and is dropped
    from what we build. Page 1 is the URL without `p`, which is what the
    site's own pager links to.
    """
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in ("p", "context")]
    if page > 1:
        query.append(("p", str(page)))
    return urlunparse(parts._replace(query=urlencode(query), fragment=""))


# ---------------------------------------------------------------------------
# robots.txt — from the snapshot committed beside this file
# ---------------------------------------------------------------------------

_ROBOTS: Optional[List[Tuple[str, str]]] = None


def _robots_rules() -> List[Tuple[str, str]]:
    """(directive, pattern) pairs of the `User-agent: *` group."""
    global _ROBOTS
    if _ROBOTS is not None:
        return _ROBOTS
    from robots_snapshot import ROBOTS_TXT
    rules: List[Tuple[str, str]] = []
    in_star = False
    for raw in ROBOTS_TXT.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            in_star = value == "*"
        elif in_star and key in ("allow", "disallow") and value:
            rules.append((key, value))
    # FAIL CLOSED. The first release read the snapshot from a file the wheel
    # did not ship, got zero rules and allowed every URL. An empty rule set
    # is never a valid reading of avito.ru's robots.txt.
    if not any(d == "disallow" for d, _ in rules):
        raise RuntimeError("robots snapshot yielded no Disallow rules — refusing "
                           "to treat every URL as allowed")
    _ROBOTS = rules
    return rules


def _robots_regex(pattern: str):
    anchored = pattern.endswith("$")
    body = re.escape(pattern.rstrip("$")).replace(r"\*", ".*")
    return re.compile("^" + body + ("$" if anchored else ""))


def robots_verdict(url: str) -> Tuple[bool, Optional[str]]:
    """(allowed, matching rule). Longest match wins, Allow wins a tie."""
    parts = urlparse(url)
    target = parts.path + (("?" + parts.query) if parts.query else "")
    best: Optional[Tuple[int, int, str, str]] = None
    for directive, pattern in _robots_rules():
        if _robots_regex(pattern).match(target):
            rank = (len(pattern), 1 if directive == "allow" else 0, directive, pattern)
            if best is None or rank[:2] > best[:2]:
                best = rank
    if best is None or best[2] == "allow":
        return True, best[3] if best else None
    return False, "Disallow: %s" % best[3]


def is_robots_allowed(url: str) -> bool:
    return robots_verdict(url)[0]


# ---------------------------------------------------------------------------
# Page state
# ---------------------------------------------------------------------------

def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    return _html.unescape(m.group(1)).strip() if m else ""


def is_wall(html: str) -> bool:
    return WALL_TITLE in _title(html)


def geetest_captcha_id(html: str) -> Optional[str]:
    """The wall's GeeTest v4 id, or None. Only meaningful on a wall page."""
    m = _GEETEST_ID_RE.search(html or "")
    return m.group(1) if m else None


def results_cutoff(html: str) -> Optional[int]:
    """Where the search RESULTS end, or None when the page has no such line.

    Measured 2026-10-06: a search with no matches (`?q=zqxjvzqxjvzqxjv`) is
    served as HTTP 200 with FIFTY cards — every one under a heading
    «Похожие объявления рядом и в других городах» (an `extraTitle-…` block),
    i.e. Avito's substitutes, not results. Counted as results they would be
    a complete-looking run about the wrong question. No such heading on any
    of the three ordinary listings captured. Cards after this offset are not
    results.
    """
    html = html or ""
    hits = [i for i in (html.find("extraTitle-"), html.find("Похожие объявления")) if i >= 0]
    return min(hits) if hits else None


_WALL_BLOCKS = (("geetest", "#geetest_captcha"), ("image", "#inner-captcha"),
                ("hcaptcha", "#h-captcha"))


def wall_variant(html: str) -> Optional[str]:
    """Which of the firewall's three captchas the page is SHOWING:
    `geetest`, `image` (Avito's own text-from-picture captcha) or `hcaptcha`.

    The server picks one per request (`/web/5/firewallCaptcha/get`) and the
    page's script un-hides that block, so the visible one is the answer — an
    inline `display: none` on the other two. Every measured request so far was
    `geetest`; the other two are in the wall's code and are recognised from
    its markup, not from a live capture.
    """
    if not is_wall(html) or not _FIREWALL_FORM_RE.search(html or ""):
        return None
    soup = BeautifulSoup(html, "html.parser")
    for name, selector in _WALL_BLOCKS:
        node = soup.select_one(selector)
        if node is None:
            continue
        style = re.sub(r"\s+", "", (node.get("style") or "").lower())
        if "display:none" not in style:
            if name == "geetest" and not geetest_captcha_id(html):
                continue
            return name
    return None


def wall_hcaptcha_sitekey(html: str) -> Optional[str]:
    """The wall's hCaptcha sitekey: `data-sitekey` of its `.h-captcha`
    widget (measured: 070db171-ddb9-4c93-b7f6-d25d3c9d7e28)."""
    m = re.search(r'class="h-captcha"[^>]*data-sitekey="([0-9a-f-]{36})"', html or "") or \
        re.search(r'data-sitekey="([0-9a-f-]{36})"', html or "")
    return m.group(1) if m else None


def wall_hcaptcha_rqdata(html: str) -> Optional[str]:
    """hCaptcha Enterprise's `rqdata`, when the wall carries one. Avito's did
    not on any capture; kept so an Enterprise switch is not silently missed."""
    m = re.search(r'["\']?rqdata["\']?\s*[:=]\s*["\']([^"\']{8,})["\']', html or "")
    return m.group(1) if m else None


def wall_image_src(html: str) -> Optional[str]:
    """The image captcha's picture: `src` of `.js-form-captcha-image`, which
    the wall's script sets from the server's answer (a `data:` URL)."""
    soup = BeautifulSoup(html or "", "html.parser")
    img = soup.select_one("#inner-captcha .js-form-captcha-image") or soup.select_one("#inner-captcha img")
    src = img.get("src") if img is not None else None
    return src or None


def _has_listing_cards(html: str) -> bool:
    html = html or ""
    if "item_list_with_filters/item(" in html:
        return True
    first = html.find('data-marker="item"')
    if first < 0:
        return False
    cutoff = results_cutoff(html)
    return cutoff is None or first < cutoff


def detect_page_state(html: str, status: Optional[int] = None,
                      url: Optional[str] = None) -> str:
    """What a fetched document is. See page_flow.STATE_POLICY for the policy.

        content    parse it
        captcha    the firewall with its captcha form (HTTP 429 + «Продолжить»)
        challenge  the firewall without one — the proof-of-work variant
                   (HTTP 439) that a browser clears by itself; wait
        empty      a served listing with no cards on it
        blocked    a refusal with no puzzle in it (403/451, or nothing at all)
        unknown    served, not yet painted, nothing recognisable

    Ordered by how much each signal PROVES (2scraper family rule): content markers
    first, the wall's own title next, and only then the structural guesses.
    Deliberately no `captcha` substring test: the Browser API extension
    injects `chrome-extension://…/captcha/*` scripts into every page it
    loads (2scraper family rule).
    """
    html = html or ""
    kind = page_kind(url) if url else None
    wall = is_wall(html)
    if not wall:
        if kind == "item" and "__staticRouterHydrationData" in html and "buyerItem" in html:
            return "content"
        if kind == "seller" and 'data-marker="profile' in html:
            return "content"
        if kind in (None, "listing") and _has_listing_cards(html):
            return "content"
    if wall:
        # The FORM element, not the substring: the wall's own script names
        # `.js-firewall-form` in a querySelector whether or not the form is
        # on the page.
        if wall_variant(html):
            return "captcha"
        # The third variant, measured 2026-10-08 right after an ACCEPTED
        # GeeTest solve: no form, no proof-of-work, only «подождите немного и
        # обновите страницу». Nothing clears it in place — a different exit
        # does. Waiting it out as a "challenge" reported the page blocked.
        if WALL_WAIT_TEXT in html:
            return "blocked"
        return "challenge"
    if status in (403, 451):
        return "blocked"
    if not html.strip():
        return "blocked" if status else "unknown"
    # A served avito page is built from its own static host; a browser error
    # page or an empty shell is not. Every captured served page references
    # avito.st dozens of times.
    served = html.count("avito.st") >= 5
    if served and kind in (None, "listing") and results_cutoff(html) is not None:
        return "empty"
    if served and kind in (None, "listing") and "pagination-button" not in html:
        return "empty"
    return "unknown"


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"\d[\d\s   ]*(?:[.,]\d+)?")


def parse_number(text: Any) -> Optional[float]:
    """`'62 990 ₽'` -> 62990.0, `'4,9'` -> 4.9. Rouble prices on this site
    carry no kopecks; a comma is the decimal point of a rating."""
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _NUM_RE.search(str(text))
    if not m:
        return None
    raw = re.sub(r"[\s   ]", "", m.group(0)).replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def count_before_word(text: Optional[str]) -> Optional[int]:
    """`'1 524 отзыва'` -> 1524. Reads the number BEFORE the word, never every
    digit in the string (2scraper family rule: a `review_count` once came out as
    445279961 by stripping every digit out of an aria-label)."""
    if not text:
        return None
    m = re.search(r"(\d[\d\s ]*?)\s*[^\d\s ]", str(text) + " ")
    if not m:
        return None
    return int(re.sub(r"\D", "", m.group(1)))


def _ms_to_iso(value: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _largest_image(images: Any) -> Optional[str]:
    """Avito image dicts are keyed by size (`"636x476"`); take the largest."""
    if not isinstance(images, dict) or not images:
        return None

    def area(key: str) -> int:
        m = re.match(r"(\d+)x(\d+)", key)
        return int(m.group(1)) * int(m.group(2)) if m else 0
    return images[max(images, key=area)]


def _html_to_text(fragment: Optional[str]) -> Optional[str]:
    if not fragment:
        return None
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment).replace("</p>", "</p>\n")
    text = BeautifulSoup(fragment, "html.parser").get_text()
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


# ---------------------------------------------------------------------------
# Embedded JSON
# ---------------------------------------------------------------------------

_DECODER = json.JSONDecoder()


def listing_state(html: str) -> Optional[dict]:
    """The `data-mfe-state` JSON's `catalog`, or None when this rendering
    has none (the ordinary one)."""
    m = re.search(r"<script[^>]*data-mfe-state[^>]*>(.*?)</script>", html or "", re.S)
    if not m:
        return None
    body = m.group(1).strip()
    for candidate in (body, _html.unescape(body)):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        catalog = (((data.get("loaderData") or {}).get("data") or {}).get("catalog")
                   if isinstance(data, dict) else None)
        if isinstance(catalog, dict) and isinstance(catalog.get("items"), list):
            return catalog
    return None


def hydration_data(html: str) -> Optional[dict]:
    """`window.__staticRouterHydrationData = JSON.parse("…")`, decoded.

    The payload is a JS string literal holding JSON, so it is decoded twice:
    the literal first (its escapes are JSON-compatible), then the JSON in it.
    """
    html = html or ""
    k = html.find("__staticRouterHydrationData")
    if k < 0:
        return None
    q = html.find('JSON.parse("', k)
    if q < 0:
        return None
    try:
        literal, _ = _DECODER.raw_decode(html, q + len("JSON.parse("))
        return json.loads(literal)
    except ValueError:
        return None


def buyer_item(html: str) -> Optional[dict]:
    data = hydration_data(html)
    if not data:
        return None
    for value in (data.get("loaderData") or {}).values():
        if isinstance(value, dict) and isinstance(value.get("buyerItem"), dict):
            return value["buyerItem"]
    return None


def _iva(item: dict, step: str) -> List[dict]:
    return [s.get("payload") or {} for s in ((item.get("iva") or {}).get(step) or [])
            if isinstance(s, dict)]


def _iva_component(item: dict, step: str, component: str) -> List[dict]:
    return [s.get("payload") or {} for s in ((item.get("iva") or {}).get(step) or [])
            if isinstance(s, dict)
            and (s.get("componentData") or {}).get("component") == component]


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def _seller_type(has_profile_link: bool, labelled_company: bool) -> Optional[str]:
    """`company`, `private` or None.

    A card WITHOUT a profile link is a private person (Avito anonymises them —
    module docstring). A card labelled «Компания» is a company. A card with a
    link but no label is NOT evidence of either: measured, `/brands/27696b…`
    has no label on its cards and is the shop «iStore | Техника Apple».
    """
    if labelled_company:
        return "company"
    if not has_profile_link:
        return "private"
    return None


def _geo_text(refs: Any) -> Optional[str]:
    parts = []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        piece = (ref.get("content") or "").strip()
        after = ((ref.get("afterWithIcon") or {}).get("text") or ref.get("after") or "").strip()
        if piece:
            parts.append(piece + (", " + after if after else ""))
    return "; ".join(parts) or None


def _row_from_state(item: dict, category: Optional[str]) -> Product:
    row = Product(price_source="state")
    row.sku = str(item["id"]) if item.get("id") is not None else None
    row.url = strip_query(item.get("urlPath")) or ""
    row.region_slug = region_slug(row.url)
    row.title = item.get("title")
    images = item.get("images") or []
    row.image_url = _largest_image(images[0]) if images else None
    row.currency = "RUB"

    shown = _iva_component(item, "PriceStep", "price")
    shown_price = (shown[0].get("priceDetailed") or {}).get("value") if shown else None
    # The top-level figure is the PRE-discount one (module docstring); it is
    # only a fallback for a card with no PriceStep, and none was seen in 50.
    row.price = parse_number(shown_price if shown_price is not None
                             else (item.get("priceDetailed") or {}).get("value"))
    if row.price is None:
        row.currency = None
    discount = _iva_component(item, "PriceStep", "discount")
    if discount:
        row.original_price = parse_number(discount[0].get("old_string"))
        row.discount_pct = parse_number(discount[0].get("percent"))

    row.category = category or (item.get("category") or {}).get("name")
    row.city = (item.get("location") or {}).get("name")
    geo = item.get("geo") or {}
    row.location = _geo_text(geo.get("geoReferences")) or geo.get("formattedAddress") or None
    row.published_at = _ms_to_iso(item.get("sortTimeStamp"))
    params = _iva(item, "AutoParamsStep")
    row.summary_params = (params[0].get("text") if params else None) or None

    info = _iva_component(item, "UserInfoStep", "seller-info")
    if info:
        profile = info[0].get("profile") or {}
        rating = info[0].get("rating") or {}
        row.seller_name = profile.get("title")
        row.seller_url = strip_query(profile.get("link"))
        row.seller_rating = parse_number(rating.get("score"))
        row.seller_reviews = count_before_word(rating.get("summary"))
    row.seller_id = seller_id_from_url(row.seller_url)
    second = [p.get("value") for p in _iva(item, "SecondLineStep")]
    row.seller_type = _seller_type(bool(row.seller_url), "Компания" in second)

    vas = [v for p in _iva_component(item, "DateInfoStep", "vas") for v in (p.get("vas") or [])]
    row.promoted = bool(vas)
    badges = [b.get("title") for p in _iva(item, "BadgeBarStep")
              for b in (p.get("badges") or []) if b.get("title")]
    badges += [b.get("title") for p in _iva_component(item, "UserInfoStep", "seller-badgebar")
               for b in (p.get("badges") or []) if b.get("title")]
    row.badges = badges or None
    row.delivery = bool((item.get("contacts") or {}).get("delivery"))
    return row


def _row_from_card(card, category: Optional[str]) -> Product:
    row = Product(price_source="dom")
    row.sku = card.get("data-item-id")
    link = next((a for a in card.select("a[href]")
                 if _ITEM_PATH_RE.match(urlparse(a["href"]).path)), None)
    row.url = strip_query(link["href"]) if link is not None else ""
    row.region_slug = region_slug(row.url)
    title = card.select_one('[data-marker="item-title"]') or card.select_one('[itemprop="name"]')
    row.title = title.get_text(" ", strip=True) if title is not None else None
    img = card.select_one('img[itemprop="image"]') or card.select_one("img[src]")
    row.image_url = img.get("src") if img is not None else None

    price = card.select_one('[itemprop="price"]')
    if price is not None and price.get("content"):
        row.price = parse_number(price["content"])
    else:
        node = card.select_one('[data-marker="item-price-value"], [data-marker="item-price"]')
        row.price = parse_number(node.get_text(" ", strip=True)) if node is not None else None
    cur = card.select_one('[itemprop="priceCurrency"]')
    row.currency = ((cur.get("content") if cur is not None else None) or "RUB") \
        if row.price is not None else None
    row.category = category

    # The city is the last part of the photo's alt text — `"iPhone 14, 128
    # ГБ, eSIM, Москва"` is title + ", " + city — and only when the alt really
    # is the title plus one more part.
    alt = img.get("alt") if img is not None else None
    if alt and row.title and alt.startswith(row.title + ", "):
        row.city = alt[len(row.title) + 2:].strip() or None
    loc = card.select_one('[data-marker="item-location"]')
    if loc is not None:
        text = re.sub(r"\s+", " ", loc.get_text(" ", strip=True)).replace(" ,", ",")
        row.location = text or None
    params = card.select_one('[data-marker="item-specific-params"]')
    if params is not None:
        row.summary_params = params.get_text(" ", strip=True).replace("⁠", "") or None
    date = card.select_one('[data-marker="item-date"]')
    if date is not None:
        row.published_text = date.get_text(" ", strip=True) or None

    seller_link = card.select_one('a[href*="/brands/"], a[href*="/user/"]')
    if seller_link is not None:
        row.seller_url = strip_query(seller_link["href"])
        name = seller_link.select_one("p")
        row.seller_name = name.get_text(" ", strip=True) if name is not None else None
    row.seller_id = seller_id_from_url(row.seller_url)
    score = card.select_one('[data-marker="seller-rating/score"]')
    row.seller_rating = parse_number(score.get_text(strip=True)) if score is not None else None
    summary = card.select_one('[data-marker="seller-info/summary"]')
    row.seller_reviews = (count_before_word(summary.get_text(" ", strip=True))
                          if summary is not None else None)
    row.seller_type = _seller_type(seller_link is not None, False)

    row.promoted = "Продвинуто" in card.get_text(" | ", strip=True)
    badges = [b.get_text(" ", strip=True) for b in card.select('[data-marker^="badge-title"]')]
    row.badges = [b for b in badges if b] or None
    return row


def parse_listing(html: str, url: str = "", category: Optional[str] = None) -> List[Product]:
    """Rows of one listing page, in page order, `position` 1..N.

    The embedded JSON is preferred when present because it carries what the
    DOM does not (old price, timestamp, seller type). The DOM path covers the
    ordinary rendering. A card the JSON does not know is still taken from the
    DOM, so a partial JSON never loses rows.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    cards = soup.select('[data-marker="item"][data-item-id]')
    cutoff = results_cutoff(html)
    if cutoff is not None:
        # Substitutes after «Похожие объявления…» are not results.
        results = set(re.findall(r'data-item-id="(\d+)"', (html or "")[:cutoff]))
        cards = [c for c in cards if c.get("data-item-id") in results]
    # Measured 2026-10-10 (`/all/odezhda_obuv_aksessuary?q=yeezy`): a
    # «Подобрали для вас» carousel sits INSIDE the results grid with 31 cards
    # of the same `data-marker="item"` shape — personal recommendations, none
    # of them in the state's `items` (they are its `extraBlockItems`). Taken
    # as results they made page 1 hold 82 rows, 32 of them unrelated.
    cards = [c for c in cards if c.find_parent(attrs={"data-marker": "itemsCarousel"}) is None]
    state = listing_state(html)
    by_id: Dict[str, dict] = {}
    order: List[str] = []
    if state:
        for item in state.get("items") or []:
            if isinstance(item, dict) and item.get("type", "item") == "item" and item.get("id"):
                by_id[str(item["id"])] = item
                order.append(str(item["id"]))

    rows: List[Product] = []
    seen = set()
    for card in cards:
        sku = card.get("data-item-id")
        if not sku or sku in seen:
            continue
        seen.add(sku)
        rows.append(_row_from_state(by_id[sku], category) if sku in by_id
                    else _row_from_card(card, category))
    # JSON items with no card: never observed, kept so a partly painted DOM
    # never silently drops rows the page did carry.
    for sku in order:
        if sku not in seen:
            seen.add(sku)
            rows.append(_row_from_state(by_id[sku], category))
    for i, row in enumerate(rows, 1):
        row.position = i
    return rows


def next_page_href(html: str) -> Optional[str]:
    """The site's own next-page link, absolute, or None on the last page."""
    soup = BeautifulSoup(html or "", "html.parser")
    node = soup.select_one('a[data-marker="pagination-button/nextPage"][href]')
    return urljoin(BASE, node["href"]) if node is not None else None


def total_pages(html: str) -> Optional[int]:
    pages = [int(n) for n in re.findall(r'data-marker="pagination-button/page\((\d+)\)"', html or "")]
    return max(pages) if pages else None


# ---------------------------------------------------------------------------
# Item
# ---------------------------------------------------------------------------

def parse_item(html: str, url: str = "", category: Optional[str] = None) -> Optional[Item]:
    bi = buyer_item(html)
    if not bi or not isinstance(bi.get("item"), dict):
        return None
    it = bi["item"]
    row = Item(price_source="state")
    row.sku = str(it["id"]) if it.get("id") is not None else item_id_from_url(url)
    row.url = strip_query(it.get("url") or url) or ""
    row.region_slug = region_slug(row.url)
    row.title = it.get("title")
    images = [_largest_image(i) for i in (it.get("imageUrls") or []) if isinstance(i, dict)]
    row.images = [i for i in images if i] or None
    row.image_url = row.images[0] if row.images else None

    fp = it.get("formattedPrice") or {}
    row.price = parse_number(fp.get("value")) if fp.get("isHasValue", True) else None
    row.currency = "RUB" if row.price is not None else None
    row.original_price = parse_number(fp.get("oldString")) if fp.get("oldString") else None
    row.discount_pct = parse_number(fp.get("saleDiscount")) if fp.get("saleDiscount") else None

    crumbs = [c.get("name") for c in (it.get("breadcrumbs") or []) if isinstance(c, dict)]
    row.category_path = [c for c in crumbs if c and c != "Главная"] or None
    row.category = category or (row.category_path[-1] if row.category_path else None)
    row.city = (it.get("location") or {}).get("name")
    geo = it.get("geo") or {}
    row.address = geo.get("address") or it.get("address")
    row.location = row.address
    coords = geo.get("coords") or {}
    row.lat = parse_number(coords.get("lat"))
    row.lng = parse_number(coords.get("lng"))
    row.published_text = it.get("sortFormatedDate")
    row.description = _html_to_text(it.get("description"))

    params: Dict[str, str] = {}
    blocks = (((it.get("conditionParams") or {}).get("data") or {}).get("items") or [],
              (bi.get("paramsBlock") or {}).get("items") or [])
    for block in blocks:
        for p in block:
            if not isinstance(p, dict):
                continue
            title, value = p.get("title"), p.get("description")
            if title and value is not None and not any(d in title for d in _DROP_PARAMS):
                params[title] = value
    row.params = params or None
    row.summary_params = params.get("Состояние")

    seller = bi.get("seller") or {}
    shop = it.get("shop") or {}
    # `buyerItem.isCompany` is NOT this: it was False for a shop. The seller
    # block and the shop block agree with each other and with the page.
    is_company = bool(seller.get("isCompany") or shop.get("isShop"))
    name = seller.get("name") or shop.get("name")
    row.seller_name = None if (not is_company and name == PRIVATE_SELLER_PLACEHOLDER) else name
    row.seller_url = strip_query(seller.get("shopUrl")) or None
    row.seller_id = shop.get("domain") or seller_id_from_url(row.seller_url) or None
    label = (seller.get("labels") or {}).get("nominative")
    row.seller_type = "company" if is_company else ("private" if label == "Частное лицо" else None)
    row.seller_since = seller.get("tenureSince")
    row.seller_reply_time = seller.get("replyTimeText") or None
    rating = bi.get("rating") or {}
    row.seller_rating = parse_number(rating.get("scoreFloat"))
    row.seller_reviews = count_before_word(rating.get("summary"))

    views = bi.get("viewStat") or {}
    row.views_total = views.get("totalViews")
    row.views_today = views.get("todayViews")
    row.is_active = it.get("isActive")
    return row


# ---------------------------------------------------------------------------
# Seller
# ---------------------------------------------------------------------------

_SELLER_HASH_RE = re.compile(r"sellerId(?:=|%3D)([0-9a-f]{32})")
SELLER_CARD_SELECTOR = '[data-marker^="item_list_with_filters/item("][data-item-id]'


def seller_hash_id(html: str) -> Optional[str]:
    """The profile's `sellerId` — a 32-hex id in the profile's own links
    (`/brands/<slug>/items/all?…&sellerId=…`). `hashedUserId` in the page
    state was empty on both captured profiles."""
    m = _SELLER_HASH_RE.search(html or "")
    return m.group(1) if m else None


# The feed's infinite scroll is ARMED by its «Показать все» control (a div,
# not a link): measured 2026-10-09, scrolling the feed without the click left
# it at 15; after the click every scroll added 15. A function BODY with an
# explicit return — each engine wraps it in its own dialect.
SELLER_SHOW_ALL_JS_BODY = (
    "var b = document.querySelector('[data-marker=\"item_list_with_filters/show_all_button\"]');"
    "if (!b) {"
    "  var els = Array.prototype.filter.call(document.querySelectorAll('button'),"
    "    function (e) { return (e.textContent || '').trim() === 'Показать все'; });"
    "  b = els.length ? els[0] : null;"
    "}"
    "if (!b) { return false; }"
    "b.scrollIntoView({block: 'center'});"
    "b.click();"
    "return true;"
)


def seller_items_url(seller_url: str) -> str:
    """`/brands/<slug>/items` — where a shop's sellerId is published when its
    profile carries none (measured 2026-10-10). Without `?s=`, which
    robots.txt disallows and which the profile's own button adds."""
    return "%s/brands/%s/items" % (BASE, seller_id_from_url(seller_url) or "")


def seller_all_url(seller_url: str, seller_hash: str) -> str:
    """The profile's full feed, which «Показать все» opens:
    `/brands/<slug>/all?sellerId=<hash>`. robots.txt allows it; the feed's
    own infinite scroll then fetches `/web/1/profile/items`, which robots.txt
    disallows — see browser_bridge._seller_feed for that decision."""
    slug = seller_id_from_url(seller_url) or ""
    return "%s/brands/%s/all?sellerId=%s" % (BASE, slug, seller_hash)


_SINCE_RE = re.compile(r"На Авито с ([^|∙·]+?)\s*(?:[|∙·]|$)")
_SUBSCRIBERS_RE = re.compile(r"(\d[\d\s ]*)\s*подписчик")
# Two profile layouts, both live: «Объявления | 353» (2026-10-09) and the
# tabbed «Активные | 11 | Завершённые | 5» (2026-10-10, /brands/i1300361).
# Missing the second left active_ads None, so the feed was never tried.
_ADS_RE = re.compile(r"(?:Объявления|Активные)\s*\|\s*(\d[\d\s ]*)")


def parse_seller(html: str, url: str = "") -> Tuple[Optional[Seller], List[Product]]:
    """The profile, and the listings rendered on it (15 measured)."""
    soup = BeautifulSoup(html or "", "html.parser")
    profile = soup.select_one('[data-marker="profile"]')
    if profile is None:
        return None, []
    seller = Seller()
    seller.url = strip_query(url) or ""
    seller.seller_id = seller_id_from_url(url)
    # The title is "<name> - официальная страница во всех регионах, отзывы на
    # Авито"; the name is everything before that suffix.
    m = re.match(r"(.+?)\s+-\s+официальная страница", _title(html))
    seller.seller_name = m.group(1).strip() if m else None
    if not seller.seller_name:
        # The profile's own heading, `<h1 data-marker="name <name>">`. Two
        # fetches of one profile 30 s apart (2026-10-10) gave the name once
        # and None once from the title alone.
        h1 = profile.select_one('h1[data-marker^="name"]') or soup.select_one('h1[data-marker^="name"]')
        seller.seller_name = h1.get_text(" ", strip=True) or None if h1 is not None else None
    score =profile.select_one('[data-marker="profile/score"]')
    seller.seller_rating = parse_number(score.get_text(strip=True)) if score is not None else None
    summary = profile.select_one('[data-marker="profile/summary"]')
    seller.seller_reviews = (count_before_word(summary.get_text(" ", strip=True))
                             if summary is not None else None)
    badges = [b.get_text(" ", strip=True) for b in soup.select('[data-marker^="badge-"]')
              if (b.get("data-marker") or "") != "badge-bar"]
    seller.badges = [b for b in dict.fromkeys(badges) if b] or None

    cards = soup.select('[data-marker^="item_list_with_filters/item("][data-item-id]')
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    text = soup.get_text(" | ", strip=True)
    since = _SINCE_RE.search(text)
    seller.seller_since = since.group(1).strip() if since else None
    subs = _SUBSCRIBERS_RE.search(text)
    seller.subscribers = int(re.sub(r"\D", "", subs.group(1))) if subs else None
    ads = _ADS_RE.search(text)
    seller.active_ads = int(re.sub(r"\D", "", ads.group(1))) if ads else None

    rows: List[Product] = []
    for i, card in enumerate(cards, 1):
        row = _row_from_card(card, None)
        # On a profile page the seller IS the page, not a link per card.
        row.seller_name = seller.seller_name
        row.seller_url = seller.url
        row.seller_id = seller.seller_id
        row.seller_type = None
        row.seller_rating = seller.seller_rating
        row.seller_reviews = seller.seller_reviews
        row.position = i
        if not row.published_text:
            date = re.search(r"\|\s*(\d{1,2} [а-я]+(?: \d{4})?\s+\d{1,2}:\d{2})\s*$",
                             card.get_text(" | ", strip=True))
            row.published_text = date.group(1) if date else None
        rows.append(row)
    seller.listings_on_page = len(rows)
    return seller, rows
