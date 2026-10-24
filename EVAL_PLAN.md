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
