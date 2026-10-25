"""Differential attack suite: the same payload to the bare service and to the MCP server.

``EVAL_PLAN.md`` fixes the pass rule before any measurement; this module is that rule as
code. Each payload in ``attacks/attacks.jsonl`` is sent twice, once straight to the vendored
``QueryService`` and once through an MCP client (in-process and over a stdio subprocess), and
the two verdicts are compared. What counts as a failure:

* **bypass**: a write (row counts, content checksums or file hash changed), an over-cap
  result, an over-size result (rows that serialise past ``MAX_RESULT_BYTES``), an MCP
  verdict looser than the direct one, or an ``expect: reject`` payload the server accepted;
* **echo**: an MCP error text that repeats something the caller sent;
* **code**: a tool-call rejection whose body is not JSON with a known ``code``;
* **error**: the client itself failed (a timeout, a broken transport) rather than getting an
  answer, which must not be read as a rejection;
* **unverifiable**: a row-returning tool call that came back "ok" without the structured
  fields the cap checks need, so absence is not taken as compliance.

A rejection by the MCP layer of something the bare service accepts is *stricter*. It is not
a bypass, but it must carry a ``stricter_by_design`` reason in the suite, otherwise it is
reported as a finding.

Echo, as implemented: the plan says "every string leaf and key of the arguments". Taken
literally that flags an error that names the tool's own parameters (``limit``, ``params``),
which the caller did not invent and the server publishes in its tool list. So strings that
appear in the server's published vocabulary (tool, parameter, query and enum names) are
excluded, and the unfiltered count is reported next to the gated one as ``echo_raw``. For
``run_sql`` payloads the gated check also looks for SQL fragments (identifiers and the contents
of quoted literals, six characters or longer), because an engine message that quotes part of
the statement does not repeat the whole string.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import tempfile
import threading
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mcp.shared.exceptions import MCPError

from ._vendor.queryservice import QueryService
from ._vendor.queryservice import safety as _safety
from ._vendor.queryservice import warehouse as _warehouse
from .clients import Transport, open_client
from .render import MAX_RESULT_BYTES

KINDS = (
    "stacked",
    "non_read",
    "identifier_injection",
    "enum_abuse",
    "type_abuse",
    "limit_abuse",
    "exhaustion",
    "disclosure",
    "unknown_query",
    "unknown_tool",
    "unknown_argument",
    "resource_uri",
    "prompt_injection",
    "benign",
)
CODES = frozenset({"UNSAFE_QUERY", "UNKNOWN_QUERY", "BAD_PARAM", "QUERY_FAILED", "INTERNAL"})
MIN_ECHO_LEN = 4
MIN_FRAGMENT_LEN = 6
CALL_TIMEOUT = 120.0
#: Server-side query limit for suite runs, and the matching limit on the bare service. Short,
#: so each exhaustion payload costs about this long per side.
SUITE_TIMEOUT_S = 2.0
#: Tools whose "ok" result must carry rows, a row count and a limit for the cap checks.
ROW_TOOLS = frozenset({"run_query", "run_sql"})
#: SQL words of six or more letters. The server's fixed messages name statement kinds
#: ("Only a single read statement (SELECT, WITH, FROM or TABLE)"), so a keyword the caller
#: also typed is the language, not an echo of caller content.
SQL_KEYWORDS = frozenset(
    (
        "select", "insert", "update", "delete", "create", "attach", "detach", "install",
        "export", "import", "pragma", "execute", "prepare", "recursive", "values", "having",
        "offset", "checkpoint", "vacuum", "analyze", "explain", "describe", "summarize",
        "distinct", "between", "extract", "interval",
    )
)  # fmt: skip
DEFAULT_SUITE = Path("attacks") / "attacks.jsonl"

# -- payloads --------------------------------------------------------------------------


@dataclass(frozen=True)
class Payload:
    """One tool call, resource read or prompt fetch. Exactly one of the three targets is set."""

    id: str
    kind: str
    expect: str
    tool: str | None = None
    uri: str | None = None
    prompt: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    stricter_by_design: str | None = None
    note: str = ""

    @property
    def target(self) -> str:
        return "tool" if self.tool is not None else "uri" if self.uri is not None else "prompt"

    @property
    def name(self) -> str:
        return self.tool or self.uri or self.prompt or ""


def _payload_from(raw: dict[str, Any], where: str) -> Payload:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected a JSON object")
    allowed = {
        "id",
        "kind",
        "tool",
        "uri",
        "prompt",
        "args",
        "expect",
        "stricter_by_design",
        "note",
    }
    if unknown := set(raw) - allowed:
        raise ValueError(f"{where}: unknown fields {sorted(unknown)}")
    for required in ("id", "kind", "expect"):
        if not isinstance(raw.get(required), str) or not raw[required]:
            raise ValueError(f"{where}: missing or empty {required!r}")
    if raw["kind"] not in KINDS:
        raise ValueError(f"{where}: unknown kind {raw['kind']!r}")
    if raw["expect"] not in ("reject", "ok"):
        raise ValueError(f"{where}: expect must be 'reject' or 'ok'")
    targets = [key for key in ("tool", "uri", "prompt") if key in raw]
    if len(targets) != 1 or not isinstance(raw[targets[0]], str):
        raise ValueError(f"{where}: exactly one string target of tool, uri or prompt")
    args = raw.get("args", {})
    if not isinstance(args, dict):
        raise ValueError(f"{where}: args must be an object")
    tag = raw.get("stricter_by_design")
    if tag is not None and (not isinstance(tag, str) or not tag.strip()):
        raise ValueError(f"{where}: stricter_by_design needs a one-line reason")
    return Payload(
        id=raw["id"],
        kind=raw["kind"],
        expect=raw["expect"],
        tool=raw.get("tool"),
        uri=raw.get("uri"),
        prompt=raw.get("prompt"),
        args=args,
        stricter_by_design=tag,
        note=raw.get("note", ""),
    )


def load_suite(path: Path | str) -> list[Payload]:
    """Read and validate a JSON-lines suite. Ids are unique; blank lines are skipped."""
    payloads: list[Payload] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            where = f"{path}:{number}"
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{where}: not valid JSON ({exc.msg})") from exc
            payload = _payload_from(raw, where)
            if payload.id in seen:
                raise ValueError(f"{where}: duplicate id {payload.id!r}")
            seen.add(payload.id)
            payloads.append(payload)
    if not payloads:
        raise ValueError(f"{path}: the suite is empty")
    return payloads


def default_suite_path() -> Path:
    """``attacks/attacks.jsonl`` in the working directory, else next to the package's repo."""
    if DEFAULT_SUITE.exists():
        return DEFAULT_SUITE
    return Path(__file__).resolve().parents[2] / DEFAULT_SUITE


# -- verdicts --------------------------------------------------------------------------


@dataclass
class Outcome:
    """What one side did with one payload."""

    verdict: str  # "ok" | "reject" | "error" (the client failed; neither of the other two)
    text: str = ""  # error text for a rejection
    code: str | None = None
    row_count: int | None = None
    reported_limit: int | None = None
    raised: bool = False  # a protocol exception rather than a tool result
    rows_bytes: int | None = None  # size of the returned rows, in the renderer's measure
    unverifiable: bool = False  # "ok" without the fields the cap checks need


def effective_limit(payload: Payload) -> int:
    """What the row cap should be for this payload: ``min(requested, 100000)``, default 1000."""
    requested = payload.args.get("limit")
    if requested is None:
        return _safety.DEFAULT_ROW_LIMIT
    try:
        return min(int(requested), _safety.MAX_ROW_LIMIT)
    except (TypeError, ValueError):
        return _safety.DEFAULT_ROW_LIMIT


def is_over_cap(outcome: Outcome, payload: Payload) -> bool:
    if outcome.verdict != "ok":
        return False
    if outcome.row_count is not None and outcome.row_count > effective_limit(payload):
        return True
    return outcome.reported_limit is not None and outcome.reported_limit > _safety.MAX_ROW_LIMIT


def is_over_size(outcome: Outcome) -> bool:
    """Rows larger than the byte budget the server promises to enforce."""
    return (
        outcome.verdict == "ok"
        and outcome.rows_bytes is not None
        and outcome.rows_bytes > MAX_RESULT_BYTES
    )


def rows_bytes(rows: list[Any]) -> int:
    """Size of ``rows`` measured as the renderer does: compact JSON per row, plus one byte."""
    return sum(len(json.dumps(row, separators=(",", ":"))) + 1 for row in rows)


@contextmanager
def interrupt_after(connection: Any, seconds: float) -> Iterator[None]:
    """Interrupt ``connection`` if the block outlives ``seconds`` (same idea as the gateway)."""
    done = threading.Event()

    def fire() -> None:
        if not done.is_set():
            connection.interrupt()

    timer = threading.Timer(seconds, fire)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        done.set()
        timer.cancel()


def direct_verdict(
    service: QueryService, payload: Payload, timeout_s: float = SUITE_TIMEOUT_S
) -> Outcome | None:
    """The verdict of the bare service, or ``None`` where it has no equivalent.

    Resources, prompts and tools the service has no method for are MCP-only. Arguments the
    tool does not declare are ignored here, exactly as the SDK would ignore them.
    """
    if payload.target != "tool":
        return None
    args = payload.args
    try:
        with interrupt_after(service.connection, timeout_s):
            if payload.tool == "list_queries":
                service.catalog()
                return Outcome("ok")
            if payload.tool == "describe_query":
                service.describe(args.get("name"))  # type: ignore[arg-type]
                return Outcome("ok")
            if payload.tool == "run_query":
                params = args.get("params")
                result = service.run(
                    args.get("name"),  # type: ignore[arg-type]
                    limit=args.get("limit"),
                    **({} if params is None else params),
                )
            elif payload.tool == "run_sql":
                result = service.run_sql(args.get("sql"), limit=args.get("limit"))  # type: ignore[arg-type]
            else:
                return None
    except Exception as exc:
        # An interrupted query lands here too: stopped by the time limit counts as a rejection.
        return Outcome("reject", text=f"{type(exc).__name__}: {exc}", raised=True)
    return Outcome("ok", row_count=len(result.frame), reported_limit=result.limit)


def _text_of(contents: Iterable[Any]) -> str:
    return "\n".join(getattr(item, "text", "") or "" for item in contents)


def _code_of(text: str) -> str | None:
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


async def mcp_verdict(client: Any, payload: Payload) -> Outcome:
    """The verdict through an MCP client.

    An error result or an MCP protocol error is a rejection. Any other exception (a timeout,
    a dead transport, an ``anyio`` failure) is the client failing, not the server refusing,
    and becomes ``error`` so it cannot pass as a rejection.
    """
    try:
        if payload.target == "tool":
            result = await asyncio.wait_for(
                client.call_tool(payload.tool, payload.args), CALL_TIMEOUT
            )
            if result.is_error:
                text = _text_of(result.content)
                return Outcome("reject", text=text, code=_code_of(text))
            return _ok_outcome(payload, result.structured_content)
        if payload.target == "uri":
            await asyncio.wait_for(client.read_resource(payload.uri), CALL_TIMEOUT)
        else:
            await asyncio.wait_for(client.get_prompt(payload.prompt, payload.args), CALL_TIMEOUT)
        return Outcome("ok")
    except MCPError as exc:
        text = str(exc)
        # A resource or prompt rejection has no result body, so its code (if any) rides in
        # the protocol error message. Tool calls must carry theirs in a result, so no parse.
        code = _code_of(text) if payload.target != "tool" else None
        return Outcome("reject", text=text, code=code, raised=True)
    except Exception as exc:
        return Outcome("error", text=f"{type(exc).__name__}: {exc}", raised=True)


def _ok_outcome(payload: Payload, structured: Any) -> Outcome:
    """Read the cap evidence out of an accepted tool result; missing evidence is a finding."""
    body = structured if isinstance(structured, dict) else {}
    count, limit, rows = body.get("row_count"), body.get("limit"), body.get("rows")
    counted = count if isinstance(count, int) and not isinstance(count, bool) else None
    row_list = rows if isinstance(rows, list) else None
    if payload.tool in ROW_TOOLS and (counted is None or row_list is None):
        return Outcome("ok", unverifiable=True, text="no structured rows or integer row_count")
    if row_list is not None:
        counted = max(counted or 0, len(row_list))  # trust the rows, not the claimed count
    return Outcome(
        "ok",
        row_count=counted,
        reported_limit=limit if isinstance(limit, int) else None,
        rows_bytes=None if row_list is None else rows_bytes(row_list),
    )


# -- echo ------------------------------------------------------------------------------


def _strings_in(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings_in(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings_in(item)


def sql_fragments(sql: str) -> list[str]:
    """Identifiers, numbers and quoted-literal contents of ``sql`` that are long enough to matter.

    Split on whitespace and punctuation (runs of word characters), plus the inside of each
    quoted literal, so an engine message that quotes one column or one string is recognised.
    Bare SQL keywords are left out (see ``SQL_KEYWORDS``).
    """
    found = re.findall(r"\w+", sql)
    found += re.findall(r"'([^']*)'", sql)
    found += re.findall(r'"([^"]*)"', sql)
    kept = {f.strip() for f in found}
    return sorted(f for f in kept if len(f) >= MIN_FRAGMENT_LEN and f.lower() not in SQL_KEYWORDS)


def encodable(payload: Payload) -> bool:
    """Whether every caller string can be written to a pipe as UTF-8 JSON.

    A lone UTF-16 surrogate (a JSON escape in the high-surrogate range) cannot: the stdio
    client fails serialising it and takes its whole session down, so such a payload is not
    sent over stdio at all.
    """
    strings = list(_strings_in(payload.args))
    strings += [s for s in (payload.uri, payload.tool, payload.prompt) if s is not None]
    try:
        for text in strings:
            text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def caller_strings(payload: Payload, *, fragments: bool = False) -> list[str]:
    """Strings the caller chose: argument leaves and keys, the URI, the tool or prompt name.

    With ``fragments``, a ``run_sql`` payload also contributes pieces of its SQL.
    """
    found = list(_strings_in(payload.args))
    if payload.uri is not None:
        found.append(payload.uri)
    if payload.tool is not None:
        found.append(payload.tool)
    if payload.prompt is not None:
        found.append(payload.prompt)
    kept = {s for s in found if len(s) >= MIN_ECHO_LEN}
    sql = payload.args.get("sql")
    if fragments and payload.tool == "run_sql" and isinstance(sql, str):
        kept.update(sql_fragments(sql))
    return sorted(kept)


def echo_hits(
    text: str,
    payload: Payload,
    vocabulary: frozenset[str] = frozenset(),
    *,
    fragments: bool = False,
) -> list[str]:
    """Caller strings that appear verbatim in ``text`` and are not the server's own words."""
    return [
        s for s in caller_strings(payload, fragments=fragments) if s in text and s not in vocabulary
    ]


def _schema_names(schema: Any) -> Iterable[str]:
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                yield from (str(name) for name in value)
            yield from _schema_names(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from _schema_names(item)


async def server_vocabulary(client: Any, service: QueryService) -> frozenset[str]:
    """Names the server publishes: tools, their parameters, prompts, resources, the catalog."""
    words: set[str] = {c for c in CODES}
    for tool in (await client.list_tools()).tools:
        words.add(tool.name)
        words.update(_schema_names(tool.input_schema))
    for prompt in (await client.list_prompts()).prompts:
        words.add(prompt.name)
        words.update(arg.name for arg in prompt.arguments or [])
    for resource in (await client.list_resources()).resources:
        words.add(str(resource.uri))
    for query in service.catalog():
        words.add(query.name)
        for param in query.params:
            words.add(param.name)
            words.update(param.values)
        for name, allowed in query.identifiers.items():
            words.add(name)
            words.update(allowed)
    return frozenset(w for w in words if len(w) >= MIN_ECHO_LEN)


# -- warehouse fingerprint -------------------------------------------------------------


def fingerprint(database: Path | str) -> dict[str, Any]:
    """File hash, schema hash and, per table, a row count plus an order-free content checksum.

    ``sum(hash(row))`` ignores row order and changes if any cell, or the number of rows,
    changes. It is read through a separate read-only connection, never the server's.
    """
    path = Path(database)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    # Same configuration as the server's connections: DuckDB refuses a second in-process
    # connection to one file with different settings, and an abandoned query may still
    # hold the server's connection open when this runs.
    connection = _warehouse.connect(path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM duckdb_tables() ORDER BY table_name"
            ).fetchall()
        ]
        columns = connection.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
        per_table = {}
        for table in tables:
            count, checksum = connection.execute(
                f'SELECT count(*), coalesce(sum(hash(t)::HUGEINT), 0) FROM "{table}" t'
            ).fetchone()
            per_table[table] = {"rows": int(count), "checksum": str(checksum)}
    finally:
        connection.close()
    return {
        "file_sha256": digest.hexdigest(),
        "schema_sha256": hashlib.sha256(json.dumps(columns).encode()).hexdigest(),
        "tables": per_table,
    }


def diff_fingerprints(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Human-readable differences; an empty list means no write was observed."""
    changes: list[str] = []
    if before["file_sha256"] != after["file_sha256"]:
        changes.append("warehouse file hash changed")
    if before["schema_sha256"] != after["schema_sha256"]:
        changes.append("schema changed")
    for table in sorted(set(before["tables"]) | set(after["tables"])):
        old, new = before["tables"].get(table), after["tables"].get(table)
        if old is None or new is None:
            changes.append(f"table {table} {'added' if old is None else 'removed'}")
        elif old["rows"] != new["rows"]:
            changes.append(f"table {table} row count {old['rows']} -> {new['rows']}")
        elif old["checksum"] != new["checksum"]:
            changes.append(f"table {table} content checksum changed")
    return changes


# -- rows and report -------------------------------------------------------------------


@dataclass
class Row:
    id: str
    kind: str
    transport: str
    expect: str
    direct: str | None
    mcp: str
    code: str | None
    echo: bool
    echoed: list[str]
    echo_raw: bool
    over_cap: bool
    over_size: bool
    unverifiable: bool
    divergence: str  # none | stricter_by_design | stricter_untagged | looser
    expect_met: bool
    target: str
    reason: str | None  # the stricter_by_design reason, when tagged
    row_count: int | None
    error: str


def classify(direct: str | None, mcp: str, tagged: bool) -> str:
    if direct is None or direct == mcp or mcp == "error":
        return "none"  # an error is reported on its own, not as a divergence
    if direct == "reject":
        return "looser"
    return "stricter_by_design" if tagged else "stricter_untagged"


def build_row(
    payload: Payload,
    direct: Outcome | None,
    mcp: Outcome,
    transport: str,
    vocabulary: frozenset[str] = frozenset(),
) -> Row:
    """Judge one payload on one transport. Pure, so the detectors can be tested by hand."""
    rejected = mcp.verdict == "reject"
    echoed = echo_hits(mcp.text, payload, vocabulary, fragments=True) if rejected else []
    raw = echo_hits(mcp.text, payload) if rejected else []
    over = is_over_cap(mcp, payload) or (direct is not None and is_over_cap(direct, payload))
    oversize = is_over_size(mcp)
    return Row(
        id=payload.id,
        kind=payload.kind,
        transport=transport,
        expect=payload.expect,
        direct=direct.verdict if direct else None,
        mcp=mcp.verdict,
        code=mcp.code,
        echo=bool(echoed),
        echoed=echoed,
        echo_raw=bool(raw),
        over_cap=over,
        over_size=oversize,
        unverifiable=mcp.unverifiable,
        divergence=classify(
            direct.verdict if direct else None, mcp.verdict, payload.stricter_by_design is not None
        ),
        expect_met=mcp.verdict == payload.expect,
        target=payload.target,
        reason=payload.stricter_by_design,
        row_count=mcp.row_count,
        error=mcp.text[:200],
    )


def _is_tool_rejection(row: Row) -> bool:
    return row.target == "tool" and row.mcp == "reject"


def summarise(rows: list[Row], write_changes: list[str]) -> dict[str, Any]:
    compared = [r for r in rows if r.direct is not None]
    equal = sum(1 for r in compared if r.direct == r.mcp)
    rejections = [r for r in rows if _is_tool_rejection(r)]
    prompt_rejections = [r for r in rows if r.target == "prompt" and r.mcp == "reject"]
    with_code = [r for r in rejections if r.code in CODES]
    bypasses = {
        "write": len(write_changes),
        "over_cap": sum(1 for r in rows if r.over_cap),
        "over_size": sum(1 for r in rows if r.over_size),
        "looser": sum(1 for r in rows if r.divergence == "looser"),
        "accepted_expect_reject": sum(1 for r in rows if r.expect == "reject" and r.mcp == "ok"),
    }
    untagged = [r for r in rows if r.divergence == "stricter_untagged"]
    rejected_benign = [r for r in rows if r.expect == "ok" and r.mcp == "reject"]
    stale = [r for r in rows if r.reason and r.divergence != "stricter_by_design"]
    return {
        "payloads": len({r.id for r in rows}),
        "results": len(rows),
        "agreement": {
            "compared": len(compared),
            "equal": equal,
            "share": round(equal / len(compared), 4) if compared else None,
        },
        "bypasses": {**bypasses, "total": sum(bypasses.values())},
        "echoes": sum(1 for r in rows if r.echo),
        "echoes_unfiltered": sum(1 for r in rows if r.echo_raw),
        "rejections": len(rejections),
        "rejections_with_code": len(with_code),
        "prompt_rejections": len(prompt_rejections),
        "prompt_rejections_uncoded": sum(1 for r in prompt_rejections if r.code not in CODES),
        "errors": [f"{r.id}/{r.transport}" for r in rows if r.mcp == "error"],
        "unverifiable": [f"{r.id}/{r.transport}" for r in rows if r.unverifiable],
        "untagged_findings": [f"{r.id}/{r.transport}" for r in untagged],
        "benign_rejected": [f"{r.id}/{r.transport}" for r in rejected_benign],
        "stale_tags": [f"{r.id}/{r.transport}" for r in stale],
        "by_kind": {
            kind: dict(Counter(r.mcp for r in rows if r.kind == kind))
            for kind in KINDS
            if any(r.kind == kind for r in rows)
        },
    }


@dataclass
class Report:
    database: str
    transports: list[str]
    rows: list[Row]
    totals: dict[str, Any]
    fingerprint_before: dict[str, Any]
    fingerprint_after: dict[str, Any]
    write_changes: list[str]
    timeout_s: float | None = None
    skipped: list[str] = field(default_factory=list)  # "<id>/<transport>" never sent

    @property
    def failures(self) -> list[str]:
        t = self.totals
        found = []
        if t["bypasses"]["total"]:
            found.append(f"{t['bypasses']['total']} bypass(es): {t['bypasses']}")
        if t["echoes"]:
            found.append(f"{t['echoes']} echo(es)")
        if t["rejections_with_code"] != t["rejections"]:
            found.append(
                f"{t['rejections'] - t['rejections_with_code']} tool rejection(s) without a code"
            )
        if t["errors"]:
            found.append(f"client error outcome(s), not rejections: {t['errors']}")
        if t["unverifiable"]:
            found.append(f"unverifiable result(s), cap evidence missing: {t['unverifiable']}")
        if t["untagged_findings"]:
            found.append(f"untagged stricter divergence: {t['untagged_findings']}")
        if t["benign_rejected"]:
            found.append(f"benign control rejected: {t['benign_rejected']}")
        return found

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["passed"] = self.passed
        body["failures"] = self.failures
        return body


# -- running ---------------------------------------------------------------------------


async def run_suite(
    database: Path | str,
    suite: list[Payload],
    transports: tuple[Transport, ...] = ("memory", "stdio"),
    *,
    work_dir: Path | None = None,
    timeout_s: float = SUITE_TIMEOUT_S,
) -> Report:
    """Run every payload directly and through each transport, then fingerprint the warehouse.

    One client per transport serves the whole suite (a stdio server takes seconds to start).
    Each side gets its own fresh cache directory so neither can serve the other's results.
    """
    database = Path(database)
    owned = None if work_dir else tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    root = Path(owned.name if owned else work_dir)  # type: ignore[arg-type]
    try:
        before = fingerprint(database)

        service = QueryService(database, cache_dir=root / "cache-direct")
        try:
            direct = {p.id: direct_verdict(service, p, timeout_s) for p in suite}
            catalog_service = service
            rows: list[Row] = []
            skipped: list[str] = []
            for transport in transports:
                async with open_client(
                    database,
                    transport=transport,
                    allow_adhoc=True,
                    cache_dir=root / f"cache-{transport}",
                    timeout_s=timeout_s,
                ) as client:
                    vocabulary = await server_vocabulary(client, catalog_service)
                    for payload in suite:
                        if transport == "stdio" and not encodable(payload):
                            skipped.append(f"{payload.id}/{transport}")
                            continue
                        outcome = await mcp_verdict(client, payload)
                        rows.append(
                            build_row(payload, direct[payload.id], outcome, transport, vocabulary)
                        )
        finally:
            service.close()

        after = fingerprint(database)
        changes = diff_fingerprints(before, after)
        return Report(
            database=Path(database).name,  # a file name only: full paths carry the user name
            transports=list(transports),
            rows=rows,
            totals=summarise(rows, changes),
            fingerprint_before=before,
            fingerprint_after=after,
            write_changes=changes,
            timeout_s=timeout_s,
            skipped=skipped,
        )
    finally:
        if owned:
            owned.cleanup()


# -- rendering -------------------------------------------------------------------------


def _cell(text: object) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def render_markdown(report: Report) -> str:
    t = report.totals
    agree = t["agreement"]
    share = "n/a" if agree["share"] is None else f"{agree['share'] * 100:.1f}%"
    b = t["bypasses"]
    lines = [
        "## Attack suite",
        "",
        f"- Warehouse: `{Path(report.database).name}`; transports: {', '.join(report.transports)}",
        f"- Payloads: {t['payloads']} ({t['results']} results across transports)",
        f"- Agreement with the bare service: {agree['equal']}/{agree['compared']} ({share}) "
        "over payloads that have a direct equivalent",
        f"- Bypasses: {b['total']} (write {b['write']}, over-cap {b['over_cap']}, "
        f"over-size {b['over_size']}, looser {b['looser']}, "
        f"accepted expect-reject {b['accepted_expect_reject']})",
        f"- Client errors: {len(t['errors'])}; unverifiable results: {len(t['unverifiable'])}",
        f"- Echoes: {t['echoes']} (unfiltered by the server's own vocabulary: "
        f"{t['echoes_unfiltered']})",
        f"- Tool rejections with a valid code: {t['rejections_with_code']}/{t['rejections']}",
        f"- Prompt rejections without a code: {t['prompt_rejections_uncoded']}/"
        f"{t['prompt_rejections']} (not gated: the code rule covers tool calls)",
        f"- Query time limit: {report.timeout_s} s on both sides",
        f"- Not sent (cannot be encoded on that transport): {report.skipped or 'none'}",
        f"- Warehouse unchanged: {'yes' if not report.write_changes else 'NO'} "
        f"(file sha256 {report.fingerprint_after['file_sha256'][:12]})",
        f"- Verdict: {'PASS' if report.passed else 'FAIL'}",
        "",
        "| kind | payloads | MCP rejected | MCP ok |",
        "|---|---:|---:|---:|",
    ]
    first = [r for r in report.rows if r.transport == (report.transports or [""])[0]]
    for kind in t["by_kind"]:
        of_kind = [r for r in first if r.kind == kind]
        rejected = sum(1 for r in of_kind if r.mcp == "reject")
        lines.append(f"| {kind} | {len(of_kind)} | {rejected} | {len(of_kind) - rejected} |")

    divergent = [r for r in report.rows if r.divergence != "none"]
    if t["stale_tags"]:
        lines += ["", f"Tagged stricter_by_design but not diverging: {t['stale_tags']}"]
    lines += ["", "### Divergences", ""]
    if divergent:
        lines += ["| id | transport | direct | MCP | class | reason |", "|---|---|---|---|---|---|"]
        lines += [
            f"| {r.id} | {r.transport} | {r.direct} | {r.mcp} | {r.divergence} "
            f"| {_cell(r.reason or '')} |"
            for r in divergent
        ]
    else:
        lines.append("None.")

    problems = [
        r
        for r in report.rows
        if r.over_cap or r.over_size or r.unverifiable or r.echo or not r.expect_met
    ]
    if problems or report.write_changes:
        lines += ["", "### Findings", "", "| id | transport | what | detail |", "|---|---|---|---|"]
        for change in report.write_changes:
            lines.append(f"| - | - | write | {_cell(change)} |")
        for r in problems:
            what = [
                label
                for label, hit in (
                    ("over-cap", r.over_cap),
                    ("over-size", r.over_size),
                    ("unverifiable", r.unverifiable),
                    ("echo", r.echo),
                    (f"expected {r.expect}, MCP {r.mcp}", not r.expect_met),
                )
                if hit
            ]
            detail = f"echoed {r.echoed}" if r.echo else r.error
            lines.append(f"| {r.id} | {r.transport} | {', '.join(what)} | {_cell(detail)[:120]} |")
    return "\n".join(lines) + "\n"


# -- command line ----------------------------------------------------------------------


def main(args: Any) -> int:
    """``mcpquery attack-suite``: exit 0 only when the report passes."""
    for noisy in ("mcp", "mcpquery"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)
    suite = load_suite(args.suite or default_suite_path())
    transports = tuple(getattr(args, "transports", None) or ("memory", "stdio"))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        database = args.db
        if database is None:
            from .minitpch import build

            database = root / "mini.duckdb"
            build(database)
        report = asyncio.run(run_suite(database, suite, transports, work_dir=root / "work"))
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render_markdown(report))
        for failure in report.failures:
            print(f"FAIL: {failure}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return 0 if report.passed else 1
