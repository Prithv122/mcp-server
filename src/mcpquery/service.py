"""One ``QueryService`` behind the protocol, and the mapping from its exceptions to codes.

The MCP handlers are thin: everything a tool does goes through :class:`Gateway`, so the
differential attack suite can call the gateway, the MCP server and the bare
``QueryService`` with the same payload and compare verdicts. The gateway adds nothing to
the guards. It only classifies what they raise and strips caller input from the message.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from . import errors
from ._vendor.queryservice import QueryService, UnsafeQuery, safety
from ._vendor.queryservice.catalog import Query
from .errors import QueryError
from .render import render_frame

log = logging.getLogger(__name__)


def describe(query: Query) -> dict[str, Any]:
    """A catalog entry as the model sees it: what it answers and what it accepts."""
    params = []
    for param in query.params:
        spec: dict[str, Any] = {
            "name": param.name,
            "type": param.type,
            "required": param.required and param.default is None,
        }
        if param.description:
            spec["description"] = param.description
        if param.default is not None:
            spec["default"] = param.default
        if param.values:
            spec["values"] = list(param.values)
        if param.minimum is not None:
            spec["minimum"] = param.minimum
        if param.maximum is not None:
            spec["maximum"] = param.maximum
        params.append(spec)
    for name, allowed in query.identifiers.items():
        params.append(
            {
                "name": name,
                "type": "identifier",
                "required": False,
                "default": allowed[0],
                "values": list(allowed),
            }
        )
    return {"name": query.name, "description": query.description, "params": params}


class Gateway:
    """Serialises access to one read-only connection and classifies every failure."""

    def __init__(
        self,
        database: Path | str,
        *,
        cache_dir: Path | str | None = None,
        use_cache: bool = True,
    ) -> None:
        self.service = QueryService(database, cache_dir=cache_dir, use_cache=use_cache)
        # A DuckDB connection is not safe for concurrent use, and the SDK runs sync tools
        # on worker threads. One lock keeps two tool calls from sharing it mid-query.
        self._lock = threading.Lock()

    def close(self) -> None:
        self.service.close()

    def __enter__(self) -> Gateway:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- catalog ----------------------------------------------------------------

    def query_names(self) -> list[str]:
        return [query.name for query in self.service.catalog()]

    def list_queries(self) -> list[dict[str, str]]:
        return [
            {"name": query.name, "description": query.description}
            for query in self.service.catalog()
        ]

    def describe_query(self, name: str) -> dict[str, Any]:
        return describe(self._lookup(name))

    def _lookup(self, name: Any) -> Query:
        if not isinstance(name, str) or name not in self.query_names():
            raise errors.unknown_query(self.query_names())
        return self.service.describe(name)

    # -- execution --------------------------------------------------------------

    def run_query(
        self, name: Any, params: dict[str, Any] | None = None, limit: Any = None
    ) -> dict[str, Any]:
        query = self._lookup(name)
        if params is not None and not isinstance(params, dict):
            raise errors.bad_param("params must be an object of parameter name to value.")
        with self._classify(query=query):
            result = self.service.run(query.name, limit=_limit(limit), **(params or {}))
        return _payload(result)

    def run_sql(self, sql: Any, limit: Any = None) -> dict[str, Any]:
        if not isinstance(sql, str):
            raise errors.bad_param("sql must be a string.")
        with self._classify(query=None):
            result = self.service.run_sql(sql, limit=_limit(limit))
        return _payload(result)

    def schema(self) -> list[dict[str, Any]]:
        """Tables and columns, read from ``information_schema`` on the read-only connection."""
        sql = (
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position"
        )
        with self._lock:
            rows = self.service.connection.execute(sql).fetchall()
        tables: dict[str, list[dict[str, str]]] = {}
        for table, column, kind in rows:
            tables.setdefault(table, []).append({"name": column, "type": kind})
        return [{"table": table, "columns": columns} for table, columns in tables.items()]

    @contextmanager
    def _classify(self, *, query: Query | None) -> Iterator[None]:
        """Turn whatever the service raises into a :class:`QueryError` without caller text."""
        try:
            with self._lock:
                yield
        except QueryError:
            raise
        except UnsafeQuery as exc:
            if query is None:
                raise errors.unsafe_query() from exc
            raise _bad_catalog_param(query) from exc
        except duckdb.Error as exc:
            raise errors.query_failed(type(exc).__name__) from exc
        except Exception as exc:
            log.exception("unexpected failure in a tool call")
            raise errors.internal() from exc


def _limit(limit: Any) -> int | None:
    """Validate ``limit`` here, where the message can stay free of the caller's value.

    ``bool`` is an ``int`` in Python, so ``True`` would otherwise mean "one row".
    """
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise errors.bad_param("limit must be an integer >= 1.")
    if limit < 1:
        raise errors.bad_param("limit must be an integer >= 1.")
    return limit


def _bad_catalog_param(query: Query) -> QueryError:
    """One message for every bad argument to a catalog query: the full, server-side spec.

    The upstream message names the offending value; this one names only what is allowed,
    which is also the more useful thing for a model to read when it retries.
    """
    accepted = ", ".join(spec["name"] for spec in describe(query)["params"])
    return errors.bad_param(
        f"Invalid arguments for {query.name}. It accepts: {accepted}. "
        f"Call describe_query for types and allowed values."
    )


def _payload(result: Any) -> dict[str, Any]:
    body = render_frame(result.frame)
    body.update(
        truncated=result.truncated,
        cached=result.cached,
        elapsed_ms=round(result.elapsed_ms, 3),
        limit=result.limit,
    )
    return body


__all__ = ["Gateway", "QueryError", "describe", "safety"]
