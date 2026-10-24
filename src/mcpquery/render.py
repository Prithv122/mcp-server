"""DataFrame -> JSON-safe rows.

JSON has no date, decimal or NaN, and pandas hands back all three (plus numpy scalars
that ``json`` refuses outright). Dates become ISO strings, decimals become strings so no
precision is lost on the way, and missing values become ``null``.
"""

from __future__ import annotations

import datetime as dt
import decimal
import math
from typing import Any

import numpy as np
import pandas as pd


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
    if isinstance(value, bytes):
        return value.hex()
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
    rows = [
        [to_json_value(cell) for cell in record]
        for record in frame.itertuples(index=False, name=None)
    ]
    return {"columns": columns, "rows": rows, "row_count": len(rows)}
