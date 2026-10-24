# Tool-selection tasks

`tool_tasks.jsonl` holds 20 business questions, 5 for each catalog query. Each line is
`{"id", "task", "expect": {"tool", "name", "params"}, "note"}`. The question is written the way
an analyst would ask it: no parameter names, mixed date styles ("Q3 1995", "the whole of
1996", "March 1994"), mixed-case regions and segments. `expect.params` lists the required
parameters plus any optional ones the question pins (a market segment, a `dimension`
identifier, `min_orders`). End dates are exclusive, so "1995" is `1995-01-01` to `1996-01-01`.

## How it is scored

A local model gets the server's tool list, its instructions, the catalog resource and one
question, at temperature 0 with a fixed seed. It may call `list_queries` or `describe_query`
first (3 turns at most; the lookups run through the real MCP client). The first `run_query` call
is scored. It is correct when all of these hold:

- the query `name` equals the reference;
- every `expect.params` key matches after normalising (dates to ISO, enum and identifier values
  upper-cased, ints as ints); a pinned key the model omits is correct only when the reference
  equals the query's default;
- every extra parameter equals that parameter's default;
- the call runs without error on the server.

Failures are put in one category: `no_tool_call`, `wrong_tool`, `wrong_query`, `wrong_params`,
`server_rejected`. The result is reported as k/20 with a Wilson 95% interval; n = 20 is too small
to rank models, so no comparison is claimed.

## Running

```bash
cd "D:\Coding projects\resume_max\44-mcp-server"; uv run mcpquery tool-eval --out eval/tool_eval.json
```

Every model request and response is recorded in `eval/runs/<model>.jsonl`, keyed by a hash of
the request. `--replay` re-scores from those records only, with no network, and fails if a
request was not recorded.

## Frozen before the first run

The tasks and their references were fixed before any model call. A change after the first model
call is made as a dated amendment in `EVAL_PLAN.md`, and the run is repeated from scratch.
