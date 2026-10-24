# Vendored code

| Path | Source | Commit | Notes |
|---|---|---|---|
| `queryservice/*.py`, `queryservice/queries/*.sql` | [Prithv122/query-service](https://github.com/Prithv122/query-service) `src/queryservice/` | `d112ebb` | Copied byte for byte. The upstream `cli.py` is left out: this server has its own CLI. |

The copy is not edited. A change to a guard belongs upstream first, then a new copy with a new commit id here,
so the differential attack suite always tests the guards as shipped.
