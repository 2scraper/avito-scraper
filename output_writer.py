"""
output_writer.py
-----------------
Shared row model + JSON/CSV writers used by all three browser engines.

Three kinds of row, one per --mode
----------------------------------
    listing   Product   one listing card
    item      Item      one item page — a Product plus what only the page has
    seller    Seller    one profile page (its rendered cards go out as
                        Product rows in a second file, `<out>_listings.*`)

The first nine `Product` columns are the family prefix — `source`,
`scraped_at`, `url`, `sku`, `title`, `image_url`, `price`, `currency`,
`category` — byte-identical to every sibling repo, so a consumer written
against another repo in this family reads them unchanged (2scraper family rule).
`Item` extends `Product`, so its prefix is the same by construction.

`sku` is Avito's item id (`8245354778`): the trailing digits of the item
URL, `data-item-id` on a card, `item.id` on the page — all three agree.

Columns that are NOT here, and why
----------------------------------
- phone: Avito shows it only after login; the item page carries a masked one
  («8 958 XXX-XX-XX»). Never read, never exported.
- IMEI: some categories list a masked IMEI among the parameters. Dropped
  from `params` by name in product_parser.
- `delivery` IS here but is filled only from the listing JSON rendering
  (`contacts.delivery`); the ordinary DOM rendering does not state it, so it
  is None there rather than a guessed False.
"""

import csv
import hashlib
import json
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timezone
from typing import Optional, List, Set, Sequence, Any, Dict, Tuple, Type


SOURCE_DEFAULT = "avito.ru"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Product:
    # ---- family prefix — identical across the family, in this order ----
    source: str = SOURCE_DEFAULT
    scraped_at: str = field(default_factory=_now)
    url: str = ""
    sku: Optional[str] = None
    title: Optional[str] = None
    image_url: Optional[str] = None
    # The DISPLAYED price — what the buyer pays. Avito's bare `price` fields
    # are the pre-discount figure; see product_parser's docstring.
    price: Optional[float] = None
    currency: Optional[str] = None
    category: Optional[str] = None
    # Where the row came from: `state` (embedded JSON) or `dom` (the
    # ordinary rendering's markup). Two runs that differ only in this are
    # not a price change — diff_runs.py reports them as `source_changed`.
    price_source: Optional[str] = None

    # ---- Avito-specific, appended so the family prefix stays stable ----
    # The site's own "old" price of a discounted item, and its own percent.
    # Only the JSON rendering states them; the DOM shows the new price only.
    original_price: Optional[float] = None
    discount_pct: Optional[float] = None
    # The first path segment of the item URL (`moskva`, `lyubertsy`):
    # stated on every row by both renderings. `city` is the human name and
    # is only filled where the page states it — the JSON rendering always,
    # the DOM rendering only via an eagerly-loaded photo's alt text (7 of 50
    # cards measured), so it is NOT the column to group by.
    region_slug: Optional[str] = None
    city: Optional[str] = None
    # Metro / district text as the card prints it ("Багратионовская, 6–10
    # мин."), or the address on an item page.
    location: Optional[str] = None
    # The card's one-line parameter summary, raw: "Новый" / "Б/у" on phones,
    # year and mileage on cars. Its meaning is the CATEGORY's, so it is not
    # called `condition`.
    summary_params: Optional[str] = None
    published_at: Optional[str] = None      # ISO, JSON rendering only
    published_text: Optional[str] = None    # "9 сентября в 12:57", as printed
    # Companies and shops are named. Private persons are anonymised by Avito
    # itself («Пользователь», no profile link) -> None here, type "private".
    seller_name: Optional[str] = None
    seller_type: Optional[str] = None       # company / private / None = not stated
    seller_id: Optional[str] = None         # the /brands/<id> slug or hash
    seller_url: Optional[str] = None
    seller_rating: Optional[float] = None
    seller_reviews: Optional[int] = None
    promoted: Optional[bool] = None         # «Продвинуто» — paid placement
    badges: Optional[List[str]] = None
    delivery: Optional[bool] = None
    page: Optional[int] = None
    position: Optional[int] = None


@dataclass
class Item(Product):
    """An item page. Everything a listing card has, plus the page's own."""
    description: Optional[str] = None
    params: Optional[Dict[str, str]] = None
    images: Optional[List[str]] = None
    category_path: Optional[List[str]] = None
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    views_total: Optional[int] = None
    views_today: Optional[int] = None
    seller_since: Optional[str] = None      # "мая 2019", as printed
    seller_reply_time: Optional[str] = None
    is_active: Optional[bool] = None


@dataclass
class Seller:
    """A seller profile page (`/brands/<id>`)."""
    source: str = SOURCE_DEFAULT
    scraped_at: str = field(default_factory=_now)
    url: str = ""
    seller_id: Optional[str] = None
    seller_name: Optional[str] = None
    seller_rating: Optional[float] = None
    seller_reviews: Optional[int] = None
    seller_since: Optional[str] = None
    subscribers: Optional[int] = None
    # The profile's own "Объявления N" counter — how many are LIVE. The
    # profile page renders 15; the rest come from scrolling the seller's feed
    # (up to this counter or --max-products). `listings_on_page` is how many
    # rows the companion `<out>_listings` file actually holds — compare the
    # two to see whether the feed was read to the end.
    active_ads: Optional[int] = None
    listings_on_page: Optional[int] = None
    badges: Optional[List[str]] = None


# Row class by --mode, so the bridge maps its mode to a schema in one place.
ROW_CLASS_BY_MODE = {
    "listing": Product,
    "item": Item,
    "seller": Seller,
}

# Modes whose rows are one-per-sku, and therefore safe to dedupe on `sku` and
# to hand to diff_runs.py. A seller row has no sku; diff_runs refuses it.
UNIQUE_BY_SKU_MODES = ("listing", "item")


def dedupe_by_key(rows: Sequence[Any], seen: Set[str], key: str = "sku") -> List[Any]:
    """Drop rows whose key already appeared earlier in this same run.

    `seen` is mutated in place, so callers thread the same set across pages —
    a card repeated on a later page must not become a duplicate row.

    A row with no key is always kept: there is nothing to check a duplicate
    against, and dropping it would be silent data loss.
    """
    fresh = []
    for r in rows:
        val = getattr(r, key, None)
        if val is None or val not in seen:
            if val is not None:
                seen.add(val)
            fresh.append(r)
    return fresh


def dedupe_by_sku(rows: Sequence[Any], seen: Set[str]) -> List[Any]:
    return dedupe_by_key(rows, seen, key="sku")


# CSV cannot hold a list or a dict. A list is joined with " | " (readable in a
# spreadsheet, splittable again); a dict (`params`) is written as JSON. The
# JSON output keeps the real structures.
LIST_CSV_SEPARATOR = " | "


def _csv_value(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return LIST_CSV_SEPARATOR.join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return v


def _atomic_write(path: str, text: str) -> None:
    """Write `text` to `path` so a crash cannot truncate what was there.

    Temp file in the SAME directory (so `os.replace` stays on one filesystem
    and is therefore atomic), flush, fsync, then replace. The family shipped
    the naive version for months: `open(path, "w")` truncates the previous
    good file the instant it is called, so a process killed mid-serialisation
    — or a full disk — destroyed last night's dataset and left a half-written
    one that parses as valid JSON right up to where it stops.
    """
    import os
    import tempfile
    directory = os.path.dirname(os.path.abspath(path)) or "."
    # `--out live/x` in a fresh clone: no `live/`. mkstemp raised
    # FileNotFoundError and not even the attempt metadata was written.
    os.makedirs(directory, exist_ok=True)
    handle_fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-",
                                      suffix=os.path.basename(path))
    try:
        # mkstemp creates 0600 and os.replace keeps it, which made every
        # output owner-only. A new file gets what open() would have given it
        # (0o666 under the umask); an existing file keeps its own mode.
        try:
            mode = os.stat(path).st_mode & 0o7777
        except FileNotFoundError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(tmp, mode)
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(rows: Sequence[Any], path: str) -> None:
    payload = json.dumps([asdict(r) for r in rows], indent=2, ensure_ascii=False)
    _atomic_write(path, payload + "\n")


# A spreadsheet runs a cell that starts with one of these as a formula, and
# titles, locations and seller names are written by whoever posted the ad.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def _csv_cell(v: Any) -> Tuple[Any, bool]:
    """Serialise one value for CSV; return it and whether it was escaped.

    Only STRINGS are escaped, and only after a list or dict has been joined
    into one: `-5` stays a number so sums over the column keep working, while
    a joined list whose first element looks like a formula is text and is
    caught. JSON output is never touched.
    """
    v = _csv_value(v)
    if isinstance(v, str) and v.startswith(FORMULA_PREFIXES):
        return "'" + v, True
    return v, False


def write_csv(rows: Sequence[Any], path: str, row_cls: Type = Product) -> int:
    """CSV with the same field order as JSON; return how many cells were
    prefixed with an apostrophe to stop a spreadsheet running them.

    An EMPTY result still carries its header, so a consumer reads a table with
    no rows instead of failing on a zero-byte file.
    """
    import io
    names = [f.name for f in fields(row_cls)]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=names, extrasaction="ignore")
    writer.writeheader()
    escaped = 0
    for row in rows:
        out = {}
        for k, v in asdict(row).items():
            out[k], hit = _csv_cell(v)
            escaped += hit
        writer.writerow(out)
    _atomic_write(path, buffer.getvalue())
    return escaped


EXIT_NO_PRODUCTS = 4

# An unexpected exception — a bug, not the site and not the transport.
EXIT_CRASH = 1

# Exit code for a run blocked by a bot-check/challenge page before parsing
# even started.
EXIT_BLOCKED = 3

# Exit code for a run that gathered SOME rows and then stopped early.
EXIT_PARTIAL = 6

# Exit code for a run that holds NO rows because the content was never
# obtained: a Scraping Browser connection that failed (e.g. profile_locked),
# a browser that never started, a navigation that timed out on every try, a
# dead or unauthenticated proxy, a page that failed, an item or profile that
# never parsed. As opposed to EXIT_BLOCKED (the site refused the page),
# EXIT_NO_PRODUCTS (the listing was read and is empty) and an uncaught crash
# (1). A captcha-solve failure is not this: it is a warning, and the run
# continues to report what the page really was.
EXIT_REMOTE_API_ERROR = 5


class RemoteAPIError(RuntimeError):
    """A 2Captcha product call failed on its own terms, as opposed to the
    target site blocking a page. Mapped to EXIT_REMOTE_API_ERROR."""


def write_run_meta(out_prefix: str, meta: dict, *, suffix: str = ".meta.json") -> str:
    """Write a run-metadata sidecar next to the output, return its path."""
    path = f"{out_prefix}{suffix}"
    _atomic_write(path, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


ATTEMPT_META_SUFFIX = ".latest_attempt.meta.json"


def write_attempt_meta(out_prefix: str, meta: dict) -> str:
    """Metadata for EVERY attempt, successful or not.

    Why this exists, and why it is separate from `.meta.json`
    --------------------------------------------------------
    `.meta.json` describes the DATASET sitting beside it, so it is written
    only when a dataset is written. That is correct, and on its own it is
    also a trap: a blocked run leaves last night's `.meta.json` untouched,
    saying `status: complete, products: 1`, beside last night's `.json`. A
    pipeline that reads the files after a run cannot tell that from a fresh
    success — it sees complete metadata and plausible data and ships stale
    prices.

    So every attempt writes THIS file, unconditionally, and the two answer
    different questions:

        <out>.meta.json                  what is the data beside me?
        <out>.latest_attempt.meta.json   what happened the last time we ran?

    A consumer that cares about freshness reads the second and checks
    `data_updated` and `finished_at`.
    """
    return write_run_meta(out_prefix, meta, suffix=ATTEMPT_META_SUFFIX)


def schema_version(row_cls: Type = Product) -> str:
    """A short fingerprint of ONE row schema — names, order and declared
    types — so a diff can refuse to compare a run written before a column
    changed with one written after.

    Per row class: the first version hashed `Product`'s field names only, so
    an `Item`-only column change, or a type change, left it unchanged
    (external audit, 2026-10-07).
    """
    spec = ",".join("%s:%s" % (f.name, f.type) for f in fields(row_cls))
    return hashlib.sha256((row_cls.__name__ + "|" + spec).encode()).hexdigest()[:12]


def scope_fingerprint(start_url: Optional[str] = None, mode: Optional[str] = None,
                      pages: Optional[int] = None,
                      max_products: Optional[int] = None) -> dict:
    """What a run covered, in the fields a diff must agree on.

    Two complete runs of DIFFERENT listings (another city, another category)
    must not be compared row by row — every item would read as delisted.
    The start URL without `p` / `context` is the listing's identity here.
    """
    base = None
    if start_url:
        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
        parts = urlparse(start_url)
        query = sorted((k, v) for k, v in parse_qsl(parts.query) if k not in ("p", "context"))
        base = urlunparse(parts._replace(query=urlencode(query), fragment=""))
    return {
        "listing": base,
        "mode": mode,
        "pages": pages,
        "max_products": max_products,
        "schema_version": schema_version(ROW_CLASS_BY_MODE.get(mode, Product)),
    }


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             mode: str = "listing", source: str = SOURCE_DEFAULT,
             scope: Optional[dict] = None) -> dict:
    """Build the metadata dict for a finished run."""
    return {
        "source": source,
        "mode": mode,
        "status": status,
        "stop_reason": stop_reason,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed or [],
        "products": products,
        "scope": scope if scope is not None else scope_fingerprint(),
        "start_url": start_url,
        "final_url": final_url,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def save(rows: Sequence[Any], out_prefix: str, fmt: str,
         allow_empty: bool = False, row_cls: Type = Product,
         stats: Optional[dict] = None) -> int:
    """Write JSON/CSV and return a process exit code.

    On zero rows, nothing is written at all unless `allow_empty` — see the
    2scraper family rule: a run that finds nothing must not
    silently replace yesterday's good output with an empty file.
    """
    if not rows and not allow_empty:
        print(f"[!] 0 rows — refusing to write {out_prefix}.json/.csv, so an "
              f"earlier good result isn't overwritten with an empty one. "
              f"Pass --allow-empty if an empty result is the expected answer.")
        return EXIT_NO_PRODUCTS

    if fmt in ("json", "both"):
        write_json(rows, f"{out_prefix}.json")
        print(f"[+] Saved {len(rows)} row(s) -> {out_prefix}.json")
    if fmt in ("csv", "both"):
        escaped = write_csv(rows, f"{out_prefix}.csv", row_cls=row_cls)
        if stats is not None:
            stats["csv_cells_escaped"] = escaped
        print(f"[+] Saved {len(rows)} row(s) -> {out_prefix}.csv")
    return 0 if rows else EXIT_NO_PRODUCTS


# Stop reasons that mean the run saw everything there was to see.
COMPLETE_STOP_REASONS = ("completed", "pagination_exhausted", "no_new_products",
                         "single_page_mode")


def finish_run(rows: Sequence[Any], out_prefix: str, fmt: str,
               allow_empty: bool, *, blocked: bool, stop_reason: str,
               pages_requested: int, pages_completed: int,
               start_url: str, final_url: str,
               pages_failed: Optional[List[int]] = None,
               mode: str = "listing", source: str = SOURCE_DEFAULT,
               scope: Optional[dict] = None,
               extra: Optional[dict] = None) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all three browser engines so the status/exit-code mapping
    cannot drift between them.

    Zero rows means one of three things, and each gets its own code: the
    site refused (3), the listing was read to its end and really is empty
    (4, only for the stop reasons in COMPLETE_STOP_REASONS), or the content
    was never obtained (5) — a page that failed, a profile that never
    parsed, any stop reason not known to be complete. An unknown reason
    must not read as an empty catalogue.
    """
    import uuid
    complete = stop_reason in COMPLETE_STOP_REASONS
    row_cls = ROW_CLASS_BY_MODE.get(mode, Product)
    run_id = uuid.uuid4().hex[:12]
    stats: dict = {}
    rc = save(rows, out_prefix, fmt, allow_empty=allow_empty, row_cls=row_cls,
              stats=stats)
    wrote_output = bool(rows) or allow_empty

    status = "complete" if (rows and complete) else ("partial" if rows else "failed")
    base = run_meta(
        status=status, stop_reason=stop_reason,
        pages_requested=pages_requested, pages_completed=pages_completed,
        pages_failed=pages_failed, mode=mode, source=source, scope=scope,
        start_url=start_url, final_url=final_url, products=len(rows))
    base["run_id"] = run_id
    base["blocked"] = bool(blocked)
    if "csv_cells_escaped" in stats:
        # Declared, so CSV and JSON differing in those cells is not a surprise.
        base["csv_cells_escaped"] = stats["csv_cells_escaped"]
    if extra:
        base["transport_facts"] = extra

    # The dataset's own sidecar: only when a dataset was written, because a
    # "failed" sidecar beside good data would contradict it.
    if wrote_output:
        write_run_meta(out_prefix, dict(base, data_updated=True))

    # The attempt sidecar: ALWAYS. See write_attempt_meta.
    write_attempt_meta(out_prefix, dict(base, data_updated=bool(wrote_output)))

    if not rows:
        if blocked:
            return EXIT_BLOCKED
        return rc if complete else EXIT_REMOTE_API_ERROR
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc
