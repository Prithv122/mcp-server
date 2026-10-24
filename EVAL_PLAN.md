# Evaluation plan (pre-registered)

Written and committed **before** the attack suite exists and before any measurement on the real
warehouse. Changes after this commit are added as dated amendments at the bottom; the text above
them is not edited.

## Question

The server exposes `15-query-service`'s guarded `QueryService` over the Model Context Protocol.
Across a fixed attack suite, does the MCP server reach the same verdict as calling `QueryService`
directly, with **0 bypasses**?

## Definitions

- **Payload**: one tool call (`tool`, `arguments`) or one resource read (`uri`) from
  `attacks/attacks.jsonl`. Each has `kind`, and `expect` ∈ {`reject`, `ok`}.
- **Verdict**: `reject` or `ok`.
  - Direct: `QueryService.run` / `.run_sql` / `.describe` on the same payload; any exception is
    `reject`, a returned result is `ok`.
  - MCP: `is_error: true` (or a protocol error) is `reject`, a result is `ok`.
- **Bypass** (any one counts):
  1. a **write**: any table's row count or content checksum differs after the suite, or the
     warehouse file's hash changed;
  2. an **over-cap result**: a result with more rows than the effective limit
     (`min(requested, 100,000)`, default 1,000);
  3. an **MCP-looser divergence**: MCP says `ok` where direct says `reject`;
  4. a payload with `expect: reject` that MCP accepts.
- **MCP-stricter divergence**: MCP says `reject` where direct says `ok`. Not a bypass. Allowed only
  for payloads tagged `stricter_by_design` in the suite, each with a one-line reason (examples
  known before the suite was written: a `limit` passed as the string `"5"`, a boolean `limit`,
  an argument outside the tool's declared ones). An untagged stricter divergence is reported as a
  finding.
- **Echo**: an MCP error text containing any caller-supplied string (every string leaf and key of
  the arguments, or the URI, length ≥ 4, case-sensitive). Counted separately.
- **Code**: every MCP rejection of a tool call carries a JSON body with `code` in
  {`UNSAFE_QUERY`, `UNKNOWN_QUERY`, `BAD_PARAM`, `QUERY_FAILED`, `INTERNAL`}.

## Pass rule (primary, gated)

All of, on the real TPC-H warehouse (scale factor 0.1) **and** on the offline mini fixture, over
both transports (in-process and a stdio subprocess):

- 0 bypasses (definitions 1-4);
- 0 echoes;
- 100% of tool-call rejections carry a valid code;
- agreement = share of payloads with equal verdicts, reported with the list of every divergence.

A failure is published with its fix, not hidden: the README reports the first run, what failed,
the fix, and the rerun.

## Suite

- At least 30 payloads, committed before the first run on the real warehouse.
- Seeded from `15-query-service/tests/test_safety.py` (its stacked-statement and non-read lists),
  then extended to: identifier injection, enum and type abuse, oversize and negative limits,
  unknown queries and tools, unknown and misplaced arguments, path-like and foreign resource URIs,
  and prompt-argument injection.
- Benign controls with `expect: ok` (at least 5), so a server that rejects everything cannot pass.

## Reported, not gated

- **Latency**: p50 / p95 per tool, cold vs cached, in-process vs stdio, on the real warehouse,
  30 repeats per cell after one warm-up. The machine was not quiet (other work running), so the
  numbers are informative only; the README says so.
- **Tool selection** (secondary): a local model (`qwen2.5:7b-instruct` via Ollama, temperature 0)
  is given the tool list and descriptions only, and one of 20 tasks. Correct = right tool, right
  catalog query, and every required parameter equal to the reference after normalising
  (dates to ISO, enum case). Reported as k/20 with a Wilson 95% interval. Tasks are frozen before
  the first model call. n = 20 gives an interval about ±0.2 wide, so no comparison between models
  is claimed from it.

## Null and negative results

A divergence is a better README than a clean pass. If the suite finds a bypass in the vendored
guards themselves, the fix belongs upstream in `15-query-service`, and this repo re-vendors it.

---

## Amendment 1 — 2026-10-25, after the first run on the mini fixture, before any run on TPC-H

**First run** (94 payloads, mini fixture, in-process): **5 bypasses**, 0 writes, 0 over-cap,
0 looser divergences, 63/63 rejections coded. All 5 were kind 4 (`expect: reject`, MCP accepted):

- `fs-01`..`fs-03`: `glob('*')`, `read_csv(...)` and `read_text(...)` through `run_sql` returned
  rows. The bare service accepted them too, so this was a hole in the vendored guards: a table
  function is a single `SELECT`, and a read-only connection blocks writes, not file reads.
  Fixed upstream (`query-service` `9363ae5`: the connection opens with external access disabled
  and its configuration locked) and re-vendored.
- `lm-04`, `lm-05`: `limit: "5"` and `limit: true`. This plan listed both as expected
  stricter-by-design rejections, but the SDK's lax validation coerced them to 5 and 1 before the
  server's own check ran. Not a security bypass (both are within the cap), but the declared
  behaviour did not happen. Fixed: `limit` is validated in strict mode.

The tool-selection eval (run separately) found a sixth problem: a model put `limit` inside
`params`, which collided with `QueryService.run(..., limit=...)` and crashed as `INTERNAL`. A key
named `use_cache` would have reached the service's cache switch the same way. Fixed: `params`
may not contain `name`, `limit` or `use_cache`.

**Changes to the plan:**

1. **Echo rule.** Read literally, the rule flags the server's own published words: a rejection
   that says `limit must be an integer` "echoes" a payload whose key is `limit`. Strings that
   appear in the server's published vocabulary (tool names, argument and parameter names, query
   names, allowed enum and identifier values) are excluded from the echo check. The unfiltered
   count is reported next to the filtered one so the exclusion is visible.
2. **Payloads added after the first run** are marked `[A1]` in their note: `fs-04`..`fs-08`
   (more file and network reads, including a quoted file name in `FROM`), `ps-01`..`ps-03`
   (params smuggling the service's own keyword arguments). The original 94 are unchanged. The
   pass rule is unchanged.
3. **What counts as the result.** The README reports the first run above, the fixes, and the
   rerun of the full 102-payload suite on the mini fixture and on TPC-H sf 0.1, over both
   transports.
