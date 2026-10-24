"""The MCP server: four tools, two resources, one prompt, over one :class:`Gateway`.

Two things the SDK does by default are wrong for a query tool, so ``GuardedServer``
overrides ``call_tool``:

* **Unknown arguments are dropped silently.** ``run_query(name=..., region="ASIA")`` with
  ``region`` outside ``params`` would run the query with no region filter and return a
  plausible, wrong number. The upstream catalog rejects unknown parameters for exactly
  this reason, so the protocol layer does too.
* **Validation errors quote the input.** Pydantic's message includes ``input_value=...``,
  so a payload in a typed argument came straight back in the error text. Errors here carry
  field names only (see :mod:`mcpquery.errors`).

``read_resource`` is overridden for the same reason: an unknown URI comes back from the SDK
as ``Unknown resource: <uri>`` with the URI again in the error data.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, CallToolResult, TextContent, ToolAnnotations
from pydantic import Strict, ValidationError

from . import __version__, errors
from .errors import QueryError
from .service import Gateway

log = logging.getLogger(__name__)

#: Validated in strict mode: the SDK's default lax mode turns ``"5"`` into 5 and ``true``
#: into 1 before the gateway ever sees the value.
Limit = Annotated[int, Strict()] | None

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

REGIONS = ("AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST")

INSTRUCTIONS = """\
Read-only access to a TPC-H sales warehouse (customers, orders, line items, nations,
regions, suppliers, parts). Prefer the named catalog queries. If you do not already know
a query's parameters, call list_queries and describe_query first, then run_query.
Dates are ISO strings (YYYY-MM-DD); end_date is exclusive, so all of 1995 is
start_date 1995-01-01, end_date 1996-01-01. Results are capped by a row limit;
`truncated: true` means there may be more rows.
"""


def error_result(error: QueryError) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=error.to_json())], is_error=True)


class GuardedServer(MCPServer):
    """``MCPServer`` with strict arguments and error text that never echoes input."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None
    ) -> Any:
        tool = self._tool_manager.get_tool(name)
        if tool is None:
            known = sorted(t.name for t in self._tool_manager.list_tools())
            return error_result(errors.bad_param(f"Unknown tool. Known tools: {known}."))

        allowed = set(tool.parameters.get("properties", {}))
        if set(arguments) - allowed:
            return error_result(
                errors.bad_param(
                    f"Unknown argument for {name}. It accepts: {sorted(allowed)}. "
                    "Catalog query parameters go inside `params`."
                )
            )

        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as exc:
            cause = _root_cause(exc)
            if isinstance(cause, QueryError):
                return error_result(cause)
            if isinstance(cause, ValidationError):
                fields = sorted({".".join(map(str, e["loc"])) for e in cause.errors()})
                return error_result(
                    errors.bad_param(f"Invalid type for argument(s) {fields} of {name}.")
                )
            log.exception("tool %s failed unexpectedly", name)
            return error_result(errors.internal())

    async def read_resource(self, uri: Any, context: Context | None = None) -> Any:
        try:
            return await super().read_resource(uri, context)
        except ResourceError as exc:
            known = sorted(str(r.uri) for r in await self.list_resources())
            templates = sorted(t.uri_template for t in await self.list_resource_templates())
            error = errors.bad_param(
                f"Unknown resource. Resources: {known}; templates: {templates}."
            )
            # An MCPError passes through the SDK's handler unchanged, without the URI in `data`.
            raise MCPError(code=INVALID_PARAMS, message=error.to_json()) from exc


def _root_cause(exc: BaseException) -> BaseException:
    """The first QueryError or ValidationError in the chain; the SDK wraps tool errors."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, QueryError | ValidationError):
            return current
        current = current.__cause__
    return exc


def _raise(error: QueryError) -> None:
    """Re-raise a gateway rejection the way the SDK expects an anticipated failure."""
    raise ToolError(error.to_json()) from error


def build_server(gateway: Gateway, *, allow_adhoc: bool = False) -> GuardedServer:
    server = GuardedServer(
        "warehouse-query",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    @server.tool(annotations=READ_ONLY)
    def list_queries() -> dict[str, Any]:
        """List the named, reviewed queries this warehouse offers, with what each answers."""
        return {"queries": gateway.list_queries()}

    @server.tool(annotations=READ_ONLY)
    def describe_query(name: str) -> dict[str, Any]:
        """Parameters of one catalog query: name, type, whether required, allowed values."""
        try:
            return gateway.describe_query(name)
        except QueryError as error:
            _raise(error)
            raise  # unreachable; keeps type checkers honest

    @server.tool(annotations=READ_ONLY)
    def run_query(
        name: str, params: dict[str, Any] | None = None, limit: Limit = None
    ) -> dict[str, Any]:
        """Run a catalog query by name.

        `params` is one object mapping the query's parameter names to values; put every
        query parameter inside it, never next to `name`. Optional parameters (those with a
        default in describe_query) may be left out. Dates are YYYY-MM-DD and end_date is
        exclusive. `limit` caps the rows returned (default 1000, maximum 100000).
        """
        try:
            return gateway.run_query(name, params, limit)
        except QueryError as error:
            _raise(error)
            raise

    if allow_adhoc:

        @server.tool(annotations=READ_ONLY)
        def run_sql(sql: str, limit: Limit = None) -> dict[str, Any]:
            """Run one read-only SQL statement (SELECT/WITH). Writes, DDL and multiple
            statements are rejected. Read warehouse://schema for tables and columns."""
            try:
                return gateway.run_sql(sql, limit)
            except QueryError as error:
                _raise(error)
                raise

    @server.resource("warehouse://schema", name="schema", mime_type="application/json")
    def schema() -> str:
        """Every table and column in the warehouse, with types."""
        return json.dumps({"tables": gateway.schema()}, indent=2)

    @server.resource("catalog://queries", name="catalog", mime_type="application/json")
    def catalog() -> str:
        """The full query catalog with parameter specs."""
        names = gateway.query_names()
        return json.dumps({"queries": [gateway.describe_query(n) for n in names]}, indent=2)

    @server.prompt()
    def revenue_report(region: str, year: int) -> str:
        """Ask for a one-page revenue summary of one region and year."""
        # Prompt arguments become prompt text, so they are allowlisted like identifiers.
        if region not in REGIONS:
            raise ValueError(f"region must be one of {list(REGIONS)}")
        year = int(year)
        if not 1992 <= year <= 1998:
            raise ValueError("year must be between 1992 and 1998 (the TPC-H order range)")
        return (
            f"Write a short revenue report for region {region} in {year}. "
            f"Use run_query with top_customers (start_date {year}-01-01, end_date "
            f"{int(year) + 1}-01-01, region {region}) and revenue_by_dimension grouped by "
            "n_name for the same window. Quote numbers exactly as returned and say if a "
            "result was truncated."
        )

    return server
