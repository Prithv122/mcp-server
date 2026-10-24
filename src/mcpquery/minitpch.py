"""A tiny TPC-H-shaped warehouse for tests and CI, built without the ``tpch`` extension.

The real warehouse comes from DuckDB's ``dbgen``, which needs the extension downloaded
once. Tests should not need the network, so this builds the same eight tables with the
same column names and types, filled with a few dozen deterministic rows. Every value is
an arithmetic function of the row number, so two builds are identical and no random
seed has to be carried around. The 25 nations and 5 regions are TPC-H's real ones, so the
catalog's enum values (``EUROPE``, ``AUTOMOBILE``...) match rows here as they do at scale.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

REGIONS = ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]

#: (name, region key), in TPC-H nation-key order.
NATIONS = [
    ("ALGERIA", 0), ("ARGENTINA", 1), ("BRAZIL", 1), ("CANADA", 1), ("EGYPT", 4),
    ("ETHIOPIA", 0), ("FRANCE", 3), ("GERMANY", 3), ("INDIA", 2), ("INDONESIA", 2),
    ("IRAN", 4), ("IRAQ", 4), ("JAPAN", 2), ("JORDAN", 4), ("KENYA", 0),
    ("MOROCCO", 0), ("MOZAMBIQUE", 0), ("PERU", 1), ("CHINA", 2), ("ROMANIA", 3),
    ("SAUDI ARABIA", 4), ("VIETNAM", 2), ("RUSSIA", 3), ("UNITED KINGDOM", 3),
    ("UNITED STATES", 1),
]  # fmt: skip

SEGMENTS = ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]
PRIORITIES = ["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW"]
SHIPMODES = ["AIR", "FOB", "MAIL", "RAIL", "REG AIR", "SHIP", "TRUCK"]

N_SUPPLIERS = 10
N_CUSTOMERS = 50
N_PARTS = 20
N_ORDERS = 60
LINES_PER_ORDER = 3  # orders get 1..3 lines


def _list(values: list[str]) -> str:
    return "[" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + "]"


def statements() -> list[str]:
    nations = ", ".join(f"({k}, '{name}', {region})" for k, (name, region) in enumerate(NATIONS))
    return [
        """CREATE TABLE region (r_regionkey INTEGER NOT NULL, r_name VARCHAR NOT NULL,
            r_comment VARCHAR NOT NULL)""",
        f"""INSERT INTO region SELECT i, ({_list(REGIONS)})[i + 1], 'region ' || i
            FROM range({len(REGIONS)}) t(i)""",
        """CREATE TABLE nation (n_nationkey INTEGER NOT NULL, n_name VARCHAR NOT NULL,
            n_regionkey INTEGER NOT NULL, n_comment VARCHAR NOT NULL)""",
        f"""INSERT INTO nation SELECT k, name, region, 'nation ' || k
            FROM (VALUES {nations}) v(k, name, region)""",
        """CREATE TABLE supplier (s_suppkey BIGINT NOT NULL, s_name VARCHAR NOT NULL,
            s_address VARCHAR NOT NULL, s_nationkey INTEGER NOT NULL, s_phone VARCHAR NOT NULL,
            s_acctbal DECIMAL(15,2) NOT NULL, s_comment VARCHAR NOT NULL)""",
        f"""INSERT INTO supplier SELECT i, 'Supplier#' || lpad(i::VARCHAR, 9, '0'),
            'addr ' || i, (i * 7) % 25, '10-' || (100 + i), ((i * 137) % 10000) / 1.0 - 999,
            'supplier ' || i FROM range(1, {N_SUPPLIERS + 1}) t(i)""",
        """CREATE TABLE customer (c_custkey BIGINT NOT NULL, c_name VARCHAR NOT NULL,
            c_address VARCHAR NOT NULL, c_nationkey INTEGER NOT NULL, c_phone VARCHAR NOT NULL,
            c_acctbal DECIMAL(15,2) NOT NULL, c_mktsegment VARCHAR NOT NULL,
            c_comment VARCHAR NOT NULL)""",
        f"""INSERT INTO customer SELECT i, 'Customer#' || lpad(i::VARCHAR, 9, '0'),
            'addr ' || i, (i * 11) % 25, '20-' || (100 + i), ((i * 211) % 10000) / 1.0 - 999,
            ({_list(SEGMENTS)})[(i % 5) + 1], 'customer ' || i
            FROM range(1, {N_CUSTOMERS + 1}) t(i)""",
        """CREATE TABLE part (p_partkey BIGINT NOT NULL, p_name VARCHAR NOT NULL,
            p_mfgr VARCHAR NOT NULL, p_brand VARCHAR NOT NULL, p_type VARCHAR NOT NULL,
            p_size INTEGER NOT NULL, p_container VARCHAR NOT NULL,
            p_retailprice DECIMAL(15,2) NOT NULL, p_comment VARCHAR NOT NULL)""",
        f"""INSERT INTO part SELECT i, 'part ' || i, 'Manufacturer#' || (i % 5 + 1),
            'Brand#' || (i % 5 + 1) || (i % 5 + 1), 'STANDARD BRUSHED STEEL', (i % 50) + 1,
            'SM BOX', 900 + i, 'part ' || i FROM range(1, {N_PARTS + 1}) t(i)""",
        """CREATE TABLE partsupp (ps_partkey BIGINT NOT NULL, ps_suppkey BIGINT NOT NULL,
            ps_availqty BIGINT NOT NULL, ps_supplycost DECIMAL(15,2) NOT NULL,
            ps_comment VARCHAR NOT NULL)""",
        f"""INSERT INTO partsupp SELECT p, ((p + s) % {N_SUPPLIERS}) + 1, (p * 37 + s) % 9999,
            ((p * 13 + s * 7) % 1000) / 1.0 + 1, 'partsupp'
            FROM range(1, {N_PARTS + 1}) a(p), range(2) b(s)""",
        """CREATE TABLE orders (o_orderkey BIGINT NOT NULL, o_custkey BIGINT NOT NULL,
            o_orderstatus VARCHAR NOT NULL, o_totalprice DECIMAL(15,2) NOT NULL,
            o_orderdate DATE NOT NULL, o_orderpriority VARCHAR NOT NULL,
            o_clerk VARCHAR NOT NULL, o_shippriority INTEGER NOT NULL,
            o_comment VARCHAR NOT NULL)""",
        f"""INSERT INTO orders SELECT i, ((i * 7) % {N_CUSTOMERS}) + 1,
            CASE i % 3 WHEN 0 THEN 'F' WHEN 1 THEN 'O' ELSE 'P' END,
            1000 + (i * 977) % 50000, DATE '1992-01-01' + INTERVAL ((i * 41) % 2400) DAY,
            ({_list(PRIORITIES)})[(i % 5) + 1], 'Clerk#' || lpad((i % 9)::VARCHAR, 9, '0'),
            0, 'order ' || i FROM range(1, {N_ORDERS + 1}) t(i)""",
        """CREATE TABLE lineitem (l_orderkey BIGINT NOT NULL, l_partkey BIGINT NOT NULL,
            l_suppkey BIGINT NOT NULL, l_linenumber BIGINT NOT NULL,
            l_quantity DECIMAL(15,2) NOT NULL, l_extendedprice DECIMAL(15,2) NOT NULL,
            l_discount DECIMAL(15,2) NOT NULL, l_tax DECIMAL(15,2) NOT NULL,
            l_returnflag VARCHAR NOT NULL, l_linestatus VARCHAR NOT NULL,
            l_shipdate DATE NOT NULL, l_commitdate DATE NOT NULL, l_receiptdate DATE NOT NULL,
            l_shipinstruct VARCHAR NOT NULL, l_shipmode VARCHAR NOT NULL,
            l_comment VARCHAR NOT NULL)""",
        f"""INSERT INTO lineitem
            SELECT o_orderkey, ((o_orderkey * 3 + n) % {N_PARTS}) + 1,
                ((o_orderkey + n) % {N_SUPPLIERS}) + 1, n,
                1 + (o_orderkey * 5 + n) % 50, 100 + (o_orderkey * 113 + n * 17) % 9000,
                ((o_orderkey + n) % 11) / 100.0, ((o_orderkey + 2 * n) % 9) / 100.0,
                CASE (o_orderkey + n) % 3 WHEN 0 THEN 'R' WHEN 1 THEN 'A' ELSE 'N' END,
                CASE o_orderstatus WHEN 'F' THEN 'F' ELSE 'O' END,
                o_orderdate + INTERVAL (1 + (o_orderkey + n) % 30) DAY,
                o_orderdate + INTERVAL (20 + (o_orderkey * n) % 20) DAY,
                o_orderdate + INTERVAL (5 + (o_orderkey * 3 + n * 5) % 50) DAY,
                'DELIVER IN PERSON', ({_list(SHIPMODES)})[((o_orderkey + n) % 7) + 1],
                'line ' || n
            FROM orders, range(1, {LINES_PER_ORDER + 1}) t(n)
            WHERE n <= 1 + o_orderkey % {LINES_PER_ORDER}""",
    ]


TABLES = ("region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem")


def build(database: Path | str) -> dict[str, int]:
    """Write the mini warehouse to ``database`` (replacing it). Returns row counts."""
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        database.unlink()
    con = duckdb.connect(str(database))
    try:
        for statement in statements():
            con.execute(statement)
        # Table names come from the fixed tuple above, never from input.
        return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
    finally:
        con.close()
