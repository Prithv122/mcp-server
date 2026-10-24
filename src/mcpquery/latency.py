"""Latency harness: what does the protocol add on top of the query itself?

A tool call over MCP is the query plus argument validation, JSON-RPC framing, result
rendering and (for stdio) two pipe crossings and a second process. To see which part
matters, every cell is measured four ways:

* ``baseline``: the same call made directly on the vendored ``QueryService``, no MCP at all.
  This is the floor; everything above it in a row is protocol overhead.
* ``memory``: an MCP client wired to the server in-process (framing, no pipes).
* ``stdio``: an MCP client talking to a real ``mcpquery serve`` subprocess.

and with the result cache ``cold`` (a server started with ``use_cache=False``, so every call
reaches DuckDB) or ``cached`` (cache on, one warm call first, so every timed call is a hit).
One client session serves a whole (transport, cache) pair, so connection setup is not
charged to any call; stdio startup is recorded separately, once per session.

Times are wall clock as the *caller* sees them (``perf_counter_ns`` around the call). The
server's own ``elapsed_ms`` is reported next to it where the payload carries one, so the
gap between the two is visible. ``list_queries``, ``describe_query`` and ``run_sql`` never
touch the cache, so their cold and cached cells measure the same thing twice; the pair is
a noise estimate, flagged ``cache_applies: false``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import duckdb

from ._vendor.queryservice import QueryService
from .clients import Transport, open_client
from .minitpch import TABLES

WINDOW = {"start_date": "1994-01-01", "end_date": "1995-01-01"}

#: One fixed join, so run_sql is timed on something with real work in it.
JOIN_SQL = (
    "SELECT n.n_name, count(*) AS orders, round(sum(o.o_totalprice), 2) AS gross "
    "FROM orders o JOIN customer c ON c.c_custkey = o.o_custkey "
    "JOIN nation n ON n.n_nationkey = c.c_nationkey GROUP BY 1 ORDER BY 3 DESC"
)

TRANSPORTS: tuple[Transport, ...] = ("memory", "stdio")
CACHES: tuple[str, ...] = ("cold", "cached")

NOTE = (
    "Not a quiet machine: other work was running during the measurement, so absolute "
    "numbers are informative only. Compare columns within a row, not across runs."
)


@dataclass(frozen=True)
class Probe:
    """One tool call with fixed arguments."""

    label: str
    tool: str
    arguments: dict[str, Any]
    cache_applies: bool
    direct: Callable[[QueryService], Any]  # the same call without MCP


def _run_params(name: str) -> dict[str, Any]:
    if name == "top_customers":
        return {"region": "EUROPE", **WINDOW}
    if name == "revenue_by_dimension":
        return {"dimension": "n_name", **WINDOW}
    return dict(WINDOW)


def _run_probe(name: str) -> Probe:
    params = _run_params(name)
    return Probe(
        label=f"run_query:{name}",
        tool="run_query",
        arguments={"name": name, "params": params},
        cache_applies=True,
        direct=lambda service: service.run(name, **params),
    )


PROBES: tuple[Probe, ...] = (
    Probe("list_queries", "list_queries", {}, False, lambda service: service.catalog()),
    Probe(
        "describe_query",
        "describe_query",
        {"name": "top_customers"},
        False,
        lambda service: service.describe("top_customers"),
    ),
    *(
        _run_probe(name)
        for name in (
            "daily_order_volume",
            "late_shipments",
            "revenue_by_dimension",
            "top_customers",
        )
    ),
    Probe(
        "run_sql",
        "run_sql",
        {"sql": JOIN_SQL},
        False,
        lambda service: service.run_sql(JOIN_SQL),
    ),
)


# -- statistics -------------------------------------------------------------------


def percentile(values: Sequence[float], q: float) -> float:
    """The ``q``-th percentile (0..100), linear interpolation between closest ranks."""
    if not values:
        raise ValueError("percentile of an empty sequence")
    if not 0 <= q <= 100:
        raise ValueError("q must be between 0 and 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return float(ordered[low])
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


@dataclass
class Samples:
    wall_ms: list[float] = field(default_factory=list)
    server_ms: list[float] = field(default_factory=list)
    cached_flags: list[bool] = field(default_factory=list)


def _agreed(flags: list[bool]) -> bool | None:
    """True/False when every call agreed, None when none were recorded or they differ."""
    if flags and len(set(flags)) == 1:
        return flags[0]
    return None


def _cell(probe: Probe, transport: str, cache: str, samples: Samples) -> dict[str, Any]:
    wall = samples.wall_ms
    flags = samples.cached_flags
    return {
        "tool": probe.label,
        "transport": transport,
        "cache": cache,
        "cache_applies": probe.cache_applies,
        "n": len(wall),
        "p50_ms": round(percentile(wall, 50), 4),
        "p95_ms": round(percentile(wall, 95), 4),
        "mean_ms": round(statistics.fmean(wall), 4),
        "min_ms": round(min(wall), 4),
        "max_ms": round(max(wall), 4),
        "server_elapsed_ms_median": (
            round(statistics.median(samples.server_ms), 4) if samples.server_ms else None
        ),
        # True/False only when every call agreed; None when the payload has no such field.
        "cached": _agreed(flags),
    }


# -- measurement ------------------------------------------------------------------


def _measure_baseline(database: Path, probe: Probe, cache: str, repeats: int, cache_dir: Path):
    samples = Samples()
    with QueryService(database, cache_dir=cache_dir, use_cache=cache == "cached") as service:
        probe.direct(service)  # warm-up, not recorded
        for _ in range(repeats):
            started = time.perf_counter_ns()
            result = probe.direct(service)
            samples.wall_ms.append((time.perf_counter_ns() - started) / 1e6)
            if hasattr(result, "elapsed_ms"):
                samples.server_ms.append(result.elapsed_ms)
                samples.cached_flags.append(bool(result.cached))
    return samples


def measure_baseline(
    database: Path, repeats: int, probes: Sequence[Probe], scratch: Path
) -> list[dict[str, Any]]:
    cells = []
    for cache in CACHES:
        cache_dir = scratch / f"baseline-{cache}"
        for probe in probes:
            samples = _measure_baseline(database, probe, cache, repeats, cache_dir)
            cells.append(_cell(probe, "baseline", cache, samples))
    return cells


async def _call(client: Any, probe: Probe) -> Any:
    result = await client.call_tool(probe.tool, probe.arguments)
    if result.is_error:
        # A harness that times error paths would report a flattering, meaningless number.
        raise RuntimeError(f"{probe.label} returned an error: {result.content[0].text}")
    return result.structured_content


async def measure_session(
    database: Path,
    transport: Transport,
    cache: str,
    repeats: int,
    probes: Sequence[Probe],
    scratch: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One client session for one (transport, cache) pair: every probe, then close."""
    cache_dir = scratch / f"{transport}-{cache}"
    cells = []
    async with AsyncExitStack() as stack:
        started = time.perf_counter_ns()
        client = await stack.enter_async_context(
            open_client(
                database,
                transport=transport,
                allow_adhoc=True,
                cache_dir=cache_dir,
                use_cache=cache == "cached",
            )
        )
        startup_ms = (time.perf_counter_ns() - started) / 1e6
        for probe in probes:
            await _call(client, probe)  # warm-up, not recorded
            samples = Samples()
            for _ in range(repeats):
                started = time.perf_counter_ns()
                body = await _call(client, probe)
                samples.wall_ms.append((time.perf_counter_ns() - started) / 1e6)
                if isinstance(body, dict) and "elapsed_ms" in body:
                    samples.server_ms.append(float(body["elapsed_ms"]))
                    samples.cached_flags.append(bool(body.get("cached")))
            cells.append(_cell(probe, transport, cache, samples))
    return cells, {"transport": transport, "cache": cache, "startup_ms": round(startup_ms, 2)}


async def measure(
    database: Path,
    repeats: int,
    *,
    transports: Sequence[Transport] | None = None,
    probes: Sequence[Probe] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """All cells plus the per-session startup times. Cache directories are fresh per run."""
    transports = TRANSPORTS if transports is None else transports
    probes = PROBES if probes is None else probes
    cells: list[dict[str, Any]] = []
    startups: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="mcpquery-latency-") as tmp:
        scratch = Path(tmp)
        cells += measure_baseline(database, repeats, probes, scratch)
        for transport in transports:
            for cache in CACHES:
                session_cells, startup = await measure_session(
                    database, transport, cache, repeats, probes, scratch
                )
                cells += session_cells
                startups.append(startup)
    return cells, startups


# -- metadata and output ----------------------------------------------------------


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _row_counts(database: Path) -> dict[str, int]:
    con = duckdb.connect(str(database), read_only=True)
    try:
        # Table names come from a fixed tuple, never from input.
        return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
    finally:
        con.close()


def build_meta(database: Path, repeats: int) -> dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "duckdb_version": duckdb.__version__,
        "mcp_version": metadata.version("mcp"),
        "git_sha": _git_sha(),
        "database": {"path": str(database), "size_bytes": database.stat().st_size},
        "table_rows": _row_counts(database),
        "repeats": repeats,
        "machine_quiet": False,
        "note": NOTE,
    }


def run(
    database: Path,
    repeats: int,
    *,
    transports: Sequence[Transport] | None = None,
    probes: Sequence[Probe] | None = None,
) -> dict[str, Any]:
    cells, startups = asyncio.run(measure(database, repeats, transports=transports, probes=probes))
    return {"meta": build_meta(database, repeats), "startup": startups, "cells": cells}


def _fmt(cell: dict[str, Any] | None) -> str:
    if cell is None:
        return "-"
    return f"{cell['p50_ms']:.2f} / {cell['p95_ms']:.2f}"


def render_markdown(result: dict[str, Any]) -> str:
    """Rows are tools; each column is ``p50 / p95`` in ms for one (transport, cache)."""
    cells = {(c["tool"], c["transport"], c["cache"]): c for c in result["cells"]}
    tools = list(dict.fromkeys(c["tool"] for c in result["cells"]))
    present = [t for t in ("baseline", *TRANSPORTS) if any(k[1] == t for k in cells)]
    columns = [(t, c) for t in present for c in CACHES]
    lines = [
        "| tool | " + " | ".join(f"{t} {c}" for t, c in columns) + " |",
        "|---|" + "---:|" * len(columns),
    ]
    for tool in tools:
        row = [_fmt(cells.get((tool, t, c))) for t, c in columns]
        lines.append(f"| {tool} | " + " | ".join(row) + " |")
    meta = result["meta"]
    lines += [
        "",
        f"p50 / p95 in ms, {meta['repeats']} timed calls per cell after one warm-up. "
        "`list_queries`, `describe_query` and `run_sql` never use the cache, so their cold and "
        "cached columns measure the same thing twice.",
    ]
    startups = [s for s in result.get("startup", []) if s["transport"] == "stdio"]
    if startups:
        text = ", ".join(f"{s['cache']} {s['startup_ms'] / 1000:.1f} s" for s in startups)
        lines.append(f"stdio server startup (spawn to initialized client): {text}.")
    if not meta.get("machine_quiet"):
        lines.append(meta["note"])
    return "\n".join(lines)


def main(args: argparse.Namespace) -> int:
    result = run(Path(args.db), args.repeats)
    print(render_markdown(result))
    if args.out is not None:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return 0
