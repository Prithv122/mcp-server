from __future__ import annotations

from pathlib import Path

import pytest

from mcpquery import minitpch
from mcpquery.service import Gateway


@pytest.fixture(scope="session")
def mini_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("warehouse") / "mini.duckdb"
    minitpch.build(path)
    return path


@pytest.fixture
def gateway(mini_db: Path, tmp_path: Path):
    with Gateway(mini_db, cache_dir=tmp_path / "cache") as gw:
        yield gw
