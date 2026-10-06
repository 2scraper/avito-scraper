# Contributing

Bug reports, site-change reports and pull requests are all welcome. This file
covers the few things specific to a scraper, which are not the usual ones.

## Before you open anything

Run the offline suite. It needs no network, no browser and no API key:

```bash
pip install -r requirements.txt
python3 smoke_test.py
```

It prints its own check count and lists any group it skipped because an
engine library is absent.

**The suite must pass with no engine installed at all.** CI installs only
`beautifulsoup4` and `requests` in the offline job, so any import of
`playwright_scraper`, `puppeteer_scraper` or `selenium_scraper` in a test sits
inside `try/except ImportError` with the skip recorded. Each engine imports
its driver at module level so that skip means something; the suite asserts it.

If the suite fails on a clean clone, that is itself the bug — say so.

## Never commit a credential

`.env` is in `.gitignore`, and so are `.env.*`, `*.bak` and `proxylist*.txt`.
Install the hooks once per clone — they run the same scanner CI runs, while
the secret is still private:

```bash
tools/install_hooks.sh
```

The scrapers mask `user:pass@` in their own log lines, but raw HTML dumps and
your shell history are **not** masked. Replace keys, proxy passwords and full
`ws://user:pass@host:9222` endpoints with `***` before pasting anything.

## Reporting a site change

avito.ru changing its markup is the normal way this stops working. Say which
of the three places moved — it narrows the fix a lot:

1. **Listing cards** — `[data-marker="item"][data-item-id]` and their
   `itemprop` microdata, or the `script[data-mfe-state]` JSON.
2. **Item pages** — `window.__staticRouterHydrationData` →
   `loaderData[…].buyerItem`.
3. **Seller pages** — `[data-marker="profile"]` and the profile counters.

Or the **firewall** changed: a captcha type other than GeeTest v4 (its code
also carries hCaptcha and an image captcha). Attach a `--dump-html` capture
with anything personal removed.

## Pull requests

**Add a test for the behaviour you are changing.** `smoke_test.py` is one
file of plain functions; fixtures are cut from real captures and scrubbed
(`fixtures/README.md`). A new fixture must parse identically to its untrimmed
original, and must carry no seller names, descriptions or session tokens.

Properties pinned by a test because they were once wrong or easy to get
wrong:

- **`price` is the displayed price.** Avito's bare price fields are the
  pre-discount figure.
- **Substitute cards are not results.** A no-match search is served with ~50
  cards under «Похожие объявления…».
- **A captcha solve goes through the browser's exit.** A token solved from
  another IP is rejected; the solver refuses to run without the proxy.
- **The wall is recognised by its `<form>`**, not by the class name, which the
  wall's own script also mentions.
- **Exit codes are a contract**: `0` ok, `1` crash, `2` bad usage, `3`
  blocked, `4` zero rows, `5` transport error, `6` partial. A transport fault
  is never `3`.
- **A run that finds nothing writes nothing.** `--allow-empty` is the opt-out.
- **No phone, no IMEI, no private seller's placeholder name** in any row.

There is also a naming check (banned phrases) and a vocabulary check against
sibling repos. If one trips, read the message.

### Style

- Match the file you are editing. No formatter is enforced.
- Comments say **why**, especially where the obvious version is wrong, and
  numbers carry the date and method they were measured with.

### If your change needs a live run

Say in the PR what you ran, against which URL, through which transport, and
what came back — counts, not adjectives. CI never touches the live site.

## Scope

This repo reads avito.ru's **public pages** as an anonymous visitor. Out of
scope: anything behind login (phones, messages), robots-disallowed URLs, and
volume features that would make the firewall's job harder for its own sake.

## Licence

By contributing you agree your contribution is licensed under the MIT licence
in [LICENSE](LICENSE).
