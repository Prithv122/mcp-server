"""Parameterised, allowlisted query service over a DuckDB warehouse."""

from .catalog import Query, get, load_all
from .client import QueryResult, QueryService
from .safety import UnsafeQuery

__version__ = "0.1.0"

__all__ = [
    "Query",
    "QueryResult",
    "QueryService",
    "UnsafeQuery",
    "__version__",
    "get",
    "load_all",
]
