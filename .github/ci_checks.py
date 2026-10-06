#!/usr/bin/env python3
"""
CI checks that are too long to live inside the workflow YAML.

A workflow step CALLS these; it never re-implements them (family CLAUDE.md
§11). A heredoc in YAML is a check nobody runs locally and nobody notices
going stale. Run any of these from the repo root:

    python .github/ci_checks.py --help-check
    python .github/ci_checks.py --sample-check
    python .github/ci_checks.py --secret-check
    python .github/ci_checks.py --run-check --output-prefix live/canary
    python .github/ci_checks.py --all

Each prints what it looked at and exits non-zero on failure.
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Engine libraries are deliberately absent in the offline CI job. An
# ImportError naming one of these is expected, not a failure.
ENGINE_LIBS = ("playwright", "pyppeteer", "selenium")

CLIS = ["playwright_scraper.py", "puppeteer_scraper.py", "selenium_scraper.py",
        "fingerprint_client.py", "env_config.py", "diff_runs.py",
        "tools/browser_profile_client.py", "tools/autosolve_probe.py"]

# Phrases of hand-written or template sample data. The point of committing a
# sample is that it came from a real run.
FABRICATION_MARKERS = ("sample-product-", "example brand", "sample product",
                       "lorem ipsum", "your_api_key", "123456789")


def help_check():
    failed = []
    for name in CLIS:
        script = REPO / name
        if not script.is_file():
            print(f"missing  {name}")
            failed.append(name)
            continue
        result = subprocess.run([sys.executable, str(script), "--help"],
                                capture_output=True, text=True, cwd=REPO)
        if result.returncode == 0:
            print(f"ok       {name}")
            continue
        blob = result.stdout + result.stderr
        if "ModuleNotFoundError" in blob and any(lib in blob for lib in ENGINE_LIBS):
            print(f"skipped  {name} (engine library not installed here)")
            continue
        print(f"FAILED   {name}\n{blob}")
        failed.append(name)
    return failed


def sample_check(prefix="sample_output"):
    """`{prefix}.json` / `.csv` against the live `Product` schema.

    The committed sample is a `--mode listing` run, cut from a real one and
    scrubbed (seller names -> placeholders), so it doubles as a schema test:
    rename a field in the code and forget the sample, and this fails.
    """
    failed = []
    names = (f"{prefix}.json", f"{prefix}.csv")
    for name in names:
        if not (REPO / name).is_file():
            failed.append(f"{name} is missing — regenerate it from a real run")
    if failed:
        return failed
    rows = json.loads((REPO / names[0]).read_text(encoding="utf-8"))
    if not rows:
        return [f"{names[0]} is empty — a run that found nothing is not a sample"]
    blob = json.dumps(rows, ensure_ascii=False).lower()
    hits = [m for m in FABRICATION_MARKERS if m in blob]
    if hits:
        failed.append(f"{names[0]} looks fabricated: {hits}")

    sys.path.insert(0, str(REPO))
    from dataclasses import asdict
    from output_writer import Product
    expected = list(asdict(Product()).keys())
    for i, row in enumerate(rows):
        if list(row.keys()) != expected:
            failed.append(f"{names[0]} row {i}: columns differ from output_writer.Product")
            break
    with (REPO / names[1]).open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    if header != expected:
        failed.append(f"{names[1]} header differs from output_writer.Product")
    if not failed:
        priced = sum(1 for r in rows if r.get("price") is not None)
        print(f"ok       {len(rows)} rows, {len(expected)} columns, "
              f"{priced}/{len(rows)} priced, schema matches")
    return failed


def secret_check():
    """Delegate to tools/scan_secrets.py — ONE scanner, not two (§21)."""
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "scan_secrets.py"), "--worktree"],
        capture_output=True, text=True, cwd=REPO)
    if result.returncode == 0:
        print("ok       tools/scan_secrets.py --worktree: clean")
        return []
    return [line.strip() for line in (result.stdout + result.stderr).splitlines()
            if line.strip()]


def run_check(prefix="sample_output"):
    """Assertions about a RUN, not a schema — for the canary.

    The floor is set below every live listing run measured on 2026-10-06
    (147 rows over 3 pages of one Moscow category, 98–99 over 2 pages on
    each engine) so merchandising noise does not flap the canary, while a
    run that stopped paginating does trip it.
    """
    failed = []
    meta_path = REPO / f"{prefix}.meta.json"
    rows_path = REPO / f"{prefix}.json"
    if not meta_path.is_file():
        return [f"{prefix}.meta.json is missing — the run wrote no metadata"]
    if not rows_path.is_file():
        return [f"{prefix}.json is missing"]
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rows = json.loads(rows_path.read_text(encoding="utf-8"))

    MIN_ROWS = 100
    if len(rows) < MIN_ROWS:
        failed.append(f"only {len(rows)} rows, expected at least {MIN_ROWS}")
    if meta.get("status") != "complete":
        failed.append(f"run status is {meta.get('status')!r}, not 'complete'")
    if (meta.get("pages_completed") or 0) < 3:
        failed.append(f"pages_completed={meta.get('pages_completed')} — pagination "
                      f"was not exercised")
    sys.path.insert(0, str(REPO))
    from output_writer import SOURCE_DEFAULT
    if meta.get("source") != SOURCE_DEFAULT:
        failed.append(f"run source is {meta.get('source')!r}, not {SOURCE_DEFAULT!r}")

    skus = [r.get("sku") for r in rows]
    if len(set(skus)) != len(skus):
        failed.append(f"{len(skus) - len(set(skus))} duplicate sku(s)")
    pairs = [(r.get("page"), r.get("position")) for r in rows]
    if len(set(pairs)) != len(pairs):
        failed.append("page+position is not unique — the page number was not threaded")
    # A run can return the right COUNT with a column silently empty. These
    # each fail quietly in their own way on this site:
    #   price        — the grid painted before its prices did
    #   region_slug  — every item URL states it; empty means the URL was lost
    #   seller_name  — 49 of 50 cards named a seller when measured
    priced = sum(1 for r in rows if r.get("price") is not None)
    if priced < 0.9 * len(rows):
        failed.append(f"only {priced}/{len(rows)} rows carry a price")
    if not all(r.get("region_slug") for r in rows):
        failed.append("a row has no region_slug")
    named = sum(1 for r in rows if r.get("seller_name"))
    if named < 0.5 * len(rows):
        failed.append(f"only {named}/{len(rows)} rows name a seller")
    inverted = [r for r in rows if r.get("original_price") is not None
                and r.get("price") is not None and r["original_price"] <= r["price"]]
    if inverted:
        failed.append(f"{len(inverted)} row(s) have an original_price at or below their price")
    if not failed:
        print(f"ok       {len(rows)} rows, status={meta.get('status')}, "
              f"{priced} priced, {named} named sellers, "
              f"stop_reason={meta.get('stop_reason')!r}")
    return failed


CHECKS = {"help": help_check, "sample": sample_check, "secret": secret_check,
          "run": run_check}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--help-check", action="store_true",
                        help="Every shipped CLI answers --help")
    parser.add_argument("--sample-check", action="store_true",
                        help="sample_output.* exist, are real, match the schema")
    parser.add_argument("--output-prefix", default="sample_output",
                        help="check this prefix instead of sample_output "
                             "(the canary points it at a live run)")
    parser.add_argument("--run-check", action="store_true",
                        help="A run's metadata and rows say it finished "
                             "(use with --output-prefix)")
    parser.add_argument("--secret-check", action="store_true",
                        help="No credentials committed anywhere")
    parser.add_argument("--all", action="store_true",
                        help="help + sample + secret (the run check needs a live run)")
    args = parser.parse_args()

    selected = [name for name in CHECKS
                if (args.all and name != "run") or getattr(args, f"{name}_check")]
    if not selected:
        parser.error("pick at least one check, or --all")
    failures = []
    for name in selected:
        print(f"--- {name} check")
        result = (CHECKS[name](args.output_prefix) if name in ("sample", "run")
                  else CHECKS[name]())
        failures += [f"[{name}] {line}" for line in result]
    if failures:
        print()
        for line in failures:
            print("FAILED:", line)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
