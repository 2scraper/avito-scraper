#!/usr/bin/env python3
"""
diff_runs.py
-------------
Compares two output files from this project (JSON, as written by
output_writer.save) and reports what changed between them, keyed on `sku`.

    python3 diff_runs.py --old live/iphones.2026-10-06.json \\
                          --new live/iphones.2026-10-13.json

Typical use is a scheduled re-run of one listing, kept under a dated
filename and diffed against the previous one to track price moves and
delistings. `--mode listing` and `--mode item` runs can be diffed; a
`--mode seller` run cannot (its row has no sku).

Five buckets, each keyed on sku:

  added          — sku present in --new, absent from --old
  removed        — sku present in --old, absent from --new (sold, removed,
                   or just pushed off the pages fetched)
  changed        — sku in both, with a different tracked field
  source_changed — sku in both, read through DIFFERENT renderings
                   (`price_source` state vs dom) with a price difference.
                   Reported separately and ignored by --fail-on-change.
  unmatched      — rows with no sku or a duplicate one

Why `source_changed` matters on avito.ru
----------------------------------------
The same listing URL is served in two renderings (product_parser). Only the
embedded-JSON one states an item's OLD price and discount; the DOM one shows
the new price alone. Two runs a week apart that happened to get different
renderings would otherwise report every discounted item as "original_price
69989 -> None" — our instrument changing, not the item. So when the two rows
were read differently, the fields only one rendering states are not
compared, and a price difference goes in its own bucket (CLAUDE.md §8).

A run is also refused against a run of a DIFFERENT listing (another city or
category, from the sidecar's `scope.listing`): every row would read as added
or removed.
"""

import argparse
import json
import re
import sys
from typing import Dict, List, Tuple

from output_writer import UNIQUE_BY_SKU_MODES

TRACKED_FIELDS = (
    # Family prefix — the two every repo in this family diffs.
    "price", "currency",
    # What moves on an Avito listing between two runs: a discount starting or
    # ending, and a seller editing the title.
    "original_price", "discount_pct", "title",
    # NOT tracked, each for its own reason:
    #   `image_url`  — Avito serves the same photo under rotating CDN hosts.
    #   `scraped_at` — the clock, not the item.
    #   `page`/`position`, `promoted` — paid placement reorders the grid on
    #                  every visit; tracking it would report noise.
    #   `seller_*`   — part of the item's identity, not a property of it.
)

# Stated by the embedded-JSON rendering only; see the module docstring.
STATE_ONLY_FIELDS = ("original_price", "discount_pct")


def _load(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _by_sku(rows: List[dict]) -> Tuple[Dict[str, dict], int]:
    indexed = {}
    unmatchable = 0
    for r in rows:
        sku = r.get("sku")
        if sku is None:
            unmatchable += 1
            continue
        if sku in indexed:
            unmatchable += 1
            continue
        indexed[sku] = r
    return indexed, unmatchable


def diff_rows(old: List[dict], new: List[dict]) -> dict:
    old_by_sku, old_unmatchable = _by_sku(old)
    new_by_sku, new_unmatchable = _by_sku(new)

    added = [new_by_sku[sku] for sku in new_by_sku.keys() - old_by_sku.keys()]
    removed = [old_by_sku[sku] for sku in old_by_sku.keys() - new_by_sku.keys()]

    changed = []
    source_changed = []
    for sku in old_by_sku.keys() & new_by_sku.keys():
        before, after = old_by_sku[sku], new_by_sku[sku]
        read_differently = before.get("price_source") != after.get("price_source")
        field_changes = {
            field: {"old": before.get(field), "new": after.get(field)}
            for field in TRACKED_FIELDS
            if before.get(field) != after.get(field)
            and not (read_differently and field in STATE_ONLY_FIELDS)
        }
        if not field_changes:
            continue
        entry = {"sku": sku, "title": after.get("title"),
                 "changes": field_changes}
        # A price difference that arrives WITH a price_source difference is
        # our two instruments disagreeing, not the shelf price moving. It
        # goes in its own bucket and --fail-on-change ignores it.
        if "price" in field_changes and read_differently:
            entry["price_source"] = {"old": before.get("price_source"),
                                     "new": after.get("price_source")}
            source_changed.append(entry)
        else:
            changed.append(entry)

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "source_changed": source_changed,
        "unmatchable_old": old_unmatchable,
        "unmatchable_new": new_unmatchable,
    }


def _print_summary(result: dict) -> None:
    print(f"[+] {len(result['added'])} added, {len(result['removed'])} removed, "
          f"{len(result['changed'])} changed, "
          f"{len(result.get('source_changed', []))} read differently.")
    for r in result["added"]:
        print(f"  + {r.get('sku')}  {r.get('title')}  {r.get('price')} {r.get('currency')}")
    for r in result["removed"]:
        print(f"  - {r.get('sku')}  {r.get('title')}  {r.get('price')} {r.get('currency')}")
    for c in result["changed"]:
        deltas = ", ".join(f"{f}: {v['old']!r} -> {v['new']!r}" for f, v in c["changes"].items())
        print(f"  ~ {c['sku']}  {c['title']}  {deltas}")
    for c in result.get("source_changed", []):
        src = c.get("price_source", {})
        deltas = ", ".join(f"{f}: {v['old']!r} -> {v['new']!r}"
                           for f, v in c["changes"].items())
        print(f"  ? {c['sku']}  {c['title']}  {deltas}  "
              f"[read differently: {src.get('old')!r} -> {src.get('new')!r}, "
              f"not counted as a price change]")
    unmatchable = result["unmatchable_old"] + result["unmatchable_new"]
    if unmatchable:
        print(f"[!] {unmatchable} row(s) across both files had no sku or a "
              f"duplicate sku, and could not be matched across runs.")


def _run_status(path: str):
    meta_path = re.sub(r"\.json$", "", path) + ".meta.json"
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, None
    return meta.get("status"), meta


def _check_comparable(args) -> bool:
    """Refuse a diff between runs that are not both complete, or that are
    different modes, or that fetched different listings.
    """
    problems = []
    modes = {}
    listings = {}
    for label, path in (("--old", args.old), ("--new", args.new)):
        status, meta = _run_status(path)
        if status is None:
            continue
        scope = (meta or {}).get("scope") or {}
        if scope.get("listing"):
            # The page count is part of what a listing run COVERED: page 3
            # of one run is simply absent from a 2-page run.
            listings[label] = (scope["listing"], scope.get("pages"))
        mode = (meta or {}).get("mode")
        if mode:
            modes[label] = mode
        if mode and mode not in UNIQUE_BY_SKU_MODES:
            problems.append(
                f"{label} ({path}) is a {mode!r} run, which this tool does "
                f"not know how to diff.")
        if status != "complete":
            problems.append(
                f"{label} ({path}) was a {status!r} run — stopped after "
                f"{meta.get('pages_completed')} of {meta.get('pages_requested')} "
                f"page(s), reason {meta.get('stop_reason')!r}")
    if len(set(modes.values())) > 1:
        problems.append(
            f"the two runs are different modes ({modes}): a listing row and "
            f"an item row carry different fields, so added/removed would "
            f"describe the mode change rather than the data.")
    if len(set(listings.values())) > 1 and "listing" in modes.values():
        problems.append(
            f"the two runs covered different listings or page counts ({listings}): "
            f"rows outside the overlap would read as added or removed.")
    if not problems:
        return True

    print("[!] Refusing to diff these two runs:")
    for line in problems:
        print(f"      {line}")
    print("    Re-run the incomplete side, or pass --force to compare "
          "anyway (added/removed will include rows that were simply never "
          "fetched).")
    return False


def parse_args():
    p = argparse.ArgumentParser(
        description="Diff two avito-scraper JSON outputs by sku.")
    p.add_argument("--old", required=True, help="Earlier run's JSON output.")
    p.add_argument("--new", required=True, help="Later run's JSON output.")
    p.add_argument("--out", default=None,
                   help="Write the full diff as JSON to this path too.")
    p.add_argument("--fail-on-change", action="store_true",
                   help="Exit 1 if anything was added, removed or changed — "
                        "for a cron job that should only notify on a real diff.")
    p.add_argument("--force", action="store_true",
                   help="Diff even when a run's .meta.json says it was "
                        "partial or failed, or the modes or listings differ.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.force and not _check_comparable(args):
        return 2

    try:
        old = _load(args.old)
        new = _load(args.new)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[!] Could not read one of the input files: {e}")
        return 2

    result = diff_rows(old, new)
    _print_summary(result)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[+] Full diff written to {args.out}")

    if args.fail_on_change and (result["added"] or result["removed"] or result["changed"]):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
