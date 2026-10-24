"""The query catalog: named, typed, reviewable SQL.

A catalog query is a ``.sql`` file whose first block comment is a JSON header declaring
its parameters. That gives three things a bare "run this string" API cannot:

* the SQL is a **reviewable artifact** -- it lives in git, it diffs, it can be linted;
* parameters are **typed and validated before binding**, so a bad date fails with a clear
  error instead of a database exception halfway through a scan;
* dynamic identifiers are **declared with their allowed values**, so the set of legal
  substitutions is visible in the file rather than implied by the calling code.

Values use DuckDB named parameters (``$start_date``). Identifiers use ``{dimension}``
placeholders, which are the only thing ever formatted into SQL text -- and only after the
supplied value has been checked against the declared list.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from .safety import UnsafeQuery, validate_identifier

HEADER = re.compile(r"^\s*/\*(?P<json>.*?)\*/", re.DOTALL)

PARAM_TYPES = ("string", "int", "date", "enum")


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    description: str = ""
    required: bool = True
    default: Any = None
    values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None

    def coerce(self, value: Any) -> Any:
        """Validate and convert one supplied value. Raises ``UnsafeQuery`` on anything odd."""
        if self.type == "int":
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise UnsafeQuery(f"{self.name} must be an integer, got {value!r}") from exc
            if self.minimum is not None and number < self.minimum:
                raise UnsafeQuery(f"{self.name} must be >= {self.minimum}")
            if self.maximum is not None and number > self.maximum:
                raise UnsafeQuery(f"{self.name} must be <= {self.maximum}")
            return number

        if self.type == "date":
            if isinstance(value, dt.date):
                return value
            try:
                return dt.date.fromisoformat(str(value))
            except ValueError as exc:
                raise UnsafeQuery(f"{self.name} must be an ISO date, got {value!r}") from exc

        if self.type == "enum":
            if str(value) not in self.values:
                raise UnsafeQuery(f"{self.name} must be one of {list(self.values)}, got {value!r}")
            return str(value)

        return str(value)


@dataclass(frozen=True)
class Query:
    name: str
    description: str
    sql: str
    params: tuple[Param, ...] = ()
    identifiers: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def param(self, name: str) -> Param:
        for candidate in self.params:
            if candidate.name == name:
                return candidate
        raise UnsafeQuery(f"{self.name} has no parameter {name!r}")

    def bind(self, supplied: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Return (sql, bound values) for the supplied arguments.

        Unknown arguments are an error rather than being ignored: silently dropping a
        misspelled filter is how someone ends up reading an unfiltered number.
        """
        known = {param.name for param in self.params} | set(self.identifiers)
        unknown = set(supplied) - known
        if unknown:
            raise UnsafeQuery(f"Unknown parameters for {self.name}: {sorted(unknown)}")

        substitutions: dict[str, str] = {}
        for key, allowed in self.identifiers.items():
            value = supplied.get(key, allowed[0])
            substitutions[key] = validate_identifier(str(value), allowed, label=key)

        values: dict[str, Any] = {}
        for param in self.params:
            if param.name in supplied and supplied[param.name] is not None:
                values[param.name] = param.coerce(supplied[param.name])
            elif param.default is not None:
                values[param.name] = param.coerce(param.default)
            elif param.required:
                raise UnsafeQuery(f"{self.name} requires parameter {param.name!r}")

        return self.sql.format(**substitutions), values


def parse(name: str, text: str) -> Query:
    match = HEADER.match(text)
    if not match:
        raise ValueError(f"Query {name} is missing its JSON header comment")
    meta = json.loads(match.group("json"))

    params = []
    for param_name, spec in meta.get("params", {}).items():
        kind = spec.get("type", "string")
        if kind not in PARAM_TYPES:
            raise ValueError(f"{name}.{param_name}: unknown parameter type {kind!r}")
        params.append(
            Param(
                name=param_name,
                type=kind,
                description=spec.get("description", ""),
                required=spec.get("required", True),
                default=spec.get("default"),
                values=tuple(spec.get("values", ())),
                minimum=spec.get("minimum"),
                maximum=spec.get("maximum"),
            )
        )

    identifiers = {key: tuple(values) for key, values in meta.get("identifiers", {}).items()}
    if any(not values for values in identifiers.values()):
        raise ValueError(f"{name}: an identifier with no allowed values is a hole, not a filter")

    return Query(
        name=name,
        description=meta.get("description", ""),
        sql=text[match.end() :].strip(),
        params=tuple(params),
        identifiers=identifiers,
    )


@cache
def load_all() -> dict[str, Query]:
    """Every query shipped with the package, keyed by name (the filename stem)."""
    queries: dict[str, Query] = {}
    for entry in resources.files(f"{__package__}.queries").iterdir():
        if entry.name.endswith(".sql"):
            stem = entry.name.removesuffix(".sql")
            queries[stem] = parse(stem, entry.read_text(encoding="utf-8"))
    return dict(sorted(queries.items()))


def get(name: str) -> Query:
    queries = load_all()
    if name not in queries:
        raise UnsafeQuery(f"No such query: {name!r}. Known: {sorted(queries)}")
    return queries[name]
