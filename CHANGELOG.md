# Changelog

All notable changes to this project are documented here, in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Versions
follow SemVer as closely as a CLI toolkit can: a **patch** means fixes, and a
fix may change a default when the old one was wrong — such a change leads its
entry in a blockquote.

## [Unreleased]

## [1.1.2] — 2026-10-20

> **`python3 env_config.py` printed the dashboard-form proxy whole.** With
> `AVITO_PROXY` written as `host:port:login:password` — the form 1.1.1 began
> accepting — the report that promises "without printing secrets" printed
> the login and password. If you ran it with that form and shared the
> output, change the proxy password. Found in a manual test, 2026-10-19.
>
> **What changes for a scheduler or a script.** A run that gathered rows and
> then lost its connection now exits **6** with those rows written (it was
> **5** with nothing written). `<out>.latest_attempt.meta.json` now carries
> `exit_code` on every ending (it was null except on a transport failure).
> `diff_runs.py` refuses two runs capped differently by `--max-products`
> (exit 2). `tools/browser_profile_client.py accounts` prints one row per
> account with an `account id` column instead of every object with an id. A
> connect answered `profile_locked` is retried for up to 30 s before exit 5.

### Fixed

* **`env_config.py` hid a value only when it contained `@`.** Credentials
  are now hidden by variable name (`TWOCAPTCHA_KEY`, `AVITO_CDP_ENDPOINT`,
  `AVITO_PROXY`); for the proxy the report keeps the exit's host and port.
* **The attempt sidecar's `exit_code` was null on every normal ending.**
  `<out>.latest_attempt.meta.json` is documented to carry it on every
  ending; only a transport failure set it. A complete run, a block (3), an
  empty listing (4), a page that failed (5) and a partial run (6) now record
  it too.
* **`diff_runs.py` compared a full run with one capped by `--max-products`**,
  reporting every row the capped run never fetched as removed ("127
  removed", 2026-10-19). It now refuses, like a different page count;
  `--force` still compares.
* **`tools/browser_profile_client.py accounts` listed every object with an
  id** — each account's project (id 1, once per account), proxy account and
  default profile — so the number to pass as `--account-id` was one row of
  21, unmarked. It now prints one row per account with an `account id`
  column; passing a profile id is what produced `ERROR_ACCOUNT_NOT_FOUND`.
* **The proof-of-work log said "cleared" for a page still loading**, and a
  run's stop line quoted the first response's status as if it were the
  final page's. The log now says when the next page is not recognisable
  yet, and the stop line labels the status "first response".
* **A dropped connection after gathered pages threw the pages away.** Page 1
  gave 50 rows over the Scraping Browser, page 2 died with
  `ERR_CONNECTION_RESET`, and the run exited 5 with `products: 0` and no file
  (2026-10-20). Rows gathered and then a transport failure is now a partial
  run: exit 6, the rows written, the failed page named. A failure before any
  row is still exit 5.
* **`profile_locked` right after another run exited 5.** A run started just
  after another on the same Scraping Browser profile was refused; 30 s later
  it passed. That one answer is now retried 3 times, 10 s apart
  (`page_flow.PROFILE_LOCKED_*`); a timeout or `proxy_error` is not.
* **pyppeteer reported a Scraping Browser refusal as a bare `TimeoutError`.**
  It never surfaces the service's answer; the message now says so, names the
  likely causes and points to Playwright for the reason.
* **The canary died on one dropped run.** Its endpoint step is the shipped
  `tools/browser_profile_client.py canary-env` instead of 30 lines of Python
  inside the workflow, and a live run that exits 5 is retried once on a
  freshly minted session (a block or no rows is not retried).
* **A captcha that appeared after the proof-of-work was never solved.** When
  the proof-of-work went, the page in between was not recognisable, and the
  captcha arrived during the next wait, the solve rung had already been
  passed: the run rotated to a fresh exit instead (twice in three exits,
  manual test 2026-10-19). The wall rungs now run once more after that wait.
* **`diff_runs.py`'s refusal advised "re-run the incomplete side" for every
  reason**, including two complete runs capped differently. The advice now
  follows each reason (same `--max-products`, same listing and `--pages`,
  same `--mode`, …).
* **Wording:** "not a item page" → "not an item page"; the hint for a proxy
  without a login names both gateway hosts (`.proxy.2captcha.com` and
  `.proxy.rucaptcha.com`), not only the first.
* **`.gitignore` covered `--dump-html` captures by extension** (`.html`,
  `.txt`, `.htm`); `--dump-html snap.dat` left `snap_p1.dat` unignored. It
  now matches the `_p<N>.<ext>` shape.
* **The secret scan exempted `smoke_test.py` entirely.** It now reads it;
  the suite's own credential-shaped samples are assembled from pieces.
* **The suite's private-citation check walked ignored files**, so a
  contributor's own notes excluded through `.git/info/exclude` kept it red.
  In a git checkout it now reads only what git could commit.

### Changed

* **README, GeeTest:** the "3 of 3 verified" table now carries the later
  measurements — local-browser solves refused 0 of 15 (2026-10-14 to 10-20), a
  control in which `GeeTestTask` returned solutions for an unreachable
  proxy, and 11 Scraping Browser runs that met no wall (2026-10-19) — and
  says to use the Scraping Browser.
* **README:** `--mode seller` reads all of a seller's listings through the
  feed, and exit 6 there means fewer listings than the profile's counter;
  `diff_runs`'s "removed" means "not in these pages any more", not
  "delisted".

## [1.1.1] — 2026-10-16

> **`AVITO_PROXY` / `--proxy` accept the dashboard's colon form.**
> `host:port:login:password` (with or without `http://`) is now read as
> `http://login:password@host:port` instead of being refused or misread. A
> 2Captcha gateway URL without a login is now refused when a session has to
> be minted or pinned — `tools/browser_profile_client.py connection
> --mint-session` exits 1 and the scrapers exit 2 — with a message naming the
> required form.

### Fixed

* **The canary's run check crashed on a good run.** Its success message still
  named a seller count whose computation had been removed, so the first
  complete live scrape (150 rows, 2026-10-16) ended in a `NameError` and a red
  job. The message no longer uses it, and the suite now runs the check on a
  synthetic good run and on a broken one.
* **The canary can use a ready Scraping Browser endpoint.** With the
  `AVITO_CDP_ENDPOINT` secret set, the job uses that endpoint instead of
  building one from `AVITO_BROWSER_ACCOUNT_ID`, re-pointed at a Russian exit:
  `country-ru` and a `-proxy-` segment carrying a session freshly minted on
  `AVITO_PROXY`, which the captcha solver then uses too. Measured 2026-10-16:
  148 rows over 3 pages, complete; the same endpoint without the Russian
  proxy did not clear the firewall's proof-of-work.
* **`tools/browser_profile_client.py` hid the Browser API's error code.** An
  HTTP error from the API was reported as the bare status ("404 Client Error:
  Not Found"), dropping the body that names the cause, e.g.
  `ERROR_ACCOUNT_NOT_FOUND` for a profile id passed as `--account-id`. The
  body is now printed with credentials masked.
* **Session minting on a gateway URL with no login.** `connection
  --mint-session` spliced the new login in front of `http://` when
  `AVITO_PROXY` had an empty username, so the rebuilt URL had no host and the
  run died with "the URL given is not one" of a 2Captcha gateway — a message
  about the host, for a problem with the login. The login is now replaced in
  the URL's netloc, and a gateway credential without a login and password is
  refused by name.
* **The dashboard's `host:port:login:password` credential.** Read as a URL it
  has no username and a "port" of `port:login:password`; it looked like an
  IP-whitelisted gateway, and the parser's error about that port quoted the
  login. It is now normalised to a URL wherever a proxy is read (`--proxy`,
  `AVITO_PROXY`, `--proxy-file` lines, the browser-profile tool), and no
  message built from it repeats the credential.
* **Percent-encoded proxy passwords.** The login and password are decoded
  before they reach Playwright, pyppeteer, the local forwarder and the
  captcha solver's proxy fields; they were passed on still encoded.

## [1.1.0] — 2026-10-15

> **Exit codes and file modes changed.** A run that ends with zero rows for
> any reason other than a listing read to its end now exits **5** (content
> never obtained) instead of **4** — an item page with no item on it, a
> seller profile that never parsed, a failed page. A wrong host, a
> robots-disallowed URL, the wrong `--mode`, two URLs outside item mode and
> an unreadable `--from` file now exit **2** instead of **1**. Output files
> were created `0600`; they now follow the umask, and an existing file keeps
> its mode. Selenium with a typed `--cdp-endpoint` now exits **2** instead of
> **5**. **At most one paid captcha solve per page per run:** a page that
> stays walled used to buy one solve per exit (2 with the default
> `--proxy-block-retries 1`, 4 with 3); rotating to a fresh exit still
> re-fetches the page, but no longer buys a second token for it.

### Fixed

* **Zero rows read as an empty catalogue.** `finish_run` returned 4 for
  every zero-row run that was not blocked, so a removed item or a page that
  failed looked like "nothing listed". Only the stop reasons that mean the
  listing was read to its end keep 4; any other one, including one added
  later, is 5.
* **Usage errors exited 1**, which the exit table calls a crash, and a
  missing `--from` file ended in a traceback. They are now argparse errors
  (exit 2), checked before any browser starts.
* **Outputs were owner-only.** The atomic writer's temp file is created
  `0600` and the rename kept it. New files now get `0o666 & ~umask`; an
  existing file keeps its own mode. Applies to the rows, the sidecar and the
  attempt sidecar.
* **Selenium and the Scraping Browser.** An endpoint from `.env` (shared by
  all three engines) made every Selenium run exit 5 "transport_error". It is
  now ignored with a notice; a typed `--cdp-endpoint` is a usage error.
* **The secret scan read tracked files only.** `tools/scan_secrets.py
  --worktree` (and `ci_checks.py --secret-check`) now also reads untracked
  files that are not ignored, skips any directory holding `pyvenv.cfg`, prints
  how many files it read, and fails when it read none instead of reporting
  "clean".
* **Paid solves are capped per page.** The allowance
  (`page_flow.SOLVES_PER_PAGE = 1`) is checked before each purchase, keyed by
  the page's URL, and not reset by a retry or by rotating to a fresh exit. A
  refused purchase is logged and counted as `solves_refused_by_budget` in
  `transport_facts`. The Scraping Browser's own auto-solve is not counted.
* **pyppeteer's Scraping Browser connect is bounded** (30 s); a rejected
  handshake could leave it waiting forever.

### Added

* **CSV formula guard.** Text cells starting with `=`, `+`, `-`, `@`, tab, CR
  or LF get a leading `'` in the CSV only (after lists and dicts are joined);
  numbers are untouched and the JSON keeps the site's text. The sidecar
  records `csv_cells_escaped`.

### Changed

* **The canary no longer goes green without scraping.** Without
  `TWOCAPTCHA_KEY`, `AVITO_PROXY` or `AVITO_BROWSER_ACCOUNT_ID` it fails,
  naming the missing one, instead of taking a green skip branch; a live run
  that does not exit 0 is red. Its data check no longer requires half the rows
  to name a seller (the private/company mix is the category's, not the
  parser's) and instead requires `sku`, `url` and `title` on every row.
* `requirements-playwright.txt` asks for `playwright>=1.44`, as the
  `[playwright]` extra already did.
* `.gitignore` covers `--dump-html` names with a `.txt` or `.htm` extension.
* Comments and docstrings no longer point at internal documents or at other
  repositories; each states its reason here.

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
