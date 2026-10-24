"""The same call over both transports gives the same answer."""

from __future__ import annotations

import pytest

from mcpquery.clients import open_client

ARGS = {
    "name": "revenue_by_dimension",
    "params": {"start_date": "1992-01-01", "end_date": "1999-01-01", "dimension": "r_name"},
}


@pytest.mark.parametrize("transport", ["memory", "stdio"])
async def test_transports_agree(mini_db, tmp_path, transport):
    async with open_client(mini_db, transport=transport, cache_dir=tmp_path / "c") as client:
        result = await client.call_tool("run_query", ARGS)
    body = result.structured_content
    assert body["row_count"] == 5
    assert body["columns"][0] == "dimension"


async def test_stdio_rejection_has_code(mini_db, tmp_path):
    async with open_client(
        mini_db, transport="stdio", allow_adhoc=True, cache_dir=tmp_path / "c"
    ) as client:
        result = await client.call_tool("run_sql", {"sql": "DROP TABLE orders"})
    assert result.is_error
    assert '"UNSAFE_QUERY"' in result.content[0].text


async def test_unknown_transport(mini_db):
    with pytest.raises(ValueError):
        async with open_client(mini_db, transport="carrier-pigeon"):
            pass
