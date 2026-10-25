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

---

## Amendment 2 — 2026-10-25, after an independent review, before the `[A2]` payloads were run

A review from a clean context (no access to the reasoning that built the suite) tried about 110
payloads by hand and checked every README number against `results/`. It found no write, no file
read and no row-cap bypass, and the numbers matched. It found these, all now addressed:

- **Resource exhaustion through `run_sql`** (only with `--allow-adhoc`): no statement time limit
  (`SELECT count(*) FROM range(10^11)` ran past 40 s and blocked every other call behind the
  connection lock), and the row cap does not bound bytes (100,000 rows of 2 KB strings returned
  202 MB). Fixed in the gateway: a per-query time limit (default 10 s) that interrupts DuckDB, and
  a 4 MB budget on rendered rows (`truncated_by: "size"`). Process memory is not bounded: the
  vendored service materialises a result in pandas before the budget applies.
- **Rendering bugs:** LIST values went through numpy's `str()` and lost elements; BLOBs rendered as
  `bytearray(...)`. Fixed. DECIMAL and HUGEINT arrive as float64 from the upstream pandas conversion;
  documented, not fixed here.
- **Overclaim:** the README said every rejection carries a code. The rule above covers tool-call
  rejections; prompt calls the SDK rejects before the prompt function runs (missing or mistyped
  arguments, unknown prompt) come back as an uncoded `Internal server error`. Now reported as a
  separate count.
- **Runner blind spots:** client exceptions counted as `reject`; an `ok` result without row data could
  not fail the over-cap check; the echo check compared whole argument strings only; prompt names were
  not checked. Fixed: a client exception is an `error` that fails the run, missing row data fails the
  run, `over_size` is a fifth bypass type, and the echo check also tests SQL fragments of 6+
  characters and prompt names.
- **Provenance, stated plainly:** `attacks/attacks.jsonl` and the runner were first committed after the
  first run and its fixes, so "the original 94 are unchanged" means the first 94 lines of the file;
  it cannot be checked against an earlier commit. The in-session first run was in-process only and
  its output was not saved; `results/attacks/first_run_mini.md` is a reproduction on a clean clone
  with the pre-fix server files and both transports.

**Payloads added, marked `[A2]`:** five exhaustion queries (huge `range`, cross join, unbounded
recursive CTE, a 200M-element list, a 200 MB result that must come back truncated by size), a
metadata read that discloses the server's file path (`expect: ok`, a documented limitation), a lone
surrogate, an empty date window, and a trailing-comment query that fails closed. The suite runs with
a 2 s time limit. The pass rule is unchanged, with `over_size` added as a bypass.

**Amendment 2, first run of the 111-payload suite (2026-10-25, runner `957d195`, server `d83ad81`):
FAIL on both warehouses**, for one payload. 0 bypasses, 0 echoes, 164/164 tool rejections coded,
but `ex-04` (`SELECT list(range(200000000))`) produced a client error: DuckDB does not check for an
interrupt while it builds that single LIST value, so the 2 s limit fired and the call still ran past
the client's 120 s timeout (stdio on TPC-H, in-process on the mini fixture; the other transport came
back as a slow rejection). The time limit bounded most queries but not the response time of this one.
Fix (`41dcb0a`): a query still running at the limit plus 1 s is abandoned: the caller gets the
timeout error, the busy connection is left to finish on its own thread, and a fresh connection serves
the next call; with two abandoned queries still running, new calls are refused until they finish.
The pass rule and payloads are unchanged for the rerun.

**Amendment 2, second rerun (runner `0cc3516`, server `41dcb0a`):** PASS on the mini fixture; FAIL on
TPC-H for one payload, `ex-05`, the benign size control (`repeat('x', 2000)` over 100,000 rows,
about 200 MB, `expect: ok`). Over stdio, with the machine's CPU at 100% from other work, building
200 MB took longer than the suite's 2 s limit, so the server correctly stopped it, and a benign
control was rejected. The payload was meant to test the 4 MB size budget, not the time limit, so it
was reduced to 10,000 rows of 1 KB (about 10 MB, still 2.5 times the budget); its note says so. No
other payload, the pass rule and the server are unchanged for the final run. The 1-in-153 test failure
in the same run was a race in a new test (a fake stuck query that could finish before the busy
check); the fake now blocks on an event.

**Hosted CI (2026-10-25, first push, `3b18bcd`):** lint and all 153 tests passed; the attack-suite step
was cancelled by the runner 7 s after `ex-04` was abandoned. That payload builds one multi-gigabyte
LIST value, and the suite can have three copies running at once (the direct side, the abandoned
in-process query and the abandoned query in the stdio server), which most likely exhausted the
runner's 16 GB. It is the documented limit (the time limit bounds the caller's wait, not the work)
showing up for real. CI now runs the suite with `--exclude ex-04`, and the report lists the
exclusion; the full 111-payload runs behind the README are local (64 GB). The pass rule is unchanged.
