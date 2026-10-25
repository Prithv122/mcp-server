# MCP server over guarded warehouse queries — G6

**Tier:** 3 · **Category:** AI engineering · **Wave:** 4

The project's own engineering guide. Keep it short and current.

## What this is

A Model Context Protocol server that exposes the allowlisted, read-only TPC-H query service from
`query-service` (#15) to AI assistants over stdio, plus a differential attack suite that sends the
same payloads to the server and to the bare service and proves the guards survive the protocol
boundary.

## Stack

Python 3.13 · `mcp` 2.x SDK (`MCPServer`, stdio transport) · DuckDB 1.5.5 (pinned to match #15) ·
pandas · uv · ruff · pytest (+ pytest-asyncio). Ollama only for the local tool-selection eval.

## Acceptance criteria

- [x] Tools `list_queries`, `describe_query`, `run_query` (+ `run_sql` only with `--allow-adhoc`);
      resources `warehouse://schema`, `catalog://queries`; prompt `revenue_report`
- [x] Every rejection is a JSON body with a stable `code` and never repeats caller input
- [x] Attack suite (≥ 30 payloads, pre-registered in `EVAL_PLAN.md`): 0 bypasses, 0 echoes, on the
      mini fixture and on TPC-H sf 0.1, over in-process and stdio transports
- [x] Latency per tool, cold vs cached, protocol overhead against the bare service (informative)
- [x] Tool-selection eval with a local model, k/20 with a Wilson interval (informative)
- [x] Runs from a clean clone using only the documented steps
- [ ] Tests, lint and CI green; README results come from real runs

## Conventions

- `uv` for dependencies; `ruff check` + `ruff format`; `pytest`.
- `src/mcpquery/_vendor/` is a byte-for-byte copy pinned by commit (see its `VENDOR.md`). Never
  edit it; a guard fix goes upstream first, then re-vendor.
- Never loosen a guard or tag a divergence "stricter by design" to make the attack suite pass.
- The server writes nothing to stdout except protocol messages; logs go to stderr.
- No secrets; the project reads no environment variables.

## Project-specific notes

- The real warehouse is generated, not downloaded: `uv run mcpquery build-db --sf 0.1` (needs
  DuckDB's `tpch` extension once, which DuckDB fetches on first use). Tests and CI use the offline
  mini fixture from `mcpquery.minitpch`.
- Local pytest on Windows needs `--basetemp` pointing at a writable scratch directory.
- `EVAL_PLAN.md` is frozen; changes go in a dated amendment at its bottom.
