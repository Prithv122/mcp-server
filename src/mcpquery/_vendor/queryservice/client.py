"""The service itself -- the object the CLI, the tests, and a notebook all talk to.

    from queryservice import QueryService

    with QueryService() as service:
        result = service.run("top_customers", region="EUROPE",
                             start_date="1995-01-01", end_date="1996-01-01", limit=10)
        result.frame.head()

Every query goes through the same pipeline: look up the catalog entry, coerce and bind
parameters, prove the SQL is a single read statement, cap the rows, consult the cache,
and only then touch the database.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from . import catalog, safety, warehouse
from .cache import DEFAULT_DIR, DEFAULT_TTL_SECONDS, ResultCache, make_key
from .catalog import Query


@dataclass
class QueryResult:
    """A result set plus the provenance you need to trust it."""

    frame: pd.DataFrame
    sql: str
    values: dict[str, Any] = field(default_factory=dict)
    cached: bool = False
    elapsed_ms: float = 0.0
    limit: int = safety.DEFAULT_ROW_LIMIT

    @property
    def rows(self) -> int:
        return len(self.frame)

    @property
    def truncated(self) -> bool:
        """True when the row cap may have cut the result short."""
        return self.rows >= self.limit

    def _repr_html_(self) -> str:  # pragma: no cover - notebook convenience
        source = "cache" if self.cached else "warehouse"
        note = " (truncated by row limit)" if self.truncated else ""
        return (
            f"<p><small>{self.rows:,} rows from {source} in {self.elapsed_ms:.0f} ms{note}"
            f"</small></p>{self.frame.to_html(index=False)}"
        )


class QueryService:
    def __init__(
        self,
        database: Path | str = warehouse.DEFAULT_DB,
        *,
        cache_dir: Path | str | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        use_cache: bool = True,
    ) -> None:
        self.database = Path(database)
        self.use_cache = use_cache
        self.cache = ResultCache(
            directory=Path(cache_dir) if cache_dir else DEFAULT_DIR,
            ttl_seconds=ttl_seconds,
        )
        self._connection: duckdb.DuckDBPyConnection | None = None

    # -- plumbing ---------------------------------------------------------------

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            self._connection = warehouse.connect(self.database)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> QueryService:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- catalog ----------------------------------------------------------------

    def catalog(self) -> list[Query]:
        return list(catalog.load_all().values())

    def describe(self, name: str) -> Query:
        return catalog.get(name)

    # -- execution --------------------------------------------------------------

    def run(
        self,
        name: str,
        *,
        limit: int | None = None,
        use_cache: bool | None = None,
        **params: Any,
    ) -> QueryResult:
        """Run a catalog query by name."""
        query = catalog.get(name)
        sql, values = query.bind(params)
        return self._execute(
            sql,
            values,
            limit=limit,
            use_cache=self.use_cache if use_cache is None else use_cache,
            cache_name=name,
        )

    def run_sql(
        self,
        sql: str,
        params: Mapping[str, Any] | Sequence[Any] | None = None,
        *,
        limit: int | None = None,
    ) -> QueryResult:
        """Run ad-hoc SQL through the same guards. Never cached.

        This is the escape hatch an analyst will always need, and the point is that it is
        not a hole: the statement must be a single read, the connection cannot write, and
        the result is capped like everything else. Ad-hoc SQL is not cached because its
        text is unbounded -- caching it would fill the disk with single-use entries.
        """
        return self._execute(sql, params or {}, limit=limit, use_cache=False, cache_name=None)

    def _execute(
        self,
        sql: str,
        values: Mapping[str, Any] | Sequence[Any],
        *,
        limit: int | None,
        use_cache: bool,
        cache_name: str | None,
    ) -> QueryResult:
        safety.assert_read_only(sql)
        capped = safety.clamp_limit(limit)
        guarded = safety.wrap_with_limit(sql, capped)
        bound = dict(values) if isinstance(values, Mapping) else list(values)

        key = None
        if use_cache and cache_name is not None:
            key = make_key(
                cache_name, guarded, dict(bound), capped, warehouse.fingerprint(self.database)
            )
            started = time.perf_counter()
            hit = self.cache.get(key)
            if hit is not None:
                return QueryResult(
                    frame=hit,
                    sql=guarded,
                    values=dict(bound) if isinstance(bound, dict) else {},
                    cached=True,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                    limit=capped,
                )

        started = time.perf_counter()
        frame = self.connection.execute(guarded, bound).df()
        elapsed = (time.perf_counter() - started) * 1000

        if key is not None:
            self.cache.put(key, frame)

        return QueryResult(
            frame=frame,
            sql=guarded,
            values=dict(bound) if isinstance(bound, dict) else {},
            cached=False,
            elapsed_ms=elapsed,
            limit=capped,
        )
