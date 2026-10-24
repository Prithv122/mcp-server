"""Tool selection: can a small local model pick the right tool and fill in its arguments?

This is the secondary, reported-not-gated measurement in ``EVAL_PLAN.md``. A model is shown
exactly what an MCP client would show it (the tool list, the server instructions and the
``catalog://queries`` resource) and one business question. It is scored on its first
``run_query`` call: right tool, right catalog query, and every parameter the question
pins equal to a reference after normalising (dates to ISO, enum case, ints as ints).

Three choices keep the number honest and repeatable:

* **Lookups are free.** A model may call ``list_queries`` or ``describe_query`` first (up to
  3 turns in all); those calls run through the real MCP client and the answer goes back to
  the model, so looking things up is not penalised.
* **Defaults are not errors.** A pinned parameter the model leaves out is correct when the
  reference value is the query's default; an extra parameter is allowed only when it equals
  its default. Anything else changes the question that gets answered.
* **Replayable.** Every model request/response pair is cached by the hash of the request, so
  the published figure can be re-scored offline with ``--replay`` and a changed prompt or
  task fails loudly instead of silently re-calling the model.

Only the standard library talks to Ollama, so this adds no dependency.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .clients import open_client
from .server import INSTRUCTIONS

OLLAMA_URL = "http://localhost:11434/api/chat"
MAX_TURNS = 3
LOOKUP_TOOLS = ("list_queries", "describe_query")
CATEGORIES = (
    "ok",
    "no_tool_call",
    "wrong_tool",
    "wrong_query",
    "wrong_params",
    "server_rejected",
)
CATALOG_URI = "catalog://queries"
TASK_LINE = (
    "Answer the user's question by calling run_query. You may call list_queries or "
    "describe_query first if you need a parameter."
)

Transport = Callable[[dict[str, Any]], dict[str, Any]]


class ReplayMiss(RuntimeError):
    """Replay mode was asked for a request that was never recorded."""


class OllamaError(RuntimeError):
    """The local model server could not be reached or answered with an error."""


# -- statistics -------------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials (better than Wald at small n)."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


# -- tasks ------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    id: str
    task: str
    name: str  # expected catalog query
    params: dict[str, Any]  # expected parameters (required plus any the question pins)
    note: str = ""


def load_tasks(path: Path | str) -> list[Task]:
    tasks = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        expect = raw["expect"]
        if expect.get("tool") != "run_query":
            raise ValueError(f"task {raw['id']}: expect.tool must be run_query")
        tasks.append(
            Task(
                id=raw["id"],
                task=raw["task"],
                name=expect["name"],
                params=dict(expect["params"]),
                note=raw.get("note", ""),
            )
        )
    return tasks


# -- normalisation and scoring ------------------------------------------------------

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y")
_NUMERIC_DATE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T ].*)?")


def parse_date(value: Any) -> str | None:
    """A tolerant date reader: ISO with unpadded parts, slashes, and common written forms."""
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = _NUMERIC_DATE.fullmatch(text)
    if match:
        try:
            return dt.date(*(int(part) for part in match.groups())).isoformat()
        except ValueError:
            return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalise(value: Any, kind: str) -> Any:
    """Canonical form of one parameter value; ``None`` means unreadable (never equal)."""
    if kind == "date":
        return parse_date(value)
    if kind == "int":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"\s*[+-]?\d+\s*", value):
            return int(value)
        return None
    if kind in ("enum", "identifier"):
        return value.strip().upper() if isinstance(value, str) else None
    return str(value).strip() if value is not None else None


def classify_call(
    name: Any, params: Any, expected: Task, specs: dict[str, dict[str, Any]]
) -> tuple[str, str]:
    """Category and a one-line reason for one ``run_query`` call, before the server runs it."""
    if name != expected.name:
        return "wrong_query", f"called {name!r}, expected {expected.name!r}"
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return "wrong_params", "params is not an object"
    supplied = {k: v for k, v in params.items() if v is not None}
    by_name = {p["name"]: p for p in specs[expected.name]["params"]}

    for key, want in expected.params.items():
        spec = by_name[key]
        got = supplied.get(key, spec.get("default"))
        if normalise(got, spec["type"]) != normalise(want, spec["type"]):
            return "wrong_params", f"{key}: got {got!r}, expected {want!r}"
    for key, got in supplied.items():
        if key in expected.params:
            continue
        spec = by_name.get(key)
        if spec is None:
            return "wrong_params", f"unknown parameter {key!r}"
        if "default" not in spec or normalise(got, spec["type"]) != normalise(
            spec["default"], spec["type"]
        ):
            return "wrong_params", f"extra parameter {key}={got!r} is not its default"
    return "ok", ""


# -- model transport and cache -------------------------------------------------------


def ollama_transport(url: str = OLLAMA_URL, timeout: float = 600.0) -> Transport:
    def call(request: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(request).encode("utf-8")
        http = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(http, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaError(f"cannot reach Ollama at {url}: {exc}") from exc

    return call


def request_key(request: dict[str, Any]) -> str:
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def model_slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-")


@dataclass
class Completion:
    response: dict[str, Any]
    wall_s: float
    cached: bool


class ModelCache:
    """Append-only JSONL of request/response pairs, keyed by the request hash."""

    def __init__(self, path: Path | str, *, replay: bool = False) -> None:
        self.path = Path(path)
        self.replay = replay
        self._entries: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._entries[entry["key"]] = entry

    def complete(self, request: dict[str, Any], transport: Transport | None) -> Completion:
        key = request_key(request)
        hit = self._entries.get(key)
        if hit is not None:
            return Completion(hit["response"], hit["wall_s"], cached=True)
        if self.replay or transport is None:
            raise ReplayMiss(
                f"no recorded response for request {key[:12]} in {self.path} "
                "(the prompt, tools, model or task text changed since recording)"
            )
        started = time.perf_counter()
        response = transport(request)
        wall_s = round(time.perf_counter() - started, 3)
        entry = {"key": key, "request": request, "response": response, "wall_s": wall_s}
        self._entries[key] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
        return Completion(response, wall_s, cached=False)


# -- running ------------------------------------------------------------------------


@dataclass
class Turn:
    key: str
    cached: bool
    wall_s: float
    content: str
    tool_calls: list[dict[str, Any]]


@dataclass
class TaskResult:
    id: str
    expected_name: str
    category: str
    correct: bool
    server_ok: bool | None
    detail: str
    called: dict[str, Any] | None
    turns: list[Turn] = field(default_factory=list)


@dataclass
class Report:
    model: str
    results: list[TaskResult]

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def k(self) -> int:
        return sum(r.correct for r in self.results)

    @property
    def interval(self) -> tuple[float, float]:
        return wilson(self.k, self.n)

    @property
    def categories(self) -> dict[str, int]:
        counts = Counter(r.category for r in self.results)
        return {name: counts.get(name, 0) for name in CATEGORIES}

    @property
    def mean_turns(self) -> float:
        return sum(len(r.turns) for r in self.results) / self.n if self.n else 0.0

    @property
    def wall_s(self) -> float:
        return round(sum(t.wall_s for r in self.results for t in r.turns), 3)

    def to_dict(self) -> dict[str, Any]:
        low, high = self.interval
        return {
            "model": self.model,
            "k": self.k,
            "n": self.n,
            "wilson95": [round(low, 4), round(high, 4)],
            "categories": self.categories,
            "mean_turns": round(self.mean_turns, 3),
            "model_wall_s": self.wall_s,
            "results": [asdict(r) for r in self.results],
        }


def to_ollama_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """MCP tool definitions in the shape Ollama's ``tools`` field expects."""
    converted = []
    for tool in tools:
        raw = tool.model_dump(by_alias=True) if hasattr(tool, "model_dump") else tool
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": raw["name"],
                    "description": raw.get("description") or "",
                    "parameters": raw["inputSchema"],
                },
            }
        )
    return converted


def build_system_prompt(catalog_text: str) -> str:
    return f"{INSTRUCTIONS}\nCatalog ({CATALOG_URI}):\n{catalog_text}\n\n{TASK_LINE}"


def _text_of(result: Any) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content)


def _calls_of(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function", {})
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            with contextlib.suppress(ValueError):
                arguments = json.loads(arguments)
        calls.append({"name": function.get("name"), "arguments": arguments})
    return calls


async def _execute(client: Any, name: str, arguments: Any) -> tuple[bool, str]:
    """Run a tool call through the real client; a protocol error counts as a rejection."""
    if not isinstance(arguments, dict):
        return False, "arguments are not an object"
    try:
        result = await client.call_tool(name, arguments)
    except Exception as exc:  # protocol-level rejection, reported rather than raised
        return False, f"protocol error: {type(exc).__name__}"
    return (not result.is_error), _text_of(result)


async def run_task(
    client: Any,
    task: Task,
    *,
    model: str,
    system: str,
    tools: list[dict[str, Any]],
    specs: dict[str, dict[str, Any]],
    cache: ModelCache,
    transport: Transport | None,
) -> TaskResult:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task.task},
    ]
    known = {tool["function"]["name"] for tool in tools}
    turns: list[Turn] = []
    looked_up = False

    def result(category: str, detail: str, called: Any = None, ok: bool | None = None):
        return TaskResult(task.id, task.name, category, category == "ok", ok, detail, called, turns)

    for _ in range(MAX_TURNS):
        request = {
            "model": model,
            "messages": json.loads(json.dumps(messages)),
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0, "seed": 0},
        }
        done = cache.complete(request, transport)
        message = done.response.get("message", {})
        calls = _calls_of(message)
        turns.append(
            Turn(request_key(request), done.cached, done.wall_s, message.get("content", ""), calls)
        )
        if not calls:
            return result("no_tool_call", "the model answered without a tool call")

        assistant = {"role": "assistant", "content": message.get("content", "")}
        assistant["tool_calls"] = message["tool_calls"]
        messages.append(assistant)

        for call in calls:
            name, arguments = call["name"], call["arguments"]
            if name == "run_query":
                args = arguments if isinstance(arguments, dict) else {}
                category, detail = classify_call(args.get("name"), args.get("params"), task, specs)
                ok, text = await _execute(client, "run_query", arguments)
                if category == "ok" and not ok:
                    category, detail = "server_rejected", text[:200]
                return result(category, detail, call, ok)
            if name not in known:
                return result("wrong_tool", f"called unknown tool {name!r}", call)
            looked_up = True
            _, text = await _execute(client, name, arguments)
            messages.append({"role": "tool", "tool_name": name, "content": text})

    detail = "only lookup tools were called" if looked_up else "no run_query call"
    return result("wrong_tool", f"{detail} within {MAX_TURNS} turns")


async def run_eval(
    database: Path | str,
    tasks: list[Task],
    *,
    model: str,
    cache: ModelCache,
    transport: Transport | None,
) -> Report:
    results: list[TaskResult] = []
    failure: Exception | None = None
    async with open_client(database) as client:
        tools = to_ollama_tools((await client.list_tools()).tools)
        catalog_text = (await client.read_resource(CATALOG_URI)).contents[0].text
        specs = {q["name"]: q for q in json.loads(catalog_text)["queries"]}
        system = build_system_prompt(catalog_text)
        try:
            for task in tasks:
                results.append(
                    await run_task(
                        client,
                        task,
                        model=model,
                        system=system,
                        tools=tools,
                        specs=specs,
                        cache=cache,
                        transport=transport,
                    )
                )
        except (ReplayMiss, OllamaError) as exc:
            # Raised outside the client's task group, which would wrap it in an ExceptionGroup.
            failure = exc
    if failure is not None:
        raise failure
    return Report(model=model, results=results)


# -- output -------------------------------------------------------------------------


def render_markdown(report: Report) -> str:
    low, high = report.interval
    lines = [
        f"# Tool selection: {report.model}",
        "",
        f"**{report.k}/{report.n} correct** (Wilson 95% interval {low:.3f} to {high:.3f}).",
        f"Mean turns per task {report.mean_turns:.2f}; model wall time {report.wall_s:.1f} s.",
        "",
        "| Category | Tasks |",
        "|---|---|",
        *(f"| {name} | {count} |" for name, count in report.categories.items()),
        "",
        "| Task | Expected | Result | Turns | Server | Detail |",
        "|---|---|---|---|---|---|",
    ]
    for r in report.results:
        server = "-" if r.server_ok is None else ("ok" if r.server_ok else "rejected")
        lines.append(
            f"| {r.id} | {r.expected_name} | {r.category} | {len(r.turns)} | {server} | "
            f"{r.detail.replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"


def main(args: Any) -> int:
    tasks = load_tasks(args.tasks)
    runs_dir = Path(args.tasks).parent / "runs"
    cache = ModelCache(runs_dir / f"{model_slug(args.model)}.jsonl", replay=args.replay)
    transport = None if args.replay else ollama_transport()
    try:
        report = asyncio.run(
            run_eval(args.db, tasks, model=args.model, cache=cache, transport=transport)
        )
    except (ReplayMiss, OllamaError) as exc:
        print(f"tool-eval failed: {exc}", file=sys.stderr)
        return 2
    print(render_markdown(report))
    if args.out is not None:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    return 0
