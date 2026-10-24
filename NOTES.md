# Build Notes — MCP server over guarded warehouse queries

Working notes: what broke, what was tried, why X over Y.

## 2026-10-25 — core server

### SDK version
- The current `mcp` SDK is 2.x. `FastMCP` is gone (`mcp.server.fastmcp` raises a pointer to the
  migration guide); the class is `mcp.server.mcpserver.MCPServer`, and `mcp.Client` connects to a
  server object in-process for tests or to a `StdioServerParameters` subprocess. Built on 2.3.0
  rather than pinning 1.x: a new project should not start on the old major.

### Two SDK defaults that are wrong for a query tool (found in a spike before writing the server)
1. **Unknown arguments are dropped silently.** A call with an argument the tool does not declare
   is accepted and the argument is ignored. For `run_query`, a model that puts `region` next to
   `name` instead of inside `params` would get the unfiltered, plausible, wrong number. #15's
   catalog rejects unknown parameters for exactly this reason (its docstring: "silently dropping
   a misspelled filter is how someone ends up reading an unfiltered number"), so the protocol layer
   has to as well. `GuardedServer.call_tool` checks argument names against the tool's input schema
   before the SDK sees them.
2. **Validation errors quote the input.** Pydantic's message carries `input_value=...`, so
   `limit: "DROP TABLE orders"` came back verbatim in the error text a model reads. Errors now carry
   field names only.

### The upstream guards echo input too
#15's `UnsafeQuery` messages say `got 'X'` for a bad parameter, and a DuckDB binder error quotes the
SQL line. Right for a human at a terminal, wrong for a tool result read by a model that may treat
error text as instructions. The vendored code is not edited (it is what the differential suite
tests); `service.Gateway` classifies the exception and replaces the message with a template filled
from server-side facts only (query names, parameter names). Trade-off: a model writing ad-hoc SQL
gets `QUERY_FAILED (BinderException)` and a pointer to `warehouse://schema`, not the column name it
got wrong. That costs a retry; echoing costs a channel for injected text.

### Error codes
The brief listed four codes. A fifth, `QUERY_FAILED`, separates "the engine rejected a read"
(wrong column in ad-hoc SQL) from `INTERNAL` (a bug). Collapsing them would make every model typo
look like a server fault in the logs.

### Smaller things that broke
- `QueryError` was a frozen dataclass subclassing `Exception`: raising it through a
  `contextmanager` fails, because `contextlib` assigns `__traceback__` and the frozen class refuses.
  Now a plain exception class.
- The SDK wraps a tool's `ToolError` in another one (`Error executing tool run_query: ...`), so the
  original `QueryError` is two `__cause__` links down. `_root_cause` walks the chain.
- DuckDB `DATE` columns reach pandas as `datetime64`, so dates rendered as `1992-01-20T00:00:00`.
  A datetime column where every value is midnight is now rendered as dates; a real timestamp with a
  non-midnight value keeps its time. Positional, because ad-hoc SQL can repeat a column name.
- `bool` is an `int` in Python, so `limit: true` would have meant one row. Rejected.

### DuckDB pinned to 1.5.5
`uv add duckdb` resolved 2.0.0. Pinned to 1.5.5, #15's locked version, for two reasons: the
differential suite should test the guards on the engine they shipped on (`extract_statements` and
the statement-type names are what layer 3 relies on), and the `tpch` extension is cached locally
for 1.5.5, so the real warehouse builds offline.

### Prompt arguments are allowlisted
`revenue_report(region, year)` interpolates its arguments into prompt text, which is the same
problem as a dynamic SQL identifier. Region must be one of the five TPC-H regions and year
1992-1998.

## 2026-10-25 — parallel build, review findings

The attack suite, the latency harness (+ document-search adapter) and the tool-selection eval
were built in parallel against the core server, each in its own files, and reviewed before commit.

- **A third echo path: resource URIs.** For a URI that matches no resource or template, the SDK
  raises `Unknown resource: <uri>` and its handler also puts the URI in the error's `data`. Found
  while testing `docs://../../etc/passwd`. `GuardedServer.read_resource` now raises an `MCPError`
  directly (which the handler passes through untouched) with a coded JSON message listing the
  server's own resources and templates. Fixed before the attack suite was frozen; the suite
  carries the same URIs as payloads.
- **Tool descriptions contradicted the eval prompt.** `INSTRUCTIONS` said "call list_queries,
  then describe_query, then run_query" while the eval's system prompt said "a single tool call".
  The eval now allows up to 3 turns with lookups, and the descriptions now say three things a model
  could not know otherwise: parameters nest under `params` (a 7B model flattening them would hit
  the unknown-argument rejection), optional ones may be omitted, and `end_date` is exclusive.
  Changed before the 20 tasks were frozen and before the first model call.
- `docs://{doc_id}` never sees an id containing `/`, because the template does not match it; the
  adapter also validates ids against the searcher and never uses one as a path.

## 2026-10-25 — first run, fixes, results

- **First attack run failed** (94 payloads, mini fixture): 5 payloads accepted that should have
  been rejected, on both transports. Three were a real hole in #15's guards (`read_text`,
  `read_csv`, `glob` through `run_sql` read local files); two were `limit` values the SDK coerced
  before the server's check. Fixed upstream in #15 (`9363ae5`, local commit, push pending) and here.
  The first run is reproduced, not just described: `results/attacks/first_run_mini.md` was produced
  by the current runner on a clean clone with the pre-fix server files (`fdba090`) and the original
  94 payloads.
- **The tool-selection eval found a crash** the suite had no payload for: `params: {"limit": 5}`
  splatted into `QueryService.run(name, *, limit, **params)` gave `TypeError: multiple values for
  keyword argument 'limit'` and an `INTERNAL` result. A `use_cache` key would have silently reached
  the cache switch. `params` may no longer hold `name`, `limit` or `use_cache`; three payloads cover it.
- **Echo rule amended** (EVAL_PLAN Amendment 1): the literal rule flags the server's own published
  names; filtered 0, unfiltered 104, and all 104 are names like `end_date` and `run_sql`.
- **Rerun: PASS** on mini and TPC-H sf 0.1, 102 payloads, both transports, 0 bypasses, 0 echoes,
  152/152 coded, warehouse hash unchanged. About 25 s per run.
- **Tool selection: 11/20.** The live run used the server at `10a6065` (before the `params` and strict
  `limit` fixes); the offline replay on the fixed server gives the same 11/20, only `late-3`'s error
  changes from `INTERNAL` to `BAD_PARAM`. 7/9 misses are inclusive end dates.
- **Latency:** cached overhead ~3 ms (5 rows) to ~51 ms (1,000 rows); stdio startup 7-10 s. Busy
  machine (five other project builds running).

### Tried and left
- Prompt arguments that are missing or the wrong type (`year: "abc"`) are rejected by the SDK
  before the prompt function runs, and the SDK logs that at ERROR with a traceback on stderr. The
  client gets a generic "Error rendering prompt" with no echo, so it passes the suite; silencing the
  log would mean making both arguments optional and validating by hand, which also hides that they
  are required from clients. Left as is.
- A CLI subprocess test of the whole suite took 265 s on the busy machine and duplicated the CI step
  that runs the same command; removed.
