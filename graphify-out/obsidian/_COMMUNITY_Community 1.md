---
type: community
members: 5
---

# Community 1

**Members:** 5 nodes

## Members
- [[.create_order()_1]] - code - tests/test_storage.py
- [[.test_order_inputs_are_encrypted_and_scoped_to_owner()]] - code - tests/test_storage.py
- [[.test_payment_and_supplier_transitions_are_idempotent()]] - code - tests/test_storage.py
- [[OrderStoreTests]] - code - tests/test_storage.py
- [[test_storage.py]] - code - tests/test_storage.py

## Live Query (requires Dataview plugin)

```dataview
TABLE source_file, type FROM #community/Community_1
SORT file.name ASC
```

## Connections to other communities
- 2 edges to [[_COMMUNITY_Community 0]]
- 1 edge to [[_COMMUNITY_Community 4]]

## Top bridge nodes
- [[OrderStoreTests]] - degree 6, connects to 2 communities
- [[test_storage.py]] - degree 2, connects to 1 community