"""Open an MCP client on this server, in-process or over a real stdio subprocess.

In-process is fast and is what most tests use. Stdio is the real boundary: arguments and
results are serialised to JSON-RPC, cross a pipe, and are parsed by a separate Python
process, which is the path a desktop assistant takes. The attack suite and the latency
harness run on both, so a difference between the two would show up as a finding.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from mcp import Client, StdioServerParameters

from .server import build_server
from .service import Gateway

Transport = Literal["memory", "stdio"]


def stdio_parameters(
    database: Path | str,
    *,
    allow_adhoc: bool = False,
    cache_dir: Path | str | None = None,
    use_cache: bool = True,
    timeout_s: float | None = None,
) -> StdioServerParameters:
    args = ["-m", "mcpquery.cli", "serve", "--db", str(database)]
    if allow_adhoc:
        args.append("--allow-adhoc")
    if cache_dir is not None:
        args += ["--cache-dir", str(cache_dir)]
    if not use_cache:
        args.append("--no-cache")
    if timeout_s is not None:
        args += ["--timeout", str(timeout_s)]
    return StdioServerParameters(command=sys.executable, args=args)


@asynccontextmanager
async def open_client(
    database: Path | str,
    *,
    transport: Transport = "memory",
    allow_adhoc: bool = False,
    cache_dir: Path | str | None = None,
    use_cache: bool = True,
    timeout_s: float | None = None,
) -> AsyncIterator[Client]:
    """A connected client. The caller never sees the gateway, as a real client would not."""
    if transport == "stdio":
        params = stdio_parameters(
            database,
            allow_adhoc=allow_adhoc,
            cache_dir=cache_dir,
            use_cache=use_cache,
            timeout_s=timeout_s,
        )
        async with Client(params) as client:
            yield client
        return
    if transport != "memory":
        raise ValueError(f"unknown transport {transport!r}")
    extra = {} if timeout_s is None else {"timeout_s": timeout_s}
    with Gateway(database, cache_dir=cache_dir, use_cache=use_cache, **extra) as gateway:
        async with Client(build_server(gateway, allow_adhoc=allow_adhoc)) as client:
            yield client
