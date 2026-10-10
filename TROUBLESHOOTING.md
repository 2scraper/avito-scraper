# Troubleshooting

Organised by symptom. Every number here was measured on 2026-10-06; the date
is given so you can tell what has moved since. Start with
`<out>.latest_attempt.meta.json` → `transport_facts`: it records every wall
met, which rung cleared it, every solve with its time, cost and whether the
site accepted it, and every rotation.

---

## Exit 3 — «Доступ ограничен: проблема с IP»

avito.ru's own firewall. Run with `--verbose --dump-html page.html` and see
which variant you met:

| What the page holds | Status | What happens |
|---|---|---|
| a page that reloads itself, requests to `/web/3/firewallPow/…` | 439 | the browser clears it within seconds; the scraper waits 25 s |
| «Продолжить» and, after it, a GeeTest slide puzzle | 429 | the scraper solves it (below) |
| «подождите немного и обновите страницу», nothing to solve | 429 | follows a REJECTED solution; the scraper moves to a fresh exit (`--proxy-block-retries`) |
| «введите символы с картинки» — Avito's picture captcha | 429 | solved with `ImageToTextTask`; never met live yet — if you see it, `solves[].variant` is `image` and a report is welcome |
| hCaptcha («поставьте галочку») | 429 | solved with `HCaptchaTask` through the browser's exit; never met live yet — `solves[].variant` is `hcaptcha` |

`transport_facts.solves[]` names the variant of every wall met, its 2Captcha
`task_id` and, on failure, 2Captcha's own error code.

**The GeeTest wall stays up after a solve.** Look at `transport_facts.solves`:

* `"error": "no proxy for the solver …"` — the run had no proxy to give the
  solver. A token solved from another IP is **rejected by the site** (measured:
  it answered with a second captcha), so the scraper does not pay for one.
  Over `--cdp-endpoint`, `AVITO_PROXY` must be the exact session the endpoint
  was built with: `tools/browser_profile_client.py connection --mint-session
  --write-env` writes both.
* `"verified": false` after a solve — the site did not accept the token. Most
  often the proxy and the endpoint have drifted apart (one rebuilt, the other
  not). Rebuild both with the command above.
* `ERROR_CAPTCHA_UNSOLVABLE` — happened once in three local runs. Run again;
  a fresh exit gets a fresh puzzle.

**No `TWOCAPTCHA_KEY`.** The scraper says so and reports exit 3. A local
browser met the GeeTest wall on 5 of 5 fresh sessions, so without a key a
local run rarely gets anywhere.

**The Scraping Browser's auto-solve.** It is armed on every CDP connection and
measured 0 of 3 on this wall (`Captcha.detected`, then `Captcha.solveFailed`).
The scraper gives it 25 s and stops early on `solveFailed`.

**Every page walls.** The firewall is a request-RATE decision. Raise
`--delay`, do not run several processes on one exit, and for a local browser
let `--proxy-block-retries` move a walled page to a fresh session.

---

## Exit 5 — the site was never reached

| Message | Cause | Fix |
|---|---|---|
| `500 Internal Server Error … proxy_error` on connect | the endpoint's proxy session has expired (`-sessTime-`) | `browser_profile_client.py connection --mint-session --write-env` |
| `500 … profile_locked` | the profile still holds a previous connection | wait. A cleanly closed run released it within ~30 s (measured); a killed one can hold it far longer. Do **not** poll `/json/version` — that call takes the lock itself |
| `ERR_CONNECTION_CLOSED`, `ERR_CONNECTION_RESET`, `SSL_ERROR_SYSCALL` | the residential exit went away, sometimes well inside its `-sessTime-` | a local run moves to a fresh session by itself (`--proxy-block-retries`); over CDP, rebuild the endpoint |
| `Timeout … exceeded` | a slow page | `--retries` retries the same exit; raise `--timeout` |

---

## Exit 4 — zero results

* **A search with no matches.** avito.ru serves ~50 cards under «Похожие
  объявления рядом и в других городах» — substitutes, not results. Exit 4 is
  the correct answer.
* **`--mode item` and "no item data".** The item was closed or removed; the
  page no longer carries `buyerItem`.

## Exit 2 — refused before fetching

* `robots.txt says Disallow: /*?s=` (or `?f=`) — sort and filter parameters
  are disallowed by the site. Narrow by path instead: city → category →
  subcategory.
* `that is an item page, not a listing page` — pick the `--mode` that matches
  the URL.
* `--concurrency above 1 is refused` — see README, Legal.

---

## The row count is right but a column is empty

* `original_price`, `discount_pct`, `published_at`, `delivery` — stated only
  by the JSON rendering. `price_source = dom` rows do not have them. Run
  `--mode item` for the old price and date of specific items.
* `city` — often empty in the markup rendering; use `region_slug`.
* `seller_name` — private persons are anonymised by Avito itself.
* `seller_type` — empty when a card neither says «Компания» nor lacks a
  profile link; `--mode item` states it.

---

## Selenium

* `--cdp-endpoint` is refused: chromedriver cannot authenticate to it. Use
  Playwright or pyppeteer for the Scraping Browser API.
* Through a proxy, Chrome is pointed at a loopback forwarder
  (`127.0.0.1:<port>`) that adds the credential. If a page fails with
  `ERR_CONNECTION_CLOSED`, check the proxy itself first: on 2026-10-06 the
  same failure came straight from a dead residential exit with no forwarder
  involved.
