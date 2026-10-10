# avito-scraper

Scrapes avito.ru — category and search listings, item pages and seller
profiles. Prices (the price the buyer sees, with the old price and discount
where the site states them), location, condition, item parameters, photos,
views, and the seller's name, rating, reviews and tenure. JSON or CSV, and a
run-metadata sidecar that says whether the result is complete.

Three engines — **Playwright** (primary), **pyppeteer** and **Selenium** — and
the **Scraping Browser API** over CDP, which is the transport this site
actually needs.

[![release](https://img.shields.io/github/v/release/2scraper/avito-scraper?sort=semver)](https://github.com/2scraper/avito-scraper/releases)
[![tests](https://github.com/2scraper/avito-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/2scraper/avito-scraper/actions/workflows/tests.yml)
[![canary](https://github.com/2scraper/avito-scraper/actions/workflows/canary.yml/badge.svg)](https://github.com/2scraper/avito-scraper/actions/workflows/canary.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![licence](https://img.shields.io/badge/licence-MIT-lightgrey)](LICENSE)
[![engines](https://img.shields.io/badge/engines-Playwright%20%7C%20Selenium%20%7C%20pyppeteer%20%7C%20CDP-informational)](#engines)
[![needs a Russian exit](https://img.shields.io/badge/needs-a%20Russian%20exit%20%2B%20a%20browser-orange)](#the-one-thing-you-actually-need)

> **What the canary badge means.** The daily canary scrapes avito.ru only when
> the repository holds the secrets for a Russian exit and a captcha key. This
> public repository does not, so the canary takes its documented SKIP branch:
> a green badge here says the workflow is healthy, **not** that avito.ru was
> scraped today. Each skipped run says so in a warning. The live measurements
> are in [Measured results](#measured-results), with their dates.

---

## The one thing you actually need

**A real browser on a Russian exit — and, more often than not, a captcha
solve.** Everything below was measured on **2026-10-06** through a Russian
residential exit.

avito.ru sits behind its own firewall, «Доступ ограничен: проблема с IP».
Its own text says why: *too many requests from this address*. It answers in
one of these ways:

| What you meet | Status | What clears it |
|---|---|---|
| a proof-of-work page | 439 | the browser itself, in a few seconds — the page runs `/web/3/firewallPow/…` and reloads |
| a captcha page, «Продолжить» → **GeeTest v4** slide puzzle | 429 | a solve submitted through the page's own form |
| «подождите немного и обновите страницу» — no captcha, no proof-of-work | 429 | a different exit. Measured: this is what follows a **rejected** solution |

The captcha page can carry two other captchas — Avito's own picture
captcha and hCaptcha. The server picks one per request; **every request
measured (2026-10-06 to 10-09) got GeeTest**, including the page's own
"new picture" request. The picture captcha is implemented (`ImageToTextTask`)
but has never been met live. hCaptcha is solved with `HCaptchaTask`
(sitekey from the wall, through the browser's own exit) — likewise
implemented from the wall's code and 2Captcha's task format, never met live.

| Client | Result |
|---|---|
| `curl` with full browser headers, 3 fresh exits × 2 URLs | **6 of 6 HTTP 429** — a plain HTTP client never gets in |
| local Chromium (Playwright), 5 fresh exits | **5 of 5 met the GeeTest wall** |
| Scraping Browser API, fresh profile cookies | met both walls; the proof-of-work cleared itself |
| … the GeeTest wall, Scraping Browser auto-solve | **0 of 3** — `Captcha.detected`, then `Captcha.solveFailed` |
| … the GeeTest wall, 2Captcha `GeeTestTask` through the **browser's own exit** | **3 of 3** `verified`, 36–73 s, $0.00299 each |
| … the same, local Chromium | **2 of 3** (one `ERROR_CAPTCHA_UNSOLVABLE`) |
| … the same, solved **without** the browser's proxy | **0 of 1** — the site answered with a second captcha |
| the wall, left unsolved | 0 of 3 cleared on its own within 140 s |

So: the solve has to go through **the same IP the browser uses**, and this
scraper refuses to pay for one that does not. Once a profile has passed the
wall, its cookies carry it: a dozen consecutive pages of listings, items and
seller profiles came back without meeting it again.

There is **no paid-product-free path** on this site: no plain HTTP route, and
a local browser meets the captcha on every fresh session.

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

# A seller profile, plus the listings it renders
python3 playwright_scraper.py --mode seller --url https://www.avito.ru/brands/<seller> --out live/seller
```

`--verbose` narrates each page: readiness, the wall met, which rung cleared it.

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
Compare `listings_on_page` with `active_ads` to see whether the feed was
read to the end; `transport_facts.seller_feed` says why it stopped.

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

## Traps that look like bugs

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

---

## Engines

| Engine | Scraping Browser API | Local browser + proxy |
|---|---|---|
| `playwright_scraper.py` | yes — **recommended** | yes |
| `puppeteer_scraper.py` | yes | yes |
| `selenium_scraper.py` | **no** — chromedriver's `debuggerAddress` has nowhere to put the endpoint's password | yes |

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
| `2` | Bad usage — wrong host, wrong `--mode` for the URL, a robots-disallowed URL |
| `3` | Blocked — the firewall stayed up after every rung |
| `4` | Zero results — including a search with no matches |
| `5` | Transport error — **our** plumbing: a navigation timeout, a dead exit, `profile_locked`, `proxy_error` |
| `6` | Partial — some pages fetched, then stopped |

`3` and `5` mean different things: `5` says the site was never reached, so
the answer is "run it again" or "rebuild the endpoint", not the firewall.

A run that finds nothing writes nothing — last night's output is not replaced
with `[]` (`--allow-empty` is the opt-out). `<out>.meta.json` describes the
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

`sample_output.json` is cut from the 3-page run (10 rows; seller names
replaced with placeholders).

---

## Comparing two runs

```bash
python3 diff_runs.py --old live/iphones.2026-10-06.json --new live/iphones.2026-10-13.json
```

Added / removed / changed by `sku`. Refuses runs that are incomplete, of
different modes, or that covered a different listing or page count — every
row outside the overlap would read as added or removed.

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
(6 of 6 refused) and a local browser meets the captcha on every fresh session.

* **[Proxies](https://2captcha.com/proxy)** — `AVITO_PROXY`. A Russian
  residential exit, session-pinned, so the browser and the solver share one IP.
* **Scraping Browser API** — `AVITO_CDP_ENDPOINT`. The recommended transport:
  it clears the proof-of-work variant by itself and keeps the clearance in a
  persistent profile, so most pages after the first never meet the wall.
* **[Captcha solving](https://2captcha.com)** — `TWOCAPTCHA_KEY`. GeeTest v4
  through `GeeTestTask`: 3 of 3 on the Scraping Browser, $0.00299 each.
* **Fingerprints** — `fingerprint_client.py`. Not used over CDP, where the
  remote browser brings its own.

---

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
