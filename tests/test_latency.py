"""The latency harness: structure and bookkeeping only. Timing values are never asserted."""

from __future__ import annotations

import argparse
import json

import pytest

from mcpquery import latency
from mcpquery.latency import PROBES, percentile, render_markdown

REPEATS = 3
CELL_KEYS = {
    "tool",
    "transport",
    "cache",
    "cache_applies",
    "n",
    "p50_ms",
    "p95_ms",
    "mean_ms",
    "min_ms",
    "max_ms",
    "server_elapsed_ms_median",
    "cached",
}


def test_percentile_known_inputs():
    values = [10, 20, 30, 40, 50]
    assert percentile(values, 0) == 10
    assert percentile(values, 50) == 30
    assert percentile(values, 100) == 50
    assert percentile(values, 25) == 20
    assert percentile([4, 1, 3, 2], 50) == 2.5  # unsorted input, interpolated
    assert percentile(list(range(1, 101)), 95) == pytest.approx(95.05)
    assert percentile([7], 95) == 7


def test_percentile_rejects_bad_input():
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1, 2], 101)


@pytest.fixture(scope="module")
def result(mini_db):
    return latency.run(mini_db, REPEATS, transports=("memory",))


def test_json_structure(result):
    meta = result["meta"]
    for key in (
        "timestamp_utc",
        "python",
        "platform",
        "cpu_count",
        "duckdb_version",
        "mcp_version",
        "git_sha",
        "database",
        "table_rows",
        "repeats",
        "machine_quiet",
        "note",
    ):
        assert key in meta
    assert meta["repeats"] == REPEATS
    assert meta["machine_quiet"] is False
    assert meta["database"]["size_bytes"] > 0
    assert meta["table_rows"]["lineitem"] > 0
    json.dumps(result)  # serialisable as written

    cells = result["cells"]
    # (baseline + memory) x (cold, cached) x every probe
    assert len(cells) == 2 * 2 * len(PROBES)
    assert {(c["transport"], c["cache"]) for c in cells} == {
        (t, c) for t in ("baseline", "memory") for c in ("cold", "cached")
    }
    for cell in cells:
        assert set(cell) == CELL_KEYS
        assert cell["n"] == REPEATS
        assert cell["min_ms"] <= cell["p50_ms"] <= cell["p95_ms"] <= cell["max_ms"]
    assert [s["transport"] for s in result["startup"]] == ["memory", "memory"]


def test_cached_flag_comes_from_the_server_payload(result):
    by_key = {(c["tool"], c["transport"], c["cache"]): c for c in result["cells"]}
    for transport in ("baseline", "memory"):
        for probe in PROBES:
            cold = by_key[(probe.label, transport, "cold")]
            cached = by_key[(probe.label, transport, "cached")]
            if probe.tool == "run_query":
                assert cold["cached"] is False
                assert cached["cached"] is True
                assert cached["server_elapsed_ms_median"] is not None
            elif probe.tool == "run_sql":
                assert cached["cached"] is False  # ad-hoc SQL is never cached
                assert not cached["cache_applies"]
            else:
                assert cached["cached"] is None  # no provenance in a catalog listing


def test_markdown_renders(result):
    text = render_markdown(result)
    lines = text.splitlines()
    assert lines[0].startswith("| tool | baseline cold | baseline cached | memory cold")
    assert "stdio" not in lines[0]
    for probe in PROBES:
        assert any(line.startswith(f"| {probe.label} |") for line in lines)
    assert "not a quiet machine" in text.lower()


async def test_stdio_smoke(mini_db):
    probe = next(p for p in PROBES if p.label == "run_query:top_customers")
    cells, startups = await latency.measure(mini_db, 2, transports=("stdio",), probes=[probe])
    assert {s["transport"] for s in startups} == {"stdio"}
    assert all(s["startup_ms"] > 0 for s in startups)
    stdio = {c["cache"]: c for c in cells if c["transport"] == "stdio"}
    assert stdio["cold"]["cached"] is False
    assert stdio["cached"]["cached"] is True


def test_main_writes_json(mini_db, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(latency, "PROBES", PROBES[:1])
    monkeypatch.setattr(latency, "TRANSPORTS", ("memory",))
    out = tmp_path / "sub" / "latency.json"
    code = latency.main(argparse.Namespace(db=mini_db, repeats=2, out=out))
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["meta"]["repeats"] == 2
    assert "| tool |" in capsys.readouterr().out
