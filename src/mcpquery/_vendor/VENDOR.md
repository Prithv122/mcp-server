# Vendored code

| Path | Source | Commit | Notes |
|---|---|---|---|
| `queryservice/*.py`, `queryservice/queries/*.sql` | [Prithv122/query-service](https://github.com/Prithv122/query-service) `src/queryservice/` | `9363ae5` | Copied byte for byte. The upstream `cli.py` is left out: this server has its own CLI. |

The copy is not edited. A change to a guard belongs upstream first, then a new copy with a new commit id here,
so the differential attack suite always tests the guards as shipped.

## History

| Commit | Why |
|---|---|
| `d112ebb` | first copy |
| `9363ae5` | upstream fix for a hole this repo's attack suite found: a single `SELECT * FROM read_text(...)` could read local files through `run_sql`. The connection now opens with external access disabled. |
