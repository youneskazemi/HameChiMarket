# HameChiMarket MVP Design

## Intent

Build a Persian-first Telegram storefront that lets Iranian buyers browse Canboso products and, once payments and pricing are configured, pay in rials and receive the supplier fulfillment in a private chat.

## Goals

- Browse available products and their supplier-provided descriptions.
- Keep product, payment, and purchase credentials on the server.
- Convert USD supplier prices to IRR using an explicitly configured rate and markup.
- Start a card-to-card payment through Payexa and verify it server-side before purchasing from Canboso.
- Persist order transitions and make repeated callbacks safe through Payexa verification and Canboso idempotency keys.
- Store any delivery credentials encrypted at rest and only return them to the buyer in a private chat.
- Mark post-payment supplier failures for manual refund/support handling; neither API documents an automatic refund path.

## Non-goals for the first slice

- A Bale storefront, customer balance, admin dashboard, dynamic exchange-rate feed, or multiple payment providers.
- Automatic translation of product descriptions.
- Automatic refunds or a guarantee that supplier stock stays reserved between payment and purchase.

## Confirmed constraints and decisions

- The workspace has no application code yet. Python 3.14 and Node 22 are available; this implementation uses Python, SQLite, and python-telegram-bot.
- The Canboso Buyer API key is server-only. The key already shared in chat should be rotated and entered locally through environment variables.
- The current Canboso key returns English product text and lang=en. The documented products and balance operations only expose a key parameter; adding lang=fa or Accept-Language: fa did not change the response. The orders endpoint rejects lang as an unsupported filter. The Telegram interface will be Persian, while product descriptions remain verbatim until Canboso provides a Persian-configured key or the owner supplies translations.
- Canboso purchase spends the buyer wallet, and the live balance returned zero USD. Purchase calls are disabled until the wallet is funded and configuration is complete.
- Payexa is the provisional first payment provider because its documented API supports automatic card-to-card payments, a Telegram payment URL, and server-side verification. The account and API key are not yet configured.
- Payexa's `first_verification` response is required before a supplier purchase. In sandbox mode, a verified test payment never triggers a purchase against the real Canboso wallet.
- The sales rate and margin are not known. Checkout fails closed unless USD_TO_IRR_RATE and SELLER_MARGIN_BPS are set.
- Checkout also requires at least one administrator Telegram ID so post-payment supplier failures can reach an operator.
- The Canboso API has no reservation or refund endpoint. If payment succeeds but supplier purchase fails, the local order becomes refund_required and needs manual action.
- Telegram polling is used initially. The buyer returns to the bot through the Payexa deep link, which triggers verification. There is no public callback server in this slice.

## Order flow

1. Load products and show items with positive available quantity.
2. Show the supplier price in USD and the source description; gather only the fields declared by purchaseRequirements.
3. Before collecting money, re-check supplier balance and stock, then calculate a fixed IRR quote from the configured rate and margin.
4. Create a local order and a Payexa payment; give the buyer the provider's Telegram payment URL.
5. Verify the returned payment server-side. Do not trust the redirect payload or payment notification by itself.
6. Submit one Canboso purchase with the local order ID as the idempotency key.
7. Deliver immediate credentials privately, or show the supplier order code while manual fulfillment is pending. Mark supplier failures for support/refund.

## Open operational inputs

- Telegram bot token and username.
- A rotated Canboso key and funded USD wallet.
- Payexa merchant key and approved merchant account.
- USD-to-IRR rate and margin chosen by the operator.
- Confirmation from Canboso that reseller use is allowed and whether it can issue a Persian-configured key.
