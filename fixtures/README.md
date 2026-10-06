# Fixtures

Cut from real captures of www.avito.ru taken on 2026-10-06 through the
Scraping Browser API and a local Chromium, both on a Russian residential exit.
Each one was checked to parse to the same values as its untrimmed original,
apart from the fields scrubbed below.

| File | Source page | What it pins |
|---|---|---|
| `listing_dom.html` | category, page 2, ordinary rendering | DOM + microdata path, 4 cards, pager |
| `listing_state.html` | category, page 1, served right after the firewall | `data-mfe-state` JSON path: discount, timestamp, seller type, a private seller |
| `listing_empty.html` | search with no matches | substitute cards under «Похожие объявления…» are not results |
| `item_company.html` | item page, company seller | `__staticRouterHydrationData` → `buyerItem` |
| `item_private.html` | item page, private seller | anonymised seller, masked IMEI dropped |
| `seller.html` | `/brands/…` profile | profile counters, 2 of its cards |
| `wall_captcha.html` | the firewall (HTTP 429) after «Продолжить» | GeeTest v4 form + `captchaId` |

## Not verbatim

* **Seller names and profile slugs** are replaced with `Продавец N` /
  `sellerN`. The scraper collects real names at run time; this repository does
  not republish them.
* **Free text** (listing and item descriptions) is replaced with a placeholder
  sentence. A private person's words are theirs.
* **Addresses** on item pages become `Москва, ул. Примерная, 1`; seller-logo
  alt text becomes `Москва`.
* **Session and tracking material** — `context=…` tokens, `userKey`,
  `searchHash`, `itemExportKey`, `userHashedId`, `nonce`, `iid=` — is blanked.
* **Item pages** keep only the `buyerItem` keys the parser reads, re-encoded
  the way the site encodes them (`JSON.parse("…")`). The masked phone and IMEI
  are kept, re-masked, so the suite can prove they never reach a row.
* **The wall** keeps its form and the one inline script carrying `captchaId`.
  That id is public (every visitor's browser receives it) and is allow-listed
  by value in `tools/scan_secrets.py`.

`smoke_test.test_fixtures_carry_no_session_or_personal_material` guards the
next capture with patterns, not with these literals.
