"""The `avito-scraper` console command.

A thin launcher on purpose. The command runs the Playwright engine, but
Playwright is an optional extra (`avito-scraper[playwright]`): the three
engines' pins conflict, so none of them can be a base dependency. Importing
the engine at module level here made a plain `pip install avito-scraper`
produce a command that died with `ModuleNotFoundError: playwright` on its
first invocation, `--help` included (found by an external audit,
2026-10-07). This says what to install instead.
"""

import sys

INSTALL_HINT = ("avito-scraper runs the Playwright engine, which is an optional extra:\n"
                "  pip install 'avito-scraper[playwright]' && playwright install chromium")


def main(argv=None) -> int:
    try:
        import playwright_scraper
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] == "playwright":
            print(INSTALL_HINT, file=sys.stderr)
            return 2
        raise
    return playwright_scraper.main(argv)


if __name__ == "__main__":
    sys.exit(main())
