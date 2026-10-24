"""The warehouse: TPC-H, generated locally by DuckDB.

Using TPC-H rather than another invented dataset is deliberate. The schema is one an
interviewer already knows, the joins are real (8 tables, 3-deep), and nothing has to be
downloaded or licensed -- DuckDB's ``tpch`` extension generates the data itself. The scale
factor is a knob: 0.01 for tests (~60k line items), 0.1 for the numbers in the README
(~600k), and the same code path runs at 10 if you want to watch the cache earn its keep.

The first ``build`` needs network access once, to fetch the extension.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

DEFAULT_DB = Path("data") / "warehouse.duckdb"

TABLES = (
    "region",
    "nation",
    "customer",
    "supplier",
    "part",
    "partsupp",
    "orders",
    "lineitem",
)


def build(database: Path = DEFAULT_DB, scale_factor: float = 0.1) -> dict[str, int]:
    """Generate a TPC-H warehouse. Returns row counts per table."""
    scale_factor = float(scale_factor)
    if not 0 < scale_factor <= 100:
        raise ValueError("scale_factor must be in (0, 100]")

    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        database.unlink()

    con = duckdb.connect(str(database))
    try:
        con.execute("INSTALL tpch")
        con.execute("LOAD tpch")
        # dbgen takes no prepared parameters; scale_factor is float()-cast above, so the
        # only thing that can reach this string is a number.
        con.execute(f"CALL dbgen(sf={scale_factor})")
        return {
            table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES
        }
    finally:
        con.close()


def connect(database: Path = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    """Open the warehouse **read-only**.

    This is guard layer 4: even a query that defeated every check above it cannot write,
    because the connection has no write path. It also means many processes -- CLI,
    notebook, tests -- can read the same file at once.
    """
    database = Path(database)
    if not database.exists():
        raise FileNotFoundError(
            f"No warehouse at {database}. Build it first:  uv run query-service build"
        )
    return duckdb.connect(str(database), read_only=True)


def fingerprint(database: Path = DEFAULT_DB) -> str:
    """Identity of the current warehouse file, for cache keys.

    Rebuilding the warehouse changes size and mtime, so every cached result derived from
    the old one is orphaned rather than silently served.
    """
    stat = Path(database).stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"
