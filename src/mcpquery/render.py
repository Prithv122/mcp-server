"""DataFrame -> JSON-safe rows, within a size budget.

JSON has no date, decimal or NaN, and pandas hands back all three (plus numpy scalars and
arrays that ``json`` refuses outright). Dates become ISO strings, LIST and STRUCT values
become JSON arrays and objects, BLOBs become hex, and missing values become ``null``.

Precision: the vendored service materialises results with DuckDB's pandas conversion,
which turns DECIMAL and HUGEINT into float64 before this module sees them. Values with
more than about 15 significant digits lose precision. TPC-H's money columns at the scale
used here (the largest sum has 14 significant digits) are inside that; a warehouse with larger exact
values would need the service to return Arrow or Python objects instead.

Size: the row cap bounds rows, not bytes; 100,000 rows of 2 KB strings is 200 MB. Rendering
stops at ``MAX_RESULT_BYTES`` and the result is marked truncated by size.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import math
from typing import Any

import numpy as np
import pandas as pd

#: Upper bound on the rendered rows, in bytes of compact JSON.
MAX_RESULT_BYTES = 4_000_000


def to_json_value(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, np.ndarray | list | tuple):
        return [to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, bool | int | str):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _dates_as_dates(frame: pd.DataFrame) -> pd.DataFrame:
    """Turn datetime columns that are all midnight back into dates.

    DuckDB ``DATE`` arrives in pandas as ``datetime64``, so ``1995-03-01`` would otherwise
    render as ``1995-03-01T00:00:00``. A column where every value is midnight is treated
    as a date column; a real timestamp column with a non-midnight value keeps its time.
    """
    # Positional, because ad-hoc SQL can return two columns with the same name.
    converted = None
    for position in range(frame.shape[1]):
        series = frame.iloc[:, position]
        if pd.api.types.is_datetime64_any_dtype(series) and series.dt.tz is None:
            present = series.dropna()
            if (present == present.dt.normalize()).all():
                if converted is None:
                    converted = frame.copy()
                converted.isetitem(position, series.dt.date.astype(object))
    return frame if converted is None else converted


def render_frame(frame: pd.DataFrame) -> dict[str, Any]:
    """Columns and rows, row-major, every cell JSON-safe."""
    columns = [str(column) for column in frame.columns]
    frame = _dates_as_dates(frame)
    rows: list[list[Any]] = []
    size = 0
    size_truncated = False
    for record in frame.itertuples(index=False, name=None):
        row = [to_json_value(cell) for cell in record]
        size += len(json.dumps(row, separators=(",", ":"))) + 1
        if size > MAX_RESULT_BYTES:
            size_truncated = True
            break
        rows.append(row)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "size_truncated": size_truncated,
    }
