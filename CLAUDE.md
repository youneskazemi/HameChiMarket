# HameChiMarket project guide

Read `AGENTS.md` as well as this file before making project changes. `README.md` has setup and operating instructions; `docs/superpowers/specs/2026-10-22-hamechi-market-design.md` records the MVP design.

## Product

HameChiMarket is a Persian-first Telegram shop for Iranian buyers. The bot is `@roboticvn686868`; the customer-facing name is «همه‌چی مارکت» and support is `@EZPY9798`.

Keep the experience inside Telegram's native chat and keyboard UI. Write customer-facing copy in natural Persian. Do not describe the shop as a reseller. Avoid claims such as «بهترین قیمت» unless the owner confirms they are accurate.

Product and UX decisions:

- Show every available category on the first products screen; categories do not paginate.
- Show the provider's Persian product title and description when available. Preserve provider text verbatim; do not invent translations. The currently documented Canboso response may be English.
- Customers pay in IRR/toman. The supplier account balance is a separate USD balance; there is no customer dollar wallet.
- Warranty-request flows are out of scope.
- Keep a direct support action for `@EZPY9798` in the main menu.
- An admin dashboard is not part of the MVP. Do not add Django or another admin stack unless the owner asks for it.

## Code map

- `src/hamechi_market/bot.py`: Telegram handlers, catalog navigation, checkout, payment verification, order tracking, support.
- `src/hamechi_market/canboso.py`: Canboso Buyer API client.
- `src/hamechi_market/payexa.py`: Payexa payment creation and server-side verification.
- `src/hamechi_market/storage.py`: SQLite order state and encrypted delivery data.
- `src/hamechi_market/config.py`: environment loading and configuration validation.
- `src/hamechi_market/__main__.py`: application startup and polling.
- `tests/`: unit and flow tests for storage, API clients, and bot behavior.

Use `graphify query <symbol>` with `graphify-out/graph.json` before broad code searches. `graphify-out/obsidian/` contains generated navigation notes, not the source of truth. Refresh generated graph files only when requested or when the code changes make them materially stale.

## Order and payment invariants

- Payment methods supported: Card-to-card (`PAYMENT_METHOD=card`, default) and Payexa (`PAYMENT_METHOD=payexa`).
- Card-to-card generates a small random offset (110 to 990 Tomans) for order uniqueness. Admin reviews the customer's uploaded receipt and approves or rejects it directly in Telegram.
- Never trust a browser redirect or client-supplied payment result. In Payexa mode, verify the payment with Payexa on the server and match its amount, payment reference, and local order before any supplier purchase.
- Use the local order ID as the Canboso idempotency key. Preserve idempotent order and supplier state transitions.
- Sandbox payment verification must never spend the real Canboso supplier balance or trigger a live purchase.
- Live checkout requires valid configuration, a funded supplier USD balance, a chosen IRR-per-USD rate and margin, and an administrator ID for operational alerts. Fail closed when any required value is missing.
- Canboso has no documented stock reservation or automatic refund path. If payment succeeds but supplier fulfillment fails, preserve the order for manual support/refund handling; do not claim an automatic refund.
- Collect only fields required by the selected product. Keep delivery values encrypted at rest and scoped to the purchasing Telegram user.
- Do not make live payments, purchases, or other money-moving API calls without the owner's explicit request.

## Credentials and external APIs

- Never read, print, commit, or copy `.env` values into chat or documentation. Use `.env.example` to understand configuration names.
- API keys belong only in server-side environment variables. A Canboso key was previously shared in chat and must be rotated before production use; do not reuse or reproduce it.
- Before changing request or response contracts, consult the current Canboso Buyer API Swagger and the current Payexa merchant docs. Follow `AGENTS.md` for API-documentation and Context7 conventions.
- The app currently uses Telegram long polling; this MVP has no public payment callback server.

## Development

Python 3.11 or newer is required; the README's Windows setup example uses Python 3.14.

```powershell
python -m pip install -r requirements.txt
python -m pip install -e .
Copy-Item .env.example .env
python -m hamechi_market
```

The local test suite can be run with:

```powershell
python -m unittest discover -s tests -v
```

Never report tests as passing unless they were run in the current work session. Keep `.env`, the SQLite database under `data/`, and generated secrets out of version control.

## Current status and unfinished work

- Payexa is configured as a sandbox option. The production payment provider and merchant activation are not finalized; do not present live payments as available.
- The last documented Canboso supplier balance was zero USD. Recheck and fund it before any production sale.
- The bot's Persian profile copy is still being prepared. The latest About text discussed was «محصولات دیجیتال را راحت و با قیمت مناسب از همه‌چی مارکت تهیه کن.» Treat it as a draft until the owner confirms it and BotFather reflects it. `docs/telegram-bot-profile-fa.md` contains earlier copy and may be stale.
- A Persian Privacy Policy has not been written or published. If asked to add one, inspect the actual stored data and order flow first; do not claim legal compliance or add a dead URL.
- `assets/telegram-description-photo-640x360.png` and `assets/telegram-description-photo-640x360.jpg` were measured at exactly 640×360. BotFather previously reported invalid dimensions during an upload attempt. Do not assume the upload succeeded; if it recurs, verify which local file was selected before changing the artwork.
- This workspace currently has no `.git` metadata, so Git status/diff commands may not work here.
