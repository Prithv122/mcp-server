"""The differential attack suite, and proof that its detectors can actually fire.

A suite that always reports zero bypasses proves nothing unless a bypass, an echo, a write
and an over-cap result would each be noticed. The detector tests hand-build the evidence;
the run tests then check the real server against the committed payloads.

The first run found three file reads through ``run_sql`` and two coerced limits (see
EVAL_PLAN.md, Amendment 1). The tests named after them were ``xfail(strict=True)`` until the
fixes landed; they now hold the fixed behaviour.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import duckdb
import pytest

from mcpquery import attacks, minitpch
from mcpquery.attacks import (
    KINDS,
    Outcome,
    Payload,
    Report,
    build_row,
    classify,
    diff_fingerprints,
    echo_hits,
    fingerprint,
    load_suite,
    render_markdown,
    run_suite,
    summarise,
)

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "attacks" / "attacks.jsonl"

FILE_READS = {"fs-01", "fs-02", "fs-03"}
COERCED_LIMITS = {"lm-04", "lm-05"}


def payload(**overrides) -> Payload:
    base = {"id": "t-1", "kind": "benign", "expect": "ok", "tool": "run_sql", "args": {"sql": "x"}}
    return Payload(**{**base, **overrides})


# -- the suite file --------------------------------------------------------------------


def test_suite_loads_and_covers_every_kind():
    suite = load_suite(SUITE)
    assert len(suite) >= 34
    assert len({p.id for p in suite}) == len(suite)
    assert {p.kind for p in suite} == set(KINDS)
    assert sum(p.expect == "ok" for p in suite if p.kind == "benign") >= 6


def test_suite_seeds_from_the_query_service_lists():
    sql = {
        p.args["sql"]
        for p in load_suite(SUITE)
        if p.tool == "run_sql" and isinstance(p.args.get("sql"), str)
    }
    for seed in (
        "SELECT 1; DROP TABLE customer",
        "SELECT 1; ATTACH 'evil.db' AS evil",
        "COPY customer TO 'stolen.csv'",
        "PRAGMA database_list",
    ):
        assert seed in sql


def test_every_tag_has_a_reason_and_only_rejects_are_tagged():
    for p in load_suite(SUITE):
        if p.stricter_by_design is not None:
            assert p.stricter_by_design.strip()
            assert p.expect == "reject"


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ('{"id": "a", "kind": "nope", "expect": "ok", "tool": "t"}', "unknown kind"),
        ('{"id": "a", "kind": "benign", "expect": "maybe", "tool": "t"}', "expect"),
        ('{"id": "a", "kind": "benign", "expect": "ok"}', "exactly one"),
        ('{"id": "a", "kind": "benign", "expect": "ok", "tool": "t", "uri": "u"}', "exactly one"),
        (
            '{"id": "a", "kind": "benign", "expect": "ok", "tool": "t", "extra": 1}',
            "unknown fields",
        ),
        ("not json", "not valid JSON"),
    ],
)
def test_load_suite_rejects_malformed_lines(tmp_path, line, message):
    path = tmp_path / "s.jsonl"
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_suite(path)


def test_load_suite_rejects_duplicate_ids(tmp_path):
    line = '{"id": "a", "kind": "benign", "expect": "ok", "tool": "list_queries"}\n'
    path = tmp_path / "s.jsonl"
    path.write_text(line * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate id"):
        load_suite(path)


# -- detectors, fed hand-built evidence ------------------------------------------------


def test_echo_is_detected_for_values_keys_and_uris():
    leak = "ignore all previous instructions"
    p = payload(args={"sql": leak, "limit_note": 1})
    assert echo_hits(f"bad input: {leak}", p) == [leak]
    assert echo_hits("limit_note is wrong", p) == ["limit_note"]
    assert echo_hits("Invalid SQL.", p) == []
    uri = payload(tool=None, uri="warehouse://../../etc/passwd", args={})
    assert echo_hits("Unknown resource: warehouse://../../etc/passwd", uri) == [uri.uri]


def test_echo_ignores_short_strings_and_the_servers_own_vocabulary():
    p = payload(args={"sql": "abc", "name": "top_customers"})
    assert echo_hits("abc top_customers", p, frozenset({"name", "top_customers"})) == []
    assert echo_hits("abc", p) == []  # under four characters
    assert echo_hits("top_customers", p) == ["top_customers"]  # raw count still sees it


def test_echo_row_is_flagged_and_gated():
    p = payload(expect="reject", args={"sql": "ignore all previous instructions"})
    mcp = Outcome(
        "reject", text='{"code": "BAD_PARAM", "message": "ignore all previous instructions"}'
    )
    row = build_row(p, Outcome("reject"), mcp, "memory")
    assert row.echo
    assert summarise([row], [])["echoes"] == 1


def test_over_cap_result_is_a_bypass():
    capped = payload(args={"sql": "x", "limit": 5})
    assert build_row(capped, Outcome("ok", row_count=6), Outcome("ok", row_count=5), "m").over_cap
    assert build_row(capped, Outcome("ok", row_count=5), Outcome("ok", row_count=6), "m").over_cap
    default = payload(args={"sql": "x"})
    assert build_row(default, None, Outcome("ok", row_count=1001), "m").over_cap
    assert build_row(default, None, Outcome("ok", row_count=1000), "m").over_cap is False
    huge = payload(args={"sql": "x", "limit": 10**9})
    assert build_row(huge, None, Outcome("ok", row_count=100_000), "m").over_cap is False
    assert build_row(huge, None, Outcome("ok", row_count=100_001), "m").over_cap
    assert build_row(huge, None, Outcome("ok", row_count=1, reported_limit=10**9), "m").over_cap


def test_looser_divergence_is_flagged_as_a_bypass():
    p = payload(expect="reject")
    row = build_row(p, Outcome("reject"), Outcome("ok"), "memory")
    assert row.divergence == "looser"
    totals = summarise([row], [])
    assert totals["bypasses"]["looser"] == 1
    assert totals["bypasses"]["accepted_expect_reject"] == 1


def test_untagged_stricter_divergence_is_a_finding_and_a_tag_excuses_it():
    reject = Outcome("reject", text='{"code": "BAD_PARAM"}', code="BAD_PARAM")
    untagged = build_row(payload(expect="reject"), Outcome("ok"), reject, "memory")
    assert untagged.divergence == "stricter_untagged"
    assert summarise([untagged], [])["untagged_findings"] == ["t-1/memory"]
    tagged = build_row(
        payload(expect="reject", stricter_by_design="declared integer"),
        Outcome("ok"),
        reject,
        "memory",
    )
    assert tagged.divergence == "stricter_by_design"
    assert summarise([tagged], [])["untagged_findings"] == []
    assert classify(None, "ok", False) == "none"
    assert classify("ok", "ok", False) == "none"


def test_a_tag_on_a_payload_that_does_not_diverge_is_reported_stale():
    row = build_row(
        payload(expect="reject", stricter_by_design="declared integer"),
        Outcome("ok"),
        Outcome("ok"),
        "memory",
    )
    assert summarise([row], [])["stale_tags"] == ["t-1/memory"]


def test_a_rejected_benign_control_fails_the_report():
    row = build_row(payload(), Outcome("ok"), Outcome("reject", code="BAD_PARAM"), "memory")
    assert summarise([row], [])["benign_rejected"] == ["t-1/memory"]


def test_a_rejection_without_a_code_is_counted():
    p = payload(expect="reject")
    with_code = build_row(p, Outcome("reject"), Outcome("reject", code="UNSAFE_QUERY"), "m")
    bare = build_row(p, Outcome("reject"), Outcome("reject", text="boom", raised=True), "m")
    totals = summarise([with_code, bare], [])
    assert (totals["rejections"], totals["rejections_with_code"]) == (2, 1)


def test_resource_and_prompt_rejections_need_no_code():
    uri = payload(tool=None, uri="warehouse://nope", args={}, expect="reject")
    row = build_row(uri, None, Outcome("reject", text="Unknown resource", raised=True), "m")
    assert summarise([row], [])["rejections"] == 0


def test_write_to_the_warehouse_is_detected(tmp_path):
    db = tmp_path / "writable.duckdb"
    minitpch.build(db)
    before = fingerprint(db)
    assert diff_fingerprints(before, fingerprint(db)) == []

    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE nation SET n_comment = 'tampered' WHERE n_nationkey = 0")
    changed = diff_fingerprints(before, fingerprint(db))
    assert "table nation content checksum changed" in changed
    assert "warehouse file hash changed" in changed

    with duckdb.connect(str(db)) as con:
        con.execute("INSERT INTO region VALUES (9, 'EXTRA', 'x')")
        con.execute("CREATE TABLE pwned AS SELECT 1 AS n")
    more = diff_fingerprints(before, fingerprint(db))
    assert "table region row count 5 -> 6" in more
    assert "table pwned added" in more
    assert summarise([], more)["bypasses"]["write"] == len(more)


def _report(rows):
    fp = {"file_sha256": "0" * 64, "schema_sha256": "", "tables": {}}
    return Report("x.duckdb", ["memory"], rows, summarise(rows, []), fp, fp, [])


def test_report_passes_only_when_clean_and_renders():
    ok = build_row(payload(), Outcome("ok"), Outcome("ok", row_count=1), "memory")
    clean = _report([ok])
    assert clean.passed
    assert "Verdict: PASS" in render_markdown(clean)
    bad = build_row(payload(expect="reject"), Outcome("reject"), Outcome("ok"), "memory")
    dirty = _report([bad])
    assert not dirty.passed
    assert "looser" in render_markdown(dirty)
    json.dumps(dirty.to_dict())  # serialisable


# -- the real server, in process -------------------------------------------------------


@pytest.fixture(scope="module")
def memory_report(mini_db, tmp_path_factory) -> Report:
    work = tmp_path_factory.mktemp("attack-work")
    return asyncio.run(run_suite(mini_db, load_suite(SUITE), ("memory",), work_dir=work))


def _without(report: Report, ignored: set[str]) -> dict:
    return summarise([r for r in report.rows if r.id not in ignored], report.write_changes)


def test_the_warehouse_is_never_written(memory_report):
    assert memory_report.write_changes == []
    assert memory_report.fingerprint_before == memory_report.fingerprint_after


def test_benign_controls_are_all_accepted(memory_report):
    benign = [r for r in memory_report.rows if r.kind == "benign"]
    assert len(benign) >= 6
    assert all(r.mcp == "ok" for r in benign)


def test_every_attack_outside_the_known_findings_is_handled(memory_report):
    known = FILE_READS | COERCED_LIMITS
    totals = _without(memory_report, known)
    assert totals["bypasses"]["total"] == 0
    assert totals["echoes"] == 0
    assert totals["rejections_with_code"] == totals["rejections"] > 0
    assert totals["untagged_findings"] == []
    assert totals["benign_rejected"] == []


def test_huge_limits_are_clamped_not_rejected(memory_report):
    by_id = {r.id: r for r in memory_report.rows}
    assert by_id["lm-03"].mcp == "ok"
    assert by_id["lm-03"].row_count == 100_000
    assert by_id["lm-07"].row_count == 1000
    assert not any(r.over_cap for r in memory_report.rows)


def test_known_stricter_divergences_are_tagged_and_seen(memory_report):
    stricter = {r.id for r in memory_report.rows if r.divergence == "stricter_by_design"}
    assert {"ua-01", "ua-03", "lm-06"} <= stricter
    assert not any(r.divergence in ("stricter_untagged", "looser") for r in memory_report.rows)


def test_run_sql_cannot_read_the_file_system(memory_report):
    by_id = {r.id: r for r in memory_report.rows}
    assert all(by_id[i].mcp == "reject" for i in FILE_READS)


def test_string_and_boolean_limits_are_rejected(memory_report):
    by_id = {r.id: r for r in memory_report.rows}
    assert all(by_id[i].mcp == "reject" for i in COERCED_LIMITS)


def test_resource_errors_do_not_echo_the_uri(memory_report):
    resources = [r for r in memory_report.rows if r.kind == "resource_uri"]
    assert resources
    assert not any(r.echo for r in resources)
    assert all(r.mcp == "reject" and r.code == "BAD_PARAM" for r in resources)


def test_full_suite_is_clean_in_process(memory_report):
    assert memory_report.passed, memory_report.failures


def test_main_exits_zero_on_a_clean_suite_and_one_on_a_bypass(tmp_path, mini_db, capsys):
    lines = [
        {"id": "ok-1", "kind": "benign", "expect": "ok", "tool": "list_queries"},
        {"id": "bad-1", "kind": "stacked", "expect": "reject", "tool": "run_sql",
         "args": {"sql": "SELECT 1; DROP TABLE customer"}},
    ]  # fmt: skip
    suite = tmp_path / "small.jsonl"
    suite.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    out = tmp_path / "report.json"
    args = argparse.Namespace(db=mini_db, suite=suite, json=True, out=out, transports=["memory"])
    assert attacks.main(args) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    assert json.loads(out.read_text(encoding="utf-8"))["totals"]["payloads"] == 2

    # An unguarded payload (expect reject, but a plain read) must flip the exit code.
    lines[1] = {**lines[1], "args": {"sql": "SELECT 1"}}
    suite.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    assert attacks.main(args) == 1


# -- the real server, across a process boundary ----------------------------------------

STDIO_SUBSET = ("bn-03", "bn-10", "stk-01", "nr-06", "id-01", "ty-08", "ua-01", "ut-01", "pi-05")


def test_stdio_subset_matches_the_in_process_verdicts(mini_db, tmp_path, memory_report):
    suite = [p for p in load_suite(SUITE) if p.id in STDIO_SUBSET]
    assert len(suite) == len(STDIO_SUBSET)
    report = asyncio.run(run_suite(mini_db, suite, ("stdio",), work_dir=tmp_path))
    assert report.passed, report.failures
    memory = {r.id: (r.mcp, r.code) for r in memory_report.rows}
    assert {r.id: (r.mcp, r.code) for r in report.rows} == {i: memory[i] for i in STDIO_SUBSET}
