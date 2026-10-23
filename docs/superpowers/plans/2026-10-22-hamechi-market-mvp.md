# HameChiMarket MVP Implementation Plan

> **For agentic workers:** This plan is executed inline in the current task. Steps use checkbox syntax for tracking.

**Goal:** Build the first server-side Telegram storefront for Canboso with persistent orders and a Payexa card-to-card checkout path that fails closed until credentials and pricing are configured.

**Architecture:** A Python Telegram bot handles private-chat navigation. A Canboso client reads products and balance, while a Payexa client creates and verifies payments. SQLite stores order state; Fernet encrypts delivery credentials.

**Tech Stack:** Python 3.14, python-telegram-bot 22.8, httpx, SQLite, cryptography/Fernet.

**Spec:** docs/superpowers/specs/2026-10-22-hamechi-market-design.md

## Global Constraints

- Never log API keys, raw provider responses, payment URLs, or delivered credentials.
- Never mark an order paid from a callback or deep-link parameter without server-side Payexa verification.
- Never call Canboso purchase before verified payment.
- Every Canboso purchase uses the local order ID as Idempotency-Key.
- Checkout requires explicit USD_TO_IRR_RATE and SELLER_MARGIN_BPS configuration.
- Only handle buyers in private Telegram chats; deliver credentials only to the matching buyer.

## Review Focus

- Missing or malformed secrets/configuration must leave browsing available and checkout disabled.
- Unknown or unsupported product requirements must be rejected before payment creation.
- Duplicate payment returns or callbacks must not create duplicate supplier purchases.
- Canboso stock/balance failures after payment must produce a manual refund/support state.
- API descriptions and returned credentials must not be parsed as HTML or written to logs in plaintext.

---

### Task 1: Project configuration and persistence

**Files:** Create requirements.txt, .env.example, .gitignore, src/hamechi_market/config.py, src/hamechi_market/storage.py.

- [x] Load secrets and validate the pricing/encryption configuration without printing values.
- [x] Create SQLite order storage with explicit statuses and atomic status updates.
- [x] Encrypt delivery payloads with Fernet before storing them.

### Task 2: Supplier and payment clients

**Files:** Create src/hamechi_market/canboso.py and src/hamechi_market/payexa.py.

- [x] Read the Canboso catalog and USD balance using the documented authentication fields.
- [x] Create Payexa payments in IRR and verify them server-side; keep payment keys in Authorization headers.
- [x] Purchase from Canboso only with a verified payment and the order ID as the idempotency key.

### Task 3: Telegram storefront and order state machine

**Files:** Create src/hamechi_market/bot.py, src/hamechi_market/__main__.py, README.md, AGENTS.md.

- [x] Add Persian private-chat catalog, product detail, and input flows for declared purchase requirements.
- [x] Create payments only when the quote, stock, supplier balance, and credentials are ready.
- [x] Verify returning payments, handle duplicate updates, fulfill privately, and expose order status/retrieval.
- [x] Document setup and operational limits.

### Task 4: Project memory and syntax verification

**Files:** Create graphify-out/graph.json and graphify-out/obsidian/ through Graphify.

- [x] Generate both graph and Obsidian memory, then add the project pointer in AGENTS.md.
- [x] Run Python syntax compilation and inspect source changes.
