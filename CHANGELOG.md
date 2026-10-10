# Changelog

All notable changes to this project are documented here, in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Versions
follow SemVer as closely as a CLI toolkit can: a **patch** means fixes, and a
fix may change a default when the old one was wrong — such a change leads its
entry in a blockquote.

## [1.0.2] — 2026-10-10

Fixes from a review of v1.0.1, each reproduced against v1.0.1 before it
was fixed, plus one more that the live check turned up.

> **The first run in the README failed in v1.0.1.** `--mode listing --url
> "https://www.avito.ru/moskva?q=iphone+15" --pages 2` could exit **4**
> ("0 rows — refusing to write") after 0 ms. Upgrade if you met that.

### Fixed

* **A page skeleton was taken for an empty listing.** Any served avito
  page without cards and without a pager counted as `empty`, and the
  readiness wait stopped on it at once — so a listing whose cards had not
  painted yet exited 4 with no data. `empty` is now given only on an
  explicit sign (the «Похожие объявления» substitutes heading); a page that
  is merely without cards is waited for, and is called empty only if it is
  still without cards after the full wait. Measured after the fix: the
  command above gives 100 rows over the Scraping Browser (readiness waits
  of 500 and 1000 ms — the skeleton was there) and 100 rows on a local
  browser.
* **The same after a proof-of-work.** Once the firewall's proof-of-work
  cleared, a skeleton that followed was classified `empty` straight away
  ("page 1 is a served listing with no cards on it", exit 4). Same fix;
  measured on the local route: proof-of-work → GeeTest → 100 rows.
* **`--out` into a directory that does not exist** crashed with
  `FileNotFoundError` and wrote not even the attempt metadata — and the
  README's examples write to `live/`, which a fresh clone does not have.
  The directory is created now, for the output, the metadata and
  `--dump-html`.
* **`solver_cost` was always 0.** 2Captcha returns a solve's cost as a
  string; the total counted numbers only. Found on the live check (two
  solves at $0.00299, reported total 0).
* The item-mode log counted against the whole input (`item 1/100` under
  `--max-products 2`); it now counts what will be fetched.

## [1.0.1] — 2026-10-10

Fixes from an external audit of v1.0.0, and from live runs on 2026-10-08,
-09 and -10. Each finding was reproduced before it was fixed, and each now
has a regression test.

> **Behaviour changes for existing users.**
> * An **unexpected exception** now exits **1** (crash, traceback printed
>   with credentials masked). It used to exit 5, the same as a dead exit, so a
>   parser bug read as a transport fault. Exit 5 is now only a transport fault.
> * `diff_runs.py` now **refuses** two outputs when either has no readable
>   `.meta.json`, or when they were written by different row schemas. Pass
>   `--force` to compare anyway.
> * `--mode seller` now exits **6** (partial) when it wrote fewer listings
>   than the profile's own counter, instead of 0.

### Fixed

* **The installed wheel allowed every URL.** The robots snapshot was a loose
  `.txt` beside the modules and was not shipped; an installed copy read zero
  rules. It is now the module `robots_snapshot.py`, and an empty rule set
  raises instead of allowing everything.
* **`pip install avito-scraper` gave a command that crashed** with
  `ModuleNotFoundError: playwright`. The command now goes through a launcher
  that says to install `avito-scraper[playwright]` (exit 2).
* **No attempt metadata on a failed start or a crash.**
  `<out>.latest_attempt.meta.json` is now written on every ending, with
  `exit_code`, `error_class` and a masked `error_message`.
* **A failed captcha solve is now traceable.** Every solve in
  `transport_facts.solves` records the 2Captcha `task_id` and its own error
  code (`ERROR_CAPTCHA_UNSOLVABLE`, `TIMEOUT`, …), and the solver waits 240 s
  instead of 180 s: on 2026-10-08 2Captcha took 217–220 s to return
  `ERROR_CAPTCHA_UNSOLVABLE` for this captcha, so the old wait abandoned
  every task before its verdict and logged only "no solution".
* **A third firewall variant** — «подождите немного и обновите страницу», no
  captcha form, no proof-of-work, served right after an accepted solve
  (measured 2026-10-08) — is now a refusal of the exit and rotates, instead
  of being waited out as a challenge and reported blocked.
* **A solve is `verified` only when the page actually came back.** The first
  release marked it verified as soon as the document stopped being the wall,
  which included that third variant. A solve that is followed by something
  else records `after_submit`. A proof-of-work after a solve is waited out.
* **The wall's other two captchas.** The page can show Avito's own picture
  captcha or hCaptcha instead of GeeTest; the scraper now recognises which
  one is showing and records it (`solves[].variant`). The picture captcha is
  solved with `ImageToTextTask` and answered through the page's own field —
  implemented from the wall's script, not yet met live (every measured
  request was assigned GeeTest). hCaptcha is solved with `HCaptchaTask`
  (sitekey from the wall, the browser's own exit, `enterprisePayload.rqdata`
  only if the wall ever carries one) and its token submitted through
  `#h-captcha-response` — also not yet met live. One live `HCaptchaTask` on
  avito's sitekey (2026-10-09) was accepted by the API and returned
  ERROR_CAPTCHA_UNSOLVABLE after 51 s: the request shape is right, a solved
  token for this wall is not yet measured.
* **`--mode seller` now returns all of a seller's listings**, not the 15
  the profile renders: it opens the seller's feed, presses «Показать все»
  and scrolls until the profile's own counter or `--max-products`
  (measured: 353 of 353). The feed's scroll makes the page fetch
  `/web/1/profile/items`, which robots.txt disallows — README says so; this
  is a deliberate choice. `--max-products 15` keeps to the profile page.
* **A recommendation carousel was read as search results.** On 2026-10-10
  `?q=yeezy` page 1 carried a «Подобрали для вас» block INSIDE the results
  grid, with 31 cards of the same shape; page 1 came back with 82 rows, 32
  of them unrelated. Cards inside `itemsCarousel` are no longer results.
* **A seller run with fewer listings than the profile states is now
  `partial` (exit 6).** A shop stating 7331 active listings was written as a
  `complete` run of 12, because the profile's tabbed counter («Активные |
  N») was not read and the feed never started. Both are fixed; a shop
  profile without a `sellerId` now takes it from `/brands/<slug>/items`
  (measured: 100 of 100 with `--max-products 100`).
* The seller's name falls back to the profile heading when the page title
  does not carry it (one fetch of two, 2026-10-10).
* Rotation messages name Chromium's error (`ERR_CONNECTION_RESET`) instead of
  a fragment of the URL.
* **`schema_version`** now fingerprints the row class of the run's mode
  (names, order and types), not `Product`'s field names only.
* Leftovers from the repositories this one was scaffolded from, in
  `requirements.txt`, `.gitignore`, `.dockerignore` and the Claude review
  prompt, are gone; the vocabulary check now covers every tracked file.
  Comments no longer point at an operator file that is not published.

### Changed

* CI builds the wheel, installs it outside the checkout and uses it
  (`ci_checks.py --wheel-check`): robots verdicts, and the console command
  with and without the playwright extra.
* A skipped canary (no secrets — the case for this public repository) now
  raises a visible warning; README explains what the green badge does and
  does not prove.

### Not changed, and why

* **The canary still goes green when it skips.** A check that is red every
  day for want of secrets teaches everyone to ignore checks; the skip is now
  loud instead.
* Pinning Actions by SHA, lock files, a `src/` package layout and dropping
  pyppeteer are larger decisions, left for a minor release.

## [1.0.0] — 2026-10-06

First release.

### Modes

* `--mode listing` — category and search listings, `?p=N` pagination, one row
  per card.
* `--mode item` — item pages, by `--url` (repeatable) or `--from` a listing
  run's JSON.
* `--mode seller` — a `/brands/…` profile and the listings it renders.

### Transports

* Playwright (primary), pyppeteer and Selenium, sharing one CLI and one run
  loop. Playwright and pyppeteer connect to the Scraping Browser API over
  CDP; all three launch a local browser on `AVITO_PROXY`.
* Selenium reaches an authenticated proxy through `proxy_forwarder.py`, a
  loopback proxy that adds the credential upstream (WebDriver BiDi's
  `add_auth_handler` deadlocked on Selenium 4.50).

### The firewall

* Proof-of-work variant (HTTP 439): waited out.
* Captcha variant (HTTP 429): GeeTest v4 — Scraping Browser auto-solve first,
  then 2Captcha `GeeTestTask` through the browser's own exit, submitted via
  the page's own form. Measured 3 of 3 accepted on the Scraping Browser, 2 of
  3 locally, $0.00299 each. A proxyless solve is refused, because the site
  rejected the one that was measured.
* Local browsers move a walled page, or a dead exit, to a fresh session
  (`--proxy-block-retries`).
* `tools/browser_profile_client.py connection --mint-session --write-env`
  builds the endpoint and its matching solver proxy together.

### Extraction

* Listing: DOM + microdata, or the embedded `data-mfe-state` JSON when the
  site serves it; `price_source` says which.
* The displayed price, never Avito's bare (pre-discount) price field.
* Substitute cards on a no-match search are not results.
* Private sellers: no name exported (Avito shows «Пользователь»).
* Phone and IMEI are never exported.

### Known and deliberate

* A seller profile's listings beyond the 15 rendered on first load are not
  fetched.
* hCaptcha and Avito's own image captcha appear in the firewall's code but
  were never served in a measured run; this repo does not implement them yet.
* `--concurrency` above 1 is refused: the firewall is a request-rate decision.
