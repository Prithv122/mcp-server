"""``mcpquery`` command line: serve over stdio, build a warehouse, run the measurements."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

DEFAULT_DB = Path("data") / "warehouse.duckdb"


def _serve(args: argparse.Namespace) -> int:
    from .server import build_server
    from .service import Gateway

    # stdout is the protocol channel on stdio; logs must go to stderr or they corrupt it.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    gateway = Gateway(args.db, cache_dir=args.cache_dir, use_cache=not args.no_cache)
    try:
        build_server(gateway, allow_adhoc=args.allow_adhoc).run("stdio")
    finally:
        gateway.close()
    return 0


def _build_db(args: argparse.Namespace) -> int:
    if args.mini:
        from .minitpch import build

        counts = build(args.db)
    else:
        from ._vendor.queryservice import warehouse

        counts = warehouse.build(Path(args.db), scale_factor=args.sf)
    for table, rows in counts.items():
        print(f"{table:<10} {rows:>10,}")
    print(f"wrote {args.db}")
    return 0


def _attack_suite(args: argparse.Namespace) -> int:
    from .attacks import main as attacks_main

    return attacks_main(args)


def _latency(args: argparse.Namespace) -> int:
    from .latency import main as latency_main

    return latency_main(args)


def _tool_eval(args: argparse.Namespace) -> int:
    from .toolselect import main as toolselect_main

    return toolselect_main(args)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="mcpquery", description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the MCP server over stdio")
    serve.add_argument("--db", type=Path, default=DEFAULT_DB, help="DuckDB warehouse file")
    serve.add_argument("--cache-dir", type=Path, default=None, help="Result cache directory")
    serve.add_argument("--no-cache", action="store_true", help="Disable the result cache")
    serve.add_argument(
        "--allow-adhoc", action="store_true", help="Also expose run_sql (off by default)"
    )
    serve.set_defaults(func=_serve)

    build = sub.add_parser("build-db", help="Generate the TPC-H warehouse")
    build.add_argument("--db", type=Path, default=DEFAULT_DB)
    build.add_argument("--sf", type=float, default=0.1, help="TPC-H scale factor")
    build.add_argument(
        "--mini", action="store_true", help="Build the offline test fixture instead of TPC-H"
    )
    build.set_defaults(func=_build_db)

    attack = sub.add_parser("attack-suite", help="Run the differential attack suite")
    attack.add_argument("--db", type=Path, default=None, help="Warehouse (default: mini fixture)")
    attack.add_argument("--suite", type=Path, default=None, help="attacks.jsonl to run")
    attack.add_argument("--json", action="store_true", help="Print the report as JSON")
    attack.add_argument("--out", type=Path, default=None, help="Also write the report here")
    attack.set_defaults(func=_attack_suite)

    latency = sub.add_parser("latency", help="Measure per-tool latency, cold vs cached")
    latency.add_argument("--db", type=Path, default=DEFAULT_DB)
    latency.add_argument("--repeats", type=int, default=30)
    latency.add_argument("--out", type=Path, default=None)
    latency.set_defaults(func=_latency)

    tool_eval = sub.add_parser("tool-eval", help="Tool-selection accuracy with a local model")
    tool_eval.add_argument("--db", type=Path, default=DEFAULT_DB)
    tool_eval.add_argument("--tasks", type=Path, default=Path("eval") / "tool_tasks.jsonl")
    tool_eval.add_argument("--model", default="qwen2.5:7b-instruct-q4_K_M")
    tool_eval.add_argument("--out", type=Path, default=None)
    tool_eval.add_argument("--replay", action="store_true", help="Use recorded responses only")
    tool_eval.set_defaults(func=_tool_eval)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
