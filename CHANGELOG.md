# Changelog

All notable changes to this project are documented here, in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Versions
follow SemVer as closely as a CLI toolkit can: a **patch** means fixes, and a
fix may change a default when the old one was wrong — such a change leads its
entry in a blockquote.

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
