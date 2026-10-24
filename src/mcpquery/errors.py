"""The error contract: a stable code and a message built only from server-side facts.

An MCP client is usually a language model, and a model reads error text as instructions.
So an error never repeats what the caller sent. The upstream guards are not written that
way: ``UnsafeQuery`` for a bad parameter says ``got 'DROP TABLE ...'``, and a DuckDB
binder error quotes the SQL line it choked on. Both are fine for a human at a terminal
and wrong for a tool result, so every message here is a fixed template filled with
names the server already knows (query names, parameter names, allowed values), never
with caller input.
"""

from __future__ import annotations

import json
from enum import StrEnum


class ErrorCode(StrEnum):
    UNSAFE_QUERY = "UNSAFE_QUERY"  # SQL that is not exactly one read statement
    UNKNOWN_QUERY = "UNKNOWN_QUERY"  # a catalog name that does not exist
    BAD_PARAM = "BAD_PARAM"  # an argument that fails its type, range or allowlist
    QUERY_FAILED = "QUERY_FAILED"  # a read the engine rejected (bad column, type mismatch)
    INTERNAL = "INTERNAL"  # anything unexpected; details stay in the server log


class QueryError(Exception):
    """A rejection with a stable code. ``message`` must not contain caller input."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(code, message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def to_json(self) -> str:
        return json.dumps({"code": str(self.code), "message": self.message})


def unsafe_query() -> QueryError:
    return QueryError(
        ErrorCode.UNSAFE_QUERY,
        "Only a single read statement (SELECT, WITH, FROM or TABLE) is allowed.",
    )


def no_file_access() -> QueryError:
    return QueryError(
        ErrorCode.UNSAFE_QUERY,
        "Reading files, attaching databases and loading extensions are disabled.",
    )


def unknown_query(known: list[str]) -> QueryError:
    return QueryError(
        ErrorCode.UNKNOWN_QUERY,
        f"No catalog query by that name. Known queries: {sorted(known)}.",
    )


def bad_param(detail: str) -> QueryError:
    return QueryError(ErrorCode.BAD_PARAM, detail)


def query_failed(kind: str) -> QueryError:
    return QueryError(
        ErrorCode.QUERY_FAILED,
        f"The warehouse rejected the query ({kind}). "
        "Read warehouse://schema for table and column names.",
    )


def internal() -> QueryError:
    return QueryError(ErrorCode.INTERNAL, "Internal error. The server log has the details.")
