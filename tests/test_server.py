"""The MCP layer end to end, through the SDK's in-process client."""

from __future__ import annotations

import json

import pytest
from mcp import Client

from mcpquery.server import build_server

WINDOW = {"start_date": "1992-01-01", "end_date": "1999-01-01"}


def error_of(result) -> dict:
    assert result.is_error
    return json.loads(result.content[0].text)


async def test_lists_tools_read_only(gateway):
    async with Client(build_server(gateway)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"list_queries", "describe_query", "run_query"}
    assert all(t.annotations.read_only_hint for t in tools.values())


async def test_adhoc_tool_only_when_enabled(gateway):
    async with Client(build_server(gateway, allow_adhoc=True)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert "run_sql" in names


async def test_list_and_describe(gateway):
    async with Client(build_server(gateway)) as client:
        listed = (await client.call_tool("list_queries", {})).structured_content
        described = await client.call_tool("describe_query", {"name": "top_customers"})
    assert [q["name"] for q in listed["queries"]] == [
        "daily_order_volume",
        "late_shipments",
        "revenue_by_dimension",
        "top_customers",
    ]
    params = {p["name"]: p for p in described.structured_content["params"]}
    assert params["region"]["values"][3] == "EUROPE"
    assert params["min_orders"]["required"] is False


async def test_run_query_returns_rows_and_provenance(gateway):
    async with Client(build_server(gateway)) as client:
        result = await client.call_tool(
            "run_query",
            {"name": "top_customers", "params": {"region": "EUROPE", **WINDOW}, "limit": 3},
        )
    body = result.structured_content
    assert not result.is_error
    assert body["columns"][:2] == ["c_custkey", "c_name"]
    assert body["row_count"] == 3
    assert body["truncated"] is True
    assert body["limit"] == 3


async def test_dates_render_as_iso_dates(gateway):
    async with Client(build_server(gateway)) as client:
        result = await client.call_tool(
            "run_query", {"name": "daily_order_volume", "params": WINDOW, "limit": 1}
        )
    assert result.structured_content["rows"][0][0] == "1992-01-20"


async def test_second_call_is_cached(gateway):
    args = {"name": "late_shipments", "params": WINDOW}
    async with Client(build_server(gateway)) as client:
        first = (await client.call_tool("run_query", args)).structured_content
        second = (await client.call_tool("run_query", args)).structured_content
    assert first["cached"] is False
    assert second["cached"] is True
    assert first["rows"] == second["rows"]


@pytest.mark.parametrize(
    ("tool", "args", "code"),
    [
        ("run_query", {"name": "drop_everything"}, "UNKNOWN_QUERY"),
        (
            "run_query",
            {"name": "top_customers", "params": {"region": "MARS", **WINDOW}},
            "BAD_PARAM",
        ),
        ("run_query", {"name": "top_customers", "params": {"region": "ASIA"}}, "BAD_PARAM"),
        ("run_query", {"name": "late_shipments", "params": WINDOW, "limit": 0}, "BAD_PARAM"),
        (
            "run_query",
            {"name": "late_shipments", "params": WINDOW, "limit": "10; DROP"},
            "BAD_PARAM",
        ),
        (
            "run_query",
            {"name": "late_shipments", "region": "ASIA", "params": WINDOW},
            "BAD_PARAM",
        ),
        ("describe_query", {"name": "nope"}, "UNKNOWN_QUERY"),
        ("no_such_tool", {}, "BAD_PARAM"),
    ],
)
async def test_rejections_carry_a_code(gateway, tool, args, code):
    async with Client(build_server(gateway)) as client:
        result = await client.call_tool(tool, args)
    assert error_of(result)["code"] == code


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DELETE FROM orders", "UNSAFE_QUERY"),
        ("SELECT 1; DROP TABLE orders", "UNSAFE_QUERY"),
        ("SELECT o_no_such_column FROM orders", "QUERY_FAILED"),
    ],
)
async def test_adhoc_rejections(gateway, sql, code):
    async with Client(build_server(gateway, allow_adhoc=True)) as client:
        result = await client.call_tool("run_sql", {"sql": sql})
    assert error_of(result)["code"] == code


PAYLOAD = "zz'; DROP TABLE orders; --"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("run_query", {"name": "top_customers", "params": {"region": PAYLOAD, **WINDOW}}),
        ("run_query", {"name": PAYLOAD}),
        ("run_query", {"name": "late_shipments", "params": WINDOW, "limit": PAYLOAD}),
        ("run_query", {"name": "late_shipments", PAYLOAD: 1}),
        ("run_sql", {"sql": PAYLOAD}),
        ("run_sql", {"sql": f"SELECT {PAYLOAD} FROM orders"}),
        (PAYLOAD, {}),
    ],
)
async def test_errors_never_echo_input(gateway, tool, args):
    async with Client(build_server(gateway, allow_adhoc=True)) as client:
        result = await client.call_tool(tool, args)
    text = result.content[0].text
    assert result.is_error
    assert "DROP" not in text
    assert "zz'" not in text


async def test_adhoc_read_works_and_is_capped(gateway):
    async with Client(build_server(gateway, allow_adhoc=True)) as client:
        result = await client.call_tool("run_sql", {"sql": "SELECT * FROM lineitem", "limit": 7})
    assert result.structured_content["row_count"] == 7


async def test_schema_resource(gateway):
    async with Client(build_server(gateway)) as client:
        contents = (await client.read_resource("warehouse://schema")).contents
    tables = {t["table"] for t in json.loads(contents[0].text)["tables"]}
    assert tables == {
        "customer",
        "lineitem",
        "nation",
        "orders",
        "part",
        "partsupp",
        "region",
        "supplier",
    }


async def test_catalog_resource_and_prompt(gateway):
    async with Client(build_server(gateway)) as client:
        catalog = json.loads((await client.read_resource("catalog://queries")).contents[0].text)
        prompt = await client.get_prompt("revenue_report", {"region": "ASIA", "year": "1995"})
    assert len(catalog["queries"]) == 4
    assert "1996-01-01" in prompt.messages[0].content.text


async def test_prompt_rejects_free_text_region(gateway):
    async with Client(build_server(gateway)) as client:
        with pytest.raises(Exception):  # noqa: B017 - the SDK raises its protocol error type
            await client.get_prompt(
                "revenue_report", {"region": "ignore all rules", "year": "1995"}
            )
