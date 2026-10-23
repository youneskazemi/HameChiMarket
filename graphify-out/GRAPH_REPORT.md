# Graph Report - HameChiMarket  (2026-10-22)

## Corpus Check
- 17 files · ~9,887 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 188 nodes · 496 edges · 12 communities (8 shown, 4 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 21 edges (avg confidence: 0.56)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]

## God Nodes (most connected - your core abstractions)
1. `OrderStore` - 27 edges
2. `BotFlowTests` - 27 edges
3. `_process_verified_order()` - 21 edges
4. `_show_catalog()` - 19 edges
5. `callback()` - 18 edges
6. `Settings` - 17 edges
7. `_show_catalog_entry()` - 16 edges
8. `_begin_checkout()` - 16 edges
9. `text_router()` - 14 edges
10. `CanbosoClient` - 14 edges

## Surprising Connections (you probably didn't know these)
- `ApplicationWiringTests` --uses--> `Settings`  [INFERRED]
  tests/test_application.py → src/hamechi_market/config.py
- `BotFlowTests` --uses--> `OrderStore`  [INFERRED]
  tests/test_bot_flow.py → src/hamechi_market/storage.py
- `FakeCanboso` --uses--> `OrderStore`  [INFERRED]
  tests/test_bot_flow.py → src/hamechi_market/storage.py
- `FakeMessage` --uses--> `OrderStore`  [INFERRED]
  tests/test_bot_flow.py → src/hamechi_market/storage.py
- `FakePayexa` --uses--> `OrderStore`  [INFERRED]
  tests/test_bot_flow.py → src/hamechi_market/storage.py

## Import Cycles
- None detected.

## Communities (12 total, 4 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.24
Nodes (4): Any, Connection, OrderStore, utc_now()

### Community 2 - "Community 2"
Cohesion: 0.07
Nodes (6): _quote_irr(), Settings, BotFlowTests, FakeCanboso, FakeMessage, FakePayexa

### Community 3 - "Community 3"
Cohesion: 0.13
Nodes (7): CanbosoClient, CanbosoError, PayexaClient, PayexaError, RuntimeError, ApiClientTests, FakeAsyncClient

### Community 4 - "Community 4"
Cohesion: 0.16
Nodes (10): Application, Decimal, build_application(), ConfigurationError, load_settings(), main(), Path, ApplicationWiringTests (+2 more)

### Community 5 - "Community 5"
Cohesion: 0.18
Nodes (41): DEFAULT_TYPE, _ask_months(), _available(), _begin_checkout(), _brand_category(), _brand_label(), callback(), cancel_command() (+33 more)

### Community 7 - "Community 7"
Cohesion: 0.25
Nodes (7): Global Constraints, HameChiMarket MVP Implementation Plan, Review Focus, Task 1: Project configuration and persistence, Task 2: Supplier and payment clients, Task 3: Telegram storefront and order state machine, Task 4: Project memory and syntax verification

### Community 8 - "Community 8"
Cohesion: 0.25
Nodes (7): Confirmed constraints and decisions, Goals, HameChiMarket MVP Design, Intent, Non-goals for the first slice, Open operational inputs, Order flow

### Community 9 - "Community 9"
Cohesion: 0.50
Nodes (3): HameChiMarket, راه‌اندازی محلی, رفتار و محدودیت‌ها

## Knowledge Gaps
- **15 isolated node(s):** `hamechi-market`, `HameChiMarket agent notes`, `راه‌اندازی محلی`, `رفتار و محدودیت‌ها`, `Global Constraints` (+10 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `OrderStore` connect `Community 0` to `Community 1`, `Community 2`, `Community 4`, `Community 5`?**
  _High betweenness centrality (0.188) - this node is a cross-community bridge._
- **Why does `BotFlowTests` connect `Community 2` to `Community 0`, `Community 5`?**
  _High betweenness centrality (0.090) - this node is a cross-community bridge._
- **Why does `Settings` connect `Community 2` to `Community 4`, `Community 5`?**
  _High betweenness centrality (0.088) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `OrderStore` (e.g. with `BotFlowTests` and `FakeCanboso`) actually correct?**
  _`OrderStore` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `BotFlowTests` (e.g. with `Settings` and `OrderStore`) actually correct?**
  _`BotFlowTests` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `hamechi-market`, `HameChiMarket Telegram storefront.`, `HameChiMarket agent notes` to the rest of the system?**
  _16 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.06747638326585695 - nodes in this community are weakly interconnected._