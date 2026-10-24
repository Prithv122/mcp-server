"""On-disk result cache.

Keyed on everything that can change the answer: query name, bound values, row limit, and
the warehouse fingerprint. Leaving the fingerprint out is the classic way to serve
yesterday's numbers after a reload -- the cache would look correct right up until someone
noticed the totals had not moved.

Results are Parquet, so a cached result keeps its dtypes and can be read by anything.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_TTL_SECONDS = 900
DEFAULT_DIR = Path(".cache") / "queryservice"


def _stringify(value: object) -> object:
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return value


def make_key(name: str, sql: str, values: dict, limit: int, fingerprint: str) -> str:
    """Hash everything that can change the answer.

    The SQL text is in the key, not just the query name and its bound values: a catalog
    query can carry an allowlisted *identifier* (the group-by dimension), which changes
    the SQL without changing a single bound value. Keying on name+values alone served the
    by-nation result for a by-region request -- caught by a test, and exactly the kind of
    wrong answer a cache gives you silently.
    """
    payload = json.dumps(
        {
            "query": name,
            "sql": sql,
            "values": {k: _stringify(v) for k, v in sorted(values.items())},
            "limit": limit,
            "warehouse": fingerprint,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


@dataclass
class ResultCache:
    directory: Path = DEFAULT_DIR
    ttl_seconds: float = DEFAULT_TTL_SECONDS

    def path(self, key: str) -> Path:
        return Path(self.directory) / f"{key}.parquet"

    def get(self, key: str) -> pd.DataFrame | None:
        path = self.path(key)
        if not path.exists():
            return None
        if self.ttl_seconds and time.time() - path.stat().st_mtime > self.ttl_seconds:
            path.unlink(missing_ok=True)
            return None
        return pd.read_parquet(path)

    def put(self, key: str, frame: pd.DataFrame) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)

    def clear(self) -> int:
        directory = Path(self.directory)
        if not directory.exists():
            return 0
        removed = 0
        for path in directory.glob("*.parquet"):
            path.unlink()
            removed += 1
        return removed

    def stats(self) -> dict[str, object]:
        directory = Path(self.directory)
        files = sorted(directory.glob("*.parquet")) if directory.exists() else []
        return {
            "directory": str(directory),
            "entries": len(files),
            "bytes": sum(path.stat().st_size for path in files),
            "ttl_seconds": self.ttl_seconds,
        }
