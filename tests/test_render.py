import datetime as dt
import decimal

import numpy as np
import pandas as pd

from mcpquery import minitpch
from mcpquery.render import render_frame, to_json_value


def test_scalars():
    assert to_json_value(np.int64(3)) == 3
    assert to_json_value(float("nan")) is None
    assert to_json_value(decimal.Decimal("1.10")) == "1.10"
    assert to_json_value(dt.date(1995, 3, 1)) == "1995-03-01"
    assert to_json_value(pd.NaT) is None
    assert to_json_value(b"\x01") == "01"


def test_midnight_columns_become_dates_but_timestamps_keep_time():
    frame = pd.DataFrame(
        {
            "d": pd.to_datetime(["1995-03-01", "1995-03-02"]),
            "t": pd.to_datetime(["1995-03-01 10:00", "1995-03-02 00:00"]),
        }
    )
    rows = render_frame(frame)["rows"]
    assert rows[0] == ["1995-03-01", "1995-03-01T10:00:00"]


def test_duplicate_column_names():
    frame = pd.DataFrame([[1, 2]], columns=["a", "a"])
    assert render_frame(frame) == {
        "columns": ["a", "a"],
        "rows": [[1, 2]],
        "row_count": 1,
        "size_truncated": False,
    }


def test_lists_structs_and_blobs_keep_every_element():
    assert to_json_value(np.arange(2000))[1000] == 1000
    assert to_json_value(np.array(["a", "b"], dtype=object)) == ["a", "b"]
    assert to_json_value({"k": np.int64(1), "l": [1.5, None]}) == {"k": 1, "l": [1.5, None]}
    assert to_json_value(bytearray([0, 255])) == "00ff"


def test_size_budget_truncates(monkeypatch):
    from mcpquery import render

    monkeypatch.setattr(render, "MAX_RESULT_BYTES", 100)
    body = render_frame(pd.DataFrame({"s": ["x" * 30] * 10}))
    assert body["size_truncated"] is True
    assert 0 < body["row_count"] < 10


def test_mini_build_is_deterministic(tmp_path):
    first = minitpch.build(tmp_path / "a.duckdb")
    second = minitpch.build(tmp_path / "b.duckdb")
    assert first == second
    assert first["nation"] == 25
    assert first["lineitem"] > first["orders"]
