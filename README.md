# Avito Scraper — Russian Classifieds Listings and Sellers in Python

[![release](https://img.shields.io/github/v/release/2scraper/avito-scraper?sort=semver)](https://github.com/2scraper/avito-scraper/releases)
[![tests](https://github.com/2scraper/avito-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/2scraper/avito-scraper/actions/workflows/tests.yml)
[![canary](https://github.com/2scraper/avito-scraper/actions/workflows/canary.yml/badge.svg)](https://github.com/2scraper/avito-scraper/actions/workflows/canary.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![licence](https://img.shields.io/badge/licence-MIT-lightgrey)](LICENSE)
[![engines](https://img.shields.io/badge/engines-Playwright%20%7C%20Selenium%20%7C%20pyppeteer%20%7C%20CDP-informational)](#engines)
[![needs a Russian exit](https://img.shields.io/badge/needs-a%20Russian%20exit%20%2B%20a%20browser-orange)](#the-one-thing-you-actually-need)

**Avito scraper** — an open-source (MIT) Python tool that reads avito.ru category and search listings, item pages and seller profiles, and writes prices (with the old price and discount where the site states them), location, condition, item parameters and the seller's rating to JSON or CSV. avito.ru sits behind its own firewall: measured from 2026-10-06 through a Russian residential exit, a plain HTTP client was refused 6 of 6 times, and a real browser on a Russian exit was needed — more often than not with a captcha solve. Use it to build a dataset of Russian classifieds, or to compare two runs of the same search.

Scrapes avito.ru — category and search listings, item pages and seller
profiles. Prices (the price the buyer sees, with the old price and discount
where the site states them), location, condition, item parameters, photos,
views, and the seller's name, rating, reviews and tenure. JSON or CSV, and a
run-metadata sidecar that says whether the result is complete.

Three engines — **Playwright** (primary), **pyppeteer** and **Selenium** — and
the **Scraping Browser API** over CDP, which is the transport this site
actually needs.

---

## Example output

Two records from [`sample_output.json`](sample_output.json), a real
`--mode listing` run on 2026-10-06 — every column, in the output's order
(seller names were replaced with placeholders in the sample):

```json
[
  {
    "source": "avito.ru",
    "scraped_at": "2026-10-06T11:43:07.639160+00:00",
    "url": "https://www.avito.ru/moskva/telefony/iphone_17_pro_512_gb_8286114224",
    "sku": "8286114224",
    "title": "iPhone 17 Pro, 512 ГБ",
    "image_url": "https://c70.img.avito.st/image/1/1.gxuaVbaAL_KM9Y3zkF_ZKYD1LfYo4ivyKIVO9ihWLlAv9ivojPWN8yw.riB6BxxslUpfw7lj9IO8mEk6HWk14cv7Kwz98zqvx-k",
    "price": 103090.0,
    "currency": "RUB",
    "category": null,
    "price_source": "dom",
    "original_price": null,
    "discount_pct": null,
    "region_slug": "moskva",
    "city": "Москва",
    "location": "Фили, 6–10 мин.",
    "summary_params": "Новый",
    "published_at": null,
    "published_text": null,
    "seller_name": "Продавец 2",
    "seller_type": null,
    "seller_id": "seller2",
    "seller_url": "https://www.avito.ru/brands/seller2",
    "seller_rating": 5.0,
    "seller_reviews": 706,
    "promoted": true,
    "badges": [
      "Реквизиты проверены"
    ],
    "delivery": null,
    "page": 1,
    "position": 2
  },
  {
    "source": "avito.ru",
    "scraped_at": "2026-10-06T11:43:07.656682+00:00",
    "url": "https://www.avito.ru/moskva/telefony/iphone_17_256_gb_sim_esim_8245354778",
    "sku": "8245354778",
    "title": "iPhone 17, 256 ГБ, SIM + eSIM",
    "image_url": "/static/buyer-pages-mfe/global/../assets/216162157c76b087.svg",
    "price": 62990.0,
    "currency": "RUB",
    "category": null,
    "price_source": "dom",
    "original_price": null,
    "discount_pct": null,
    "region_slug": "moskva",
    "city": null,
    "location": "Багратионовская, 6–10 мин.",
    "summary_params": "Новый",
    "published_at": null,
    "published_text": null,
    "seller_name": "Продавец 1",
    "seller_type": null,
    "seller_id": "seller1",
    "seller_url": "https://www.avito.ru/brands/seller1",
    "seller_rating": 5.0,
    "seller_reviews": 624,
    "promoted": true,
    "badges": [
      "Реквизиты проверены",
      "яПомогаю"
    ],
    "delivery": null,
    "page": 1,
    "position": 8
  }
]
```

Full sample: [`sample_output.json`](sample_output.json) · 10 rows, collected
2026-10-06 (listing mode, Moscow «Мобильные телефоны → Apple», Playwright
over the Scraping Browser API).

These two came from the markup rendering (`price_source: dom`), which states
no old price, timestamp or seller type; `city` is only printed on some cards.
See [Traps that look like bugs](#traps-that-look-like-bugs) and
[What you get](#what-you-get) for every column and the `--mode item` and
`--mode seller` outputs.

## Requirements and access

| | |
|---|---|
| Runtime | Python 3.9 or newer (the offline suite runs on 3.9 and 3.12 in CI); Playwright with its Chromium (recommended), pyppeteer or Selenium (needs a local Chrome), one per virtualenv |
| Coverage | avito.ru (Russia, prices in RUB): category and search listings, item pages, and seller profiles with all their listings. Text stays in Russian as the site prints it. Private sellers are anonymised by Avito itself; phone numbers are never collected. See [Coverage and limitations](#coverage-and-limitations) |
| Required for the documented scenario | **A Russian residential exit** (`AVITO_PROXY`), **a real browser**, and **a 2Captcha key** (`TWOCAPTCHA_KEY`) for the GeeTest wall when it appears. Recommended transport: the **2Captcha Scraping Browser API** (`AVITO_CDP_ENDPOINT`), built on that proxy. There is no path without paid products: a plain HTTP client was refused 6 of 6 times |
| Optional: 2Captcha fingerprint | `--fingerprint` on a local Playwright browser; a separate subscription; no access difference measured with and without it |
| Cost per solve | GeeTest through `GeeTestTask`: $0.00299 per solve (2026-10-06); at most one paid solve per page per run |
| Measured | Access and every mode: 2026-10-06; search: 2026-10-10 (v1.0.2); Scraping Browser, 11 runs, no wall: 2026-10-19; local browser with a fingerprint: 2026-11-05; datacentre refusal re-checked 2026-11-18 at `aab4df4`. See [Verification](#verification) |

## Quick start

```bash
git clone https://github.com/2scraper/avito-scraper && cd avito-scraper
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-playwright.txt
playwright install chromium
cp .env.example .env    # fill in TWOCAPTCHA_KEY and AVITO_PROXY (a Russian residential proxy)

# build the Scraping Browser API endpoint on that proxy and write it into .env
python3 tools/browser_profile_client.py accounts
python3 tools/browser_profile_client.py connection --account-id <id> --mint-session --write-env

python3 playwright_scraper.py --mode listing \
  --url https://www.avito.ru/moskva/telefony/mobilnye_telefony/apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc \
  --pages 1 --out live/iphones
```

Credentials go in `.env` only, never on the command line; see
[Credentials go in `.env`](#credentials-go-in-env-never-on-the-command-line).
It writes three files (`--out` names the prefix and creates the directory):

* `live/iphones.json` and `live/iphones.csv` — one row per card;
* `live/iphones.meta.json` — whether the run is complete, and why it stopped;
  `live/iphones.latest_attempt.meta.json` is written on every ending, with the
  walls met and every solve with its cost.

If the run does not end with exit 0:

* **exit 3, blocked** — the firewall stayed up: check that you are on a
  Russian exit and that the solve leaves from the browser's own IP (the
  Scraping Browser API does this). See
  [The one thing you actually need](#the-one-thing-you-actually-need).
* **exit 4, zero results** — the listing was read to its end and is empty,
  including a search whose only cards are «Похожие объявления» substitutes.
* **exit 5 or 6, not obtained or partial** — 5 is the transport, not the
  firewall: a refused endpoint login (`deny_no_user`) or `500 proxy_error`
  means the endpoint or its session expired, so run the `connection` command
  again. 6 means some pages were fetched and the rest were not. See
  [Exit codes](#exit-codes).

Setup, `.env` and exit codes for the whole family:
[How to run the 2scraper scrapers](https://2captcha.com/h/how-to-run-2scraper-open-source-scrapers).

## Coverage and limitations

The short version:

* Listings, item pages and seller profiles on avito.ru; never anything behind
  login, phone numbers, or the IMEI some categories list.
* URLs that avito.ru's `robots.txt` disallows — sort (`?s=`), filters
  (`?f=`), `?radius=`, `?view=` — are refused before fetching: narrow a
  listing by path instead ([Narrow by path](#narrow-by-path-not-by-filter)).
* `--concurrency` above 1 is refused; the default `--delay` is 2 seconds.
* `original_price`, `discount_pct`, `published_at`, `seller_type` and
  `delivery` come only from the JSON rendering; a markup-rendered page
  leaves them empty.
* A run covers the first N pages of a listing that avito re-orders all the
  time, so a listing missing from a later run has moved, not necessarily
  gone ([Comparing two runs](#comparing-two-runs)).
* Private persons' names are anonymised by Avito; company and shop names are
  in the output, and the law where you operate applies to them.

### Traps that look like bugs

* **The bare price field is the OLD price.** On a discounted item Avito's
  `priceDetailed.value` / `item.price` say 69 989 while the page shows 62 990.
  `price` is always the displayed one; `original_price` is the site's own old
  figure. All three sources (JSON, DOM microdata, item page) agreed on the
  displayed price on 50 of 50 cards.
* **One URL, two renderings.** The same listing comes back as plain markup or
  with an embedded JSON state (seen right after the firewall). The JSON one
  states old prices, timestamps and seller type; the markup one does not.
  `price_source` says which a row came from, and `diff_runs.py` does not report
  that difference as a price change.
* **A search with no matches returns fifty cards.** They sit under «Похожие
  объявления рядом и в других городах» — substitutes, not results. The run
  reports the page as empty (exit 4) instead of a complete-looking answer to a
  different question.
* **`seller_type` is empty on many cards.** A card states «Компания» or has no
  profile link (private); a card with a link but no label is not evidence of
  either — one such seller was a shop.
* **`city` is often empty in the markup rendering.** It is only printed in a
  photo's alt text, on eagerly-loaded photos (7 of 50 cards). Group by
  `region_slug`, which every row has.

## Examples

- [How to scrape Avito: step-by-step guide](https://2captcha.com/h/how-to-scrape-avito)
- [Avito scraper on 2scraper.com](https://2scraper.com/scrapers/avito-scraper/) — what it collects, requirements and related scrapers on one page.
- [How to run the 2scraper scrapers: setup, proxies, exit codes and access modes](https://2captcha.com/h/how-to-run-2scraper-open-source-scrapers) — installation, `.env`, `.meta.json` for the whole family.


Commands for a category, a search, item pages and a seller profile are under
[Usage](#usage).

## Verification

The documented scenario needs a Russian residential proxy, a 2Captcha key and
the Scraping Browser API, so the served path was **not re-run in this pass**;
the last served runs are in [Measured results](#measured-results).

| Date, commit | Check | Conditions | Result |
|---|---|---|---|
| 2026-11-18, `aab4df4` | Quick start install | a fresh virtualenv, Python 3.13, `requirements.txt` + `requirements-playwright.txt` (Playwright 1.65.2) | installs; every flag in the Quick start and Usage commands is in `--help` |
| 2026-11-18, `aab4df4` | The Quick start listing, `--pages 1 --max-products 20 --solve-captcha never` | local Chromium, no proxy, no key, no `.env`, one datacentre address | first response HTTP 429, the captcha wall, `stop_reason: blocked`, exit 3, no rows written, no solve bought — the refusal described under [The one thing you actually need](#the-one-thing-you-actually-need) |
| 2026-11-18, `aab4df4` | Offline suite | `python3 smoke_test.py` and `python3 .github/ci_checks.py --all`, core requirements only | passed |
| 2026-11-05 | One page with a cached fingerprint | Playwright, local Chromium 156, a residential proxy, `--pages 1 --max-products 20 --solve-captcha never` | 20 rows, exit 0, no wall met |
| 2026-10-19 | Every mode, 11 runs | Playwright over the Scraping Browser API | no wall met, no solve bought |
| 2026-10-10, v1.0.2 | The search in Usage, `--pages 2` | Scraping Browser API; local Chromium through the proxy | 100 rows, exit 0, on each |
| 2026-10-06 | Category, item and seller modes | Scraping Browser API (Playwright, pyppeteer); local Chrome (Selenium) | 147 rows over 3 pages, 5 of 5 items, a seller profile with 15 listings; all exit 0 |
| 2026-10-24 to 2026-11-17 | [Canary](https://github.com/2scraper/avito-scraper/actions/workflows/canary.yml) (daily, 3 pages over the Scraping Browser API) | a GitHub runner with the repository's secrets | last green on 2026-10-23 (`fdc24c0`). All 26 runs since failed before reading the site: the Scraping Browser API refused the endpoint login stored in the `AVITO_CDP_ENDPOINT` secret (`deny_no_user`, and `deny_bad_password` from 2026-11-05). These runs say nothing about avito.ru |

> **What the canary badge means.** The canary runs once a day (cron
> `23 2 * * *`, UTC; GitHub may start it later) and scrapes three pages of
> avito.ru through the Scraping Browser API on a Russian exit. It is green only
> when that run is complete and its rows pass the data checks. It needs the
> secrets `TWOCAPTCHA_KEY` and `AVITO_PROXY`, plus either the secret
> `AVITO_CDP_ENDPOINT` or the variable `AVITO_BROWSER_ACCOUNT_ID`; without them
> it is **red**, naming what is missing. It is never skipped to green. A
> blocked run, one that never reached the site, or zero rows is red too. A run
> that exits 5 gets ONE retry on a fresh proxy session (a refused connection
> or a page load that died on the exit), except when the Scraping Browser API
> refused the endpoint's login (`deny_no_user`, `deny_bad_password`): a new
> session cannot fix a stale credential, so the job names the secret to
> replace. The live measurements are in [Measured results](#measured-results),
> with their dates.

---

## The one thing you actually need

**A real browser on a Russian exit — and, more often than not, a captcha
solve.** Everything below was measured on **2026-10-06** through a Russian
residential exit, unless a row gives another date.

avito.ru sits behind its own firewall, «Доступ ограничен: проблема с IP».
Its own text says why: *too many requests from this address*. It answers in
one of these ways:

| What you meet | Status | What clears it |
|---|---|---|
| a proof-of-work page | 439 | the browser itself, in a few seconds — the page runs `/web/3/firewallPow/…` and reloads |
| a captcha page, «Продолжить» → **GeeTest v4** slide puzzle | 429 | a solve submitted through the page's own form |
| «подождите немного и обновите страницу» — no captcha, no proof-of-work | 429 | a different exit. Measured: this is what follows a **rejected** solution |

The captcha page can carry two other captcha types, one of them Avito's
own picture captcha. The server picks one per request; **every request
measured (2026-10-06 to 10-09) got GeeTest**, including the page's own
"new picture" request. Both other types are implemented from the wall's
code and 2Captcha's task formats (the picture captcha through
`ImageToTextTask`); neither has been met live.

| Client | Result |
|---|---|
| `curl` with full browser headers, 3 fresh exits × 2 URLs | **6 of 6 HTTP 429** — a plain HTTP client never gets in |
| local Chromium (Playwright), 5 fresh exits | **5 of 5 met the GeeTest wall** |
| Scraping Browser API, fresh profile cookies | met both walls; the proof-of-work cleared itself |
| … the GeeTest wall, Scraping Browser auto-solve | **0 of 3** — `Captcha.detected`, then `Captcha.solveFailed` |
| … the GeeTest wall, 2Captcha `GeeTestTask` through the **browser's own exit** | **3 of 3** `verified`, 36–73 s, $0.00299 each |
| … the same, local Chromium | **2 of 3** (one `ERROR_CAPTCHA_UNSOLVABLE`) |
| … the same, solved **without** the browser's proxy | **0 of 1** — the site answered with a second captcha |
| … the same, local Chromium, every solve 2026-10-14 to 2026-10-20 | **0 of 15** `verified` — the site served the captcha or the wait wall again after each |
| Scraping Browser API, 11 runs of every mode, 2026-10-19 | **met no wall at all**; no solve was bought |
| the wall, left unsolved | 0 of 3 cleared on its own within 140 s |

So: the solve has to go through **the same IP the browser uses**. The
scraper passes the browser's own exit as the task's proxy and refuses to buy
a solve without one. **But in a control on 2026-10-14 the solver did not use
that proxy:** `GeeTestTask` returned a solution for a proxy with a wrong
password and for a proxy host that does not exist. Local-browser solves
have been refused since (0 of 15 above).
That the token comes from another IP is the likeliest reason, not a proven
one. **Use the Scraping Browser:** once a profile has passed the wall, its
cookies carry it — a dozen consecutive pages of listings, items and seller
profiles came back without meeting it again (2026-10-06), and the 2026-10-19
test met no wall in 11 runs.

There is **no paid-product-free path** on this site: no plain HTTP route, and
a local browser met the captcha on 5 of 5 fresh exits (2026-10-06).

---

## Install

```bash
git clone https://github.com/2scraper/avito-scraper && cd avito-scraper
pip install -r requirements.txt
pip install -r requirements-playwright.txt && playwright install chromium
# or  pip install -r requirements-selenium.txt      (needs a local Chrome)
# or  pip install -r requirements-puppeteer.txt
cp .env.example .env    # then fill it in — see below
```

Install **one** engine per virtualenv: playwright and pyppeteer declare
mutually unsatisfiable pins.

### Credentials go in `.env`, never on the command line

`ps` reads argv, and argv lands in shell history and CI logs.

| Variable | What it is |
|---|---|
| `TWOCAPTCHA_KEY` | 2Captcha API key — solves the firewall's GeeTest |
| `AVITO_CDP_ENDPOINT` | Scraping Browser API endpoint (Playwright, pyppeteer) |
| `AVITO_PROXY` | a Russian residential proxy — see below |
| `AVITO_URL` | optional default `--url` |

**The endpoint and the proxy are built together**, because the captcha solver
must leave from the browser's IP:

```bash
python3 tools/browser_profile_client.py accounts
python3 tools/browser_profile_client.py connection --account-id <id> --mint-session --write-env
```

That mints a fresh session on your `AVITO_PROXY` gateway credential, has the
API build the endpoint on it, and writes **both** into `.env`. A session lives
`-sessTime-` minutes (120 by default); an endpoint built on an expired one
answers `500 proxy_error` on connect — run the command again.

For a local browser (no endpoint), `AVITO_PROXY` is the browser's exit; a
gateway login without `-session-` is pinned to one session for the run
automatically.

`AVITO_PROXY` must carry the gateway's login and password, in either form:

```text
http://LOGIN:PASSWORD@HOST:PORT     # a URL
HOST:PORT:LOGIN:PASSWORD            # as the 2Captcha dashboard exports it
```

The second form may also start with `http://`; it is rewritten to the first,
with the login and password percent-encoded, so a password containing `@`,
`:` or `/` works in either. A gateway URL without a login (the IP-whitelisted
form, `http://HOST:PORT`) is refused with a message saying
so: the `-session-` and `-sessTime-` ids that pin an exit live inside the
login, so such a URL cannot be minted or pinned.

`python3 env_config.py` shows what was picked up, without printing secrets.

---

## Usage

```bash
# A category listing, three pages (?p=N), JSON and CSV
python3 playwright_scraper.py --mode listing \
  --url https://www.avito.ru/moskva/telefony/mobilnye_telefony/apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc \
  --pages 3 --out live/iphones

# A search
python3 playwright_scraper.py --mode listing --url "https://www.avito.ru/moskva?q=iphone+15" --pages 2

# Item pages for what a listing run found (repeat --url, or --from a run's JSON)
python3 playwright_scraper.py --mode item --from live/iphones.json --max-products 20 --out live/items

# A seller profile and all its listings (read from the seller's feed)
python3 playwright_scraper.py --mode seller --url https://www.avito.ru/brands/<seller> --out live/seller
```

`--verbose` narrates each page: readiness, the wall met, which rung cleared it.

### A 2Captcha fingerprint on a local browser (Playwright)

```bash
python3 playwright_scraper.py --fingerprint --fp-country ru --mode listing \
  --url https://www.avito.ru/moskva/telefony/mobilnye_telefony/apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc
```

`--fingerprint` fetches one profile from the
[2Captcha Fingerprint API](https://2captcha.com/fingerprints/api) with
`TWOCAPTCHA_KEY` and applies it to the Chromium this scraper launches: user
agent together with its client hints (`Sec-CH-UA*`, `navigator.userAgentData`),
locale and `Accept-Language`, timezone, screen and window size, device scale
factor, `navigator.platform` / `hardwareConcurrency` / `deviceMemory` and the
WebGL vendor and renderer strings. After the first page the scraper reads
these back from the live page and records them, with any mismatch, under
`transport_facts.fingerprint` in the run metadata.

* **Local browser only.** The Scraping Browser API brings its own
  fingerprint, so a typed `--cdp-endpoint` with `--fingerprint` exits 2, and an
  `AVITO_CDP_ENDPOINT` from `.env` is ignored for that run (with a notice).
  Selenium and pyppeteer do not implement `--fingerprint` and refuse it (exit 2).
* **A separate subscription.** A key without a fingerprint plan is answered
  `403 ERROR_FINGERPRINT_PLAN_REQUIRED`; the run then exits 5 before any page
  is fetched.
* `--fp-tags` takes ONE tag (default `Windows`). `--fp-country` should match
  the proxy's exit. The response is cached in
  `~/.cache/2captcha-fingerprints/` per set of flags, so the same flags give the
  same identity (and no new charge); `--fp-refresh` fetches a new one.
* What it does not change: the browser engine underneath is still the
  installed Chromium, which may be a different version from the one the
  profile names, and WebGL output is the real GPU's. It is a consistent
  identity, not a guarantee against the firewall — no access difference has
  been measured with and without it.

### Narrow by path, not by filter

avito.ru's `robots.txt` disallows `?s=` (sort), `?f=` (filters), `?radius=`,
`?view=` and `/items/phone`. The scraper refuses such URLs before fetching,
naming the rule. Narrow a listing the way the site's own URLs do: city →
category → subcategory (`/moskva/telefony/mobilnye_telefony/apple-…`). `?q=`
search and `?p=` pages are allowed.

### Pagination

`?p=N`, built by the scraper (measured: plain `?p=2` returned 50 new ids).
The run stops at the site's own last page, when a page adds no new item, or
at `--max-products`. Avito repeats some promoted cards across pages; they are
deduplicated in page order.

---

## What you get

### `--mode listing` — one row per card

The first ten columns are this family's common prefix, identical in every
2scraper repo.

| Column | Example | Notes |
|---|---|---|
| `source`, `scraped_at`, `url` | `avito.ru`, ISO time, item URL | URL without tracking query |
| `sku` | `8245354778` | Avito's item id |
| `title`, `image_url` | «iPhone 17, 256 ГБ, SIM + eSIM» | |
| `price`, `currency` | `62990.0`, `RUB` | the **displayed** price — see Traps |
| `category`, `price_source` | | `state` (embedded JSON) or `dom` |
| `original_price`, `discount_pct` | `69989.0`, `10.0` | the site's own figures, JSON rendering only |
| `region_slug`, `city` | `moskva`, «Москва» | `region_slug` on every row; `city` where stated |
| `location` | «Багратионовская, 6–10 мин.» | metro / district as printed |
| `summary_params` | «Новый», «Б/у» | the card's one-line summary, category-specific |
| `published_at`, `published_text` | ISO / «9 сентября в 12:57» | as the rendering states it |
| `seller_name`, `seller_type`, `seller_id`, `seller_url` | «…», `company` | see "Seller names" |
| `seller_rating`, `seller_reviews` | `5.0`, `624` | |
| `promoted`, `badges`, `delivery` | `true`, [«Скидка»] | `delivery` from the JSON rendering only |
| `page`, `position` | `2`, `17` | |

### `--mode item` — everything above, plus

`description`, `params` (all characteristics, e.g. `{"Модель": "iPhone 17",
"Встроенная память": "256 ГБ", "Состояние": "Новое"}`), `images` (all photos,
largest size), `category_path`, `address`, `lat`, `lng`, `views_total`,
`views_today`, `seller_since` («мая 2019»), `seller_reply_time`, `is_active`.

### `--mode seller`

`<out>.json`: `seller_name`, `seller_rating`, `seller_reviews`,
`seller_since`, `subscribers`, `active_ads` (the profile's own counter),
`listings_on_page`, `badges`. `<out>_listings.json`: **all** the seller's
listings, as listing rows, up to the profile's own counter or
`--max-products`. The profile page renders 15; the rest come from the
seller's feed (`/brands/<slug>/all?sellerId=…`), which the scraper opens,
arms with its «Показать все» button and scrolls — 15 more per scroll.
Measured 2026-10-09: **353 of 353** in 22 scrolls, every row priced.
A shop whose profile carries no `sellerId` gets it from
`/brands/<slug>/items` (robots allows that path; measured 2026-10-10 on a
shop with 7331 listings). A run that wrote fewer listings than the
profile's own counter ends **partial, exit 6** — compare `listings_on_page`
with `active_ads`; `transport_facts.seller_feed` says why it stopped.

**About robots.txt and the seller feed.** The scraper never requests a path
avito.ru's robots.txt disallows. But scrolling the feed makes the PAGE'S OWN
script fetch `/web/1/profile/items`, and `/web/` is disallowed for robots.
That is a deliberate choice (owner, 2026-10-09) — the only way to a
seller's listings beyond 15. Use `--max-products 15` to stay on the
profile page alone.

### Seller names, and what is never collected

Companies and shops are named. **Private persons are anonymised by Avito
itself** — an item page shows «Пользователь» and the card carries no profile
link — so for them `seller_name` is empty and `seller_type` is `private`.

Never read, never exported: phone numbers (behind login; the page carries a
masked one), the IMEI some categories list (masked, dropped by name), anything
behind login.

The output contains sellers' names. If you store or process it, the law where
you operate applies to that data — in Russia, Federal Law 152-FZ.

---

## Engines

| Engine | Scraping Browser API | Local browser + proxy |
|---|---|---|
| `playwright_scraper.py` | yes — **recommended** | yes |
| `puppeteer_scraper.py` | yes | yes |
| `selenium_scraper.py` | **no** — chromedriver's `debuggerAddress` has nowhere to put the endpoint's password (a typed `--cdp-endpoint` exits 2; `AVITO_CDP_ENDPOINT` from `.env` is ignored with a notice) | yes |

All three share one CLI and one run loop (`browser_bridge.py`), so they agree
on exit codes and on when to wait, solve or rotate. Live-verified on
2026-10-06: Playwright (listing, item, seller) and pyppeteer (listing) over
the Scraping Browser API; Selenium on a local Chrome, including a GeeTest
wall solved mid-run.

**Selenium and an authenticated proxy.** Chrome's `--proxy-server` carries no
credential, and WebDriver BiDi's `add_auth_handler` deadlocked on Selenium
4.50 (measured). So Chrome is pointed at `proxy_forwarder.py`, a loopback
proxy inside the scraper process that adds the credential upstream — it never
reaches argv or disk. pyppeteer passes it through `page.authenticate`,
Playwright through its own `proxy` fields.

pyppeteer is effectively unmaintained; it is here for parity.

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Complete |
| `1` | Crash — an unexpected exception: a bug, not the site and not the transport (traceback printed, credentials masked) |
| `2` | Bad usage — wrong host, wrong `--mode` for the URL, a robots-disallowed URL, an unreadable `--from` file |
| `3` | Blocked — the firewall stayed up after every rung |
| `4` | Zero results — the listing was read to its end and is empty, including a search with no matches |
| `5` | Content never obtained — **our** plumbing (a navigation timeout, a dead exit, `profile_locked` still held after 3 retries 10 s apart, `proxy_error`), or zero rows for any reason other than a listing read to its end: a failed page, an item page with no item on it, a seller profile that never parsed |
| `6` | Partial — some pages fetched, then stopped (a connection that dropped AFTER them keeps them: the rows are written); in `--mode seller`, fewer listings written than the profile's own counter |

A page buys at most one paid captcha solve per run
(`page_flow.SOLVES_PER_PAGE`): a fresh exit re-fetches a page that stayed
walled, but does not buy a second token for it.

`3` and `5` mean different things: `5` says the content was never obtained,
so the answer is "run it again", "rebuild the endpoint" or "check the URL",
not the firewall. `4` is only for a run that read the listing to its end.

A run that finds nothing writes nothing — last night's output is not replaced
with `[]` (`--allow-empty` is the opt-out). Output files get the mode
`open()` would give them under your umask, and an existing file keeps its
own. In the CSV, a text cell starting with `=`, `+`, `-`, `@`, a tab or a
line break is prefixed with `'` so a spreadsheet does not run it as a
formula; the JSON keeps the site's text, and `csv_cells_escaped` in the
sidecar counts the cells that differ. `<out>.meta.json` describes the
data beside it; `<out>.latest_attempt.meta.json` is written on EVERY ending — success, block, transport error or crash, with `exit_code` and `error_class` — and
records `transport_facts`: walls met, which rung cleared each, every solve
with its time and cost, rotations, and auto-solve events.

---

## Measured results

2026-10-06, Moscow category «Мобильные телефоны → Apple».

| Run | Result |
|---|---|
| Playwright, Scraping Browser, `--mode listing --pages 3` | 147 rows, 3 pages, exit 0 |
| Playwright, Scraping Browser, `--mode item`, 5 items | 5 of 5, exit 0 |
| Playwright, Scraping Browser, `--mode seller` | profile + 15 listings, exit 0 |
| pyppeteer, Scraping Browser, `--pages 2` | 98 rows, exit 0 |
| Selenium, local Chrome, `--pages 2` | 99 rows, exit 0 — page 2 met the wall: proof-of-work did not clear in 25 s, a fresh exit met GeeTest, solved in 92 s |

2026-10-10, v1.0.2, the search in Usage (`?q=iphone+15 --pages 2`):

| Run | Result |
|---|---|
| Playwright, Scraping Browser | 100 rows, exit 0 — both pages arrived as a skeleton first (ready after 500 and 1000 ms) |
| Playwright, local Chromium through the proxy | 100 rows, exit 0 — proof-of-work cleared in 1 s, then GeeTest: the first token was refused, a fresh exit's was accepted (15 s and 26 s, $0.00299 each) |

2026-11-05, `--fingerprint --fp-country de`, Playwright, local Chromium 156
through a residential proxy, the category above, `--pages 1 --max-products 20
--solve-captcha never`: 20 rows, exit 0, no wall met. The account had no
fingerprint plan that day (`403 ERROR_FINGERPRINT_PLAN_REQUIRED`), so the
profile was a real API response from 2026-09-30 served from the cache. Read
back from the page: user agent, client-hint brands and platform, `de-DE`,
`Europe/Berlin`, `Win32`, 20 cores, 16 GB, 1366×768 — no mismatch.

`sample_output.json` is cut from the 3-page run (10 rows; seller names
replaced with placeholders).

---

## Comparing two runs

```bash
python3 diff_runs.py --old live/iphones.2026-10-06.json --new live/iphones.2026-10-13.json
```

Added / removed / changed by `sku`. Refuses runs that are incomplete, of
different modes, or that covered a different listing, page count or
`--max-products` cap — every row outside the overlap would read as added or
removed (exit 2; `--force` compares anyway).

**"Removed" means "not in these pages any more", not "delisted".** A run
covers the first N pages of a listing, and avito re-orders a listing all
the time (raised ads, fresh ads). Two runs of the same 3-page category
(2026-10-19) differed by 47 added and 42 removed out of 145: listings that
moved past page 3, not listings that were taken down. To tell the two
apart, run `--mode item` on the removed ones — an ad that is gone answers
exit 5 (no item data on the page).

---

## Testing

```bash
python3 smoke_test.py          # the offline suite: no network, no browser
python3 -m pytest -q           # the same suite, as one pytest test
python3 .github/ci_checks.py --all
```

Fixtures are cut from real captures and scrubbed (`fixtures/README.md`). The
suite passes with no engine installed; CI also runs it once per engine in its
own venv and builds the Docker image.

---

## Troubleshooting

[TROUBLESHOOTING.md](TROUBLESHOOTING.md), organised by symptom.

---

## Do you need the paid products?

**Yes, on this site — measured, not assumed.** There is no plain-HTTP route
(6 of 6 refused) and a local browser met the captcha on 5 of 5 fresh exits
(2026-10-06).

* **[Proxies](https://2captcha.com/proxy)** — `AVITO_PROXY`. A Russian
  residential exit, session-pinned, so the browser and the solver share one IP.
* **Scraping Browser API** — `AVITO_CDP_ENDPOINT`. The recommended transport:
  it clears the proof-of-work variant by itself and keeps the clearance in a
  persistent profile, so most pages after the first never meet the wall.
* **[Captcha solving](https://2captcha.com)** — `TWOCAPTCHA_KEY`. GeeTest v4
  through `GeeTestTask`: 3 of 3 on the Scraping Browser (2026-10-06),
  $0.00299 each. On a local browser the solutions were refused 0 of 15
  (2026-10-14 to 10-20) — see [the one thing you actually need](#the-one-thing-you-actually-need).
  At most one paid solve per page per run.
* **[Fingerprints](https://2captcha.com/fingerprints/api)** — `--fingerprint`
  (Playwright, local browser), billed as a separate subscription on the same
  key. Not used over CDP, where the remote browser brings its own. See
  [the fingerprint section](#a-2captcha-fingerprint-on-a-local-browser-playwright).

---

## FAQ

### Does Avito have a public API?

This repository does not use or need one. It reads avito.ru's public
listing, item and seller pages in a real browser — a plain HTTP client was
refused 6 of 6 times — and takes the data from the page's embedded JSON
state or its markup (`price_source` says which).

### Is it legal to scrape avito.ru?

This is not legal advice. Scraping public pages is permitted in many
jurisdictions, but avito.ru's terms of use, copyright in listing texts and
photos, and data-protection law differ between countries. The output
contains company and shop names; in Russia, Federal Law 152-FZ applies to
such data. The scraper reads public pages only, does not log in or open
contact details, respects `robots.txt` for every URL it requests (with the
one seller-feed exception stated under [What you get](#what-you-get)), and
waits 2 seconds between pages by default.

### Why does Avito block headless browsers or datacentre IPs?

The wall is Avito's own firewall, and its own text gives the reason: too
many requests from this address. Measured through Russian residential exits
on 2026-10-06, `curl` with full browser headers was refused 6 of 6 times and
a local Chromium met the GeeTest wall on 5 of 5 fresh exits; a datacentre
address with no proxy got HTTP 429 and the captcha wall on 2026-11-18. A
Scraping Browser API profile that has passed the wall keeps its cookies:
the 2026-10-19 test met no wall in 11 runs.

### Do I need a Russian proxy and a 2Captcha key?

Yes, measured: there is no path without paid products on this site. You need
a Russian residential exit (`AVITO_PROXY`), a 2Captcha key for the GeeTest
wall (`TWOCAPTCHA_KEY`, $0.00299 per solve on 2026-10-06, at most one per
page), and preferably the Scraping Browser API (`AVITO_CDP_ENDPOINT`), built
on the same proxy so that the solve leaves from the browser's IP. See
[Do you need the paid products?](#do-you-need-the-paid-products).

### How do I run the Avito scraper on a server?

As in the [Quick start](#quick-start): put `TWOCAPTCHA_KEY` and `AVITO_PROXY`
in `.env`, build the endpoint with `tools/browser_profile_client.py
connection --mint-session --write-env`, and run Playwright or pyppeteer
against it; no local display is needed. A proxy session lives 120 minutes by
default, and an endpoint built on an expired one answers `500 proxy_error`,
so rebuild it before each scheduled run. Branch on the exit code and on
`status` in `<out>.meta.json`.

### What fields does the scraper return?

`--mode listing`: 29 columns per card — `source`, `scraped_at`, `url`,
`sku`, `title`, `image_url`, `price`, `currency`, `category`,
`price_source`, `original_price`, `discount_pct`, `region_slug`, `city`,
`location`, `summary_params`, `published_at`, `published_text`,
`seller_name`, `seller_type`, `seller_id`, `seller_url`, `seller_rating`,
`seller_reviews`, `promoted`, `badges`, `delivery`, `page`, `position`.
`--mode item` adds the description, all parameters, all photos, address and
coordinates, views and seller tenure; `--mode seller` writes the profile and
all its listings. Details: [What you get](#what-you-get).

### How often does it break when avito.ru changes?

A daily canary scrapes three pages over the Scraping Browser API and is red
on a blocked, partial or empty run. It has been failing on its stored
endpoint credential since 2026-10-24 (see [Verification](#verification)), so
it is not watching the site right now. Changes made because the site changed
are recorded, with the measurement behind each, in
[CHANGELOG.md](CHANGELOG.md).

## Related scrapers

Same CLI conventions, same output contract (one row per item, JSON or CSV, a `.meta.json` sidecar and shared exit codes):

- [Craigslist scraper](https://github.com/2scraper/craigslist-scraper) — Result lists and adverts from all 714 areas
- [dubizzle scraper](https://github.com/2scraper/dubizzle-scraper) — UAE classifieds — motors, property, electronics, jobs…
- [Facebook Marketplace scraper](https://github.com/2scraper/facebook-marketplace-scraper) — Marketplace listings by keyword, category, location…
- [MakeMyTrip scraper](https://github.com/2scraper/makemytrip-scraper) — Hotel prices with taxes and fees, ratings, stars…
- [Skyscanner scraper](https://github.com/2scraper/skyscanner-scraper) — Route and dates in — every itinerary with price, airline…
- [Spinny scraper](https://github.com/2scraper/spinny-scraper) — Used-car listings and car detail pages

All scrapers: [github.com/2scraper](https://github.com/2scraper) · Need another site? [Open a request](https://github.com/2scraper/.github/issues/new?title=%5BScraper%20request%5D%20)

## Contributing, security, licence

* [CONTRIBUTING.md](CONTRIBUTING.md) — reporting that avito.ru changed, and
  what a fix needs.
* [SECURITY.md](SECURITY.md) — reporting a vulnerability.
* [CHANGELOG.md](CHANGELOG.md) — what changed, with the measurement behind it.
* MIT — see [LICENSE](LICENSE).

---

## Legal

This repo reads **public pages**. It does not log in, does not open contact
details, and does not message sellers. When avito.ru's firewall presents a
captcha, the scraper solves it the way a visitor would — through the page's
own form — with a solve you pay 2Captcha for.

`robots.txt` is respected for every URL the scraper requests: the snapshot
is in `robots_snapshot.py`, and URLs it disallows are refused before any
request is made. One exception is stated above: scrolling a seller's feed
makes the page itself fetch the disallowed `/web/1/profile/items`.

The firewall exists because of request volume. Default `--delay` is 2 seconds
between pages, `--concurrency` above 1 is refused, and running hard from one
address is the fastest way to meet the wall on every page. Respect the site's
terms and the law where you operate — including on the personal data the
output contains.
