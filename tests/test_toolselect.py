"""Tool-selection scoring, the lookup loop and replay, with a scripted model (no Ollama)."""

from __future__ import annotations

import itertools
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from mcpquery._vendor.queryservice.catalog import load_all
from mcpquery.toolselect import (
    MAX_TURNS,
    ModelCache,
    ReplayMiss,
    Task,
    classify_call,
    load_tasks,
    parse_date,
    render_markdown,
    run_eval,
    wilson,
)

TASKS_FILE = Path(__file__).resolve().parent.parent / "eval" / "tool_tasks.jsonl"

TOP = Task(
    id="t1",
    task="Top customers in Europe during 1996",
    name="top_customers",
    params={"region": "EUROPE", "start_date": "1996-01-01", "end_date": "1997-01-01"},
)
GOOD = {"region": "EUROPE", "start_date": "1996-01-01", "end_date": "1997-01-01"}


def call(name: str, arguments: Any) -> dict[str, Any]:
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }
    }


def run_query(query: str, params: Any = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"name": query}
    if params is not None:
        arguments["params"] = params
    return call("run_query", arguments)


_runs = itertools.count()


class FakeModel:
    """Replays scripted responses and records every request it was sent."""

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        return self.responses.pop(0)


async def score(mini_db, tmp_path, task: Task, *responses: dict[str, Any]):
    model = FakeModel(*responses)
    # A fresh cache per call, or identical requests would be answered from an earlier script.
    cache = ModelCache(tmp_path / f"run{next(_runs)}.jsonl")
    report = await run_eval(mini_db, [task], model="fake", cache=cache, transport=model)
    return report.results[0], model


async def test_correct_call_scores_correct(mini_db, tmp_path):
    result, model = await score(mini_db, tmp_path, TOP, run_query("top_customers", GOOD))
    assert result.correct and result.category == "ok"
    assert result.server_ok is True
    assert len(result.turns) == 1
    request = model.requests[0]
    assert request["stream"] is False
    assert request["options"] == {"temperature": 0, "seed": 0}
    assert {t["function"]["name"] for t in request["tools"]} == {
        "list_queries",
        "describe_query",
        "run_query",
    }
    assert "catalog://queries" in request["messages"][0]["content"]
    assert "top_customers" in request["messages"][0]["content"]


def specs_of(gateway) -> dict:
    return {n: gateway.describe_query(n) for n in gateway.query_names()}


def test_dates_are_normalised(gateway):
    params = {"region": "EUROPE", "start_date": "1996-1-1", "end_date": "1997/01/01"}
    assert classify_call("top_customers", params, TOP, specs_of(gateway))[0] == "ok"
    bad = {**params, "start_date": "Q1 1996"}
    assert classify_call("top_customers", bad, TOP, specs_of(gateway))[0] == "wrong_params"


def test_enum_case_and_int_types_are_normalised(gateway):
    specs = specs_of(gateway)
    pinned = Task("p", "q", "top_customers", {**GOOD, "min_orders": 3})
    params = {**GOOD, "region": "europe", "min_orders": "3"}
    assert classify_call("top_customers", params, pinned, specs)[0] == "ok"
    assert classify_call("top_customers", {**params, "min_orders": True}, pinned, specs)[0] == (
        "wrong_params"
    )
    seg = Task(
        "s", "q", "daily_order_volume", {"start_date": "1996-01-01", "end_date": "1997-01-01"}
    )
    base = {"start_date": "1996-01-01", "end_date": "1997-01-01"}
    assert classify_call("daily_order_volume", {**base, "segment": "all"}, seg, specs)[0] == "ok"
    assert classify_call("daily_order_volume", {**base, "segment": None}, seg, specs)[0] == "ok"


async def test_unpadded_date_is_scored_but_server_rejects(mini_db, tmp_path):
    params = {"region": "EUROPE", "start_date": "1996-1-1", "end_date": "1997-01-01"}
    result, _ = await score(mini_db, tmp_path, TOP, run_query("top_customers", params))
    # Scoring is tolerant, but a call the server refuses is not a success.
    assert result.category == "server_rejected"
    assert result.server_ok is False
    assert not result.correct


def test_parse_date_is_tolerant():
    assert parse_date("1995-1-1") == "1995-01-01"
    assert parse_date("1995/07/01") == "1995-07-01"
    assert parse_date("1995-07-01T00:00:00") == "1995-07-01"
    assert parse_date("July 1, 1995") == "1995-07-01"
    assert parse_date("1 Jul 1995") == "1995-07-01"
    assert parse_date("1995-13-01") is None
    assert parse_date("Q3 1995") is None
    assert parse_date(19950701) is None


async def test_wrong_query(mini_db, tmp_path):
    result, _ = await score(mini_db, tmp_path, TOP, run_query("late_shipments", GOOD))
    assert result.category == "wrong_query"


async def test_wrong_params(mini_db, tmp_path):
    params = {**GOOD, "region": "ASIA"}
    result, _ = await score(mini_db, tmp_path, TOP, run_query("top_customers", params))
    assert result.category == "wrong_params"
    assert result.server_ok is True  # the call ran; it just answered a different question


async def test_missing_pinned_param_is_wrong_unless_default(mini_db, tmp_path):
    missing = {"region": "EUROPE", "start_date": "1996-01-01"}
    result, _ = await score(mini_db, tmp_path, TOP, run_query("top_customers", missing))
    assert result.category == "wrong_params"

    pinned_default = Task("p", "q", "top_customers", {**GOOD, "min_orders": 1})
    result, _ = await score(mini_db, tmp_path, pinned_default, run_query("top_customers", GOOD))
    assert result.correct


async def test_extra_param_only_allowed_at_its_default(mini_db, tmp_path):
    result, _ = await score(
        mini_db, tmp_path, TOP, run_query("top_customers", {**GOOD, "min_orders": 1})
    )
    assert result.correct
    result, _ = await score(
        mini_db, tmp_path, TOP, run_query("top_customers", {**GOOD, "min_orders": 3})
    )
    assert result.category == "wrong_params"
    result, _ = await score(
        mini_db, tmp_path, TOP, run_query("top_customers", {**GOOD, "country": "FRANCE"})
    )
    assert result.category == "wrong_params"


async def test_flattened_params_are_wrong(mini_db, tmp_path):
    result, _ = await score(
        mini_db, tmp_path, TOP, call("run_query", {"name": "top_customers", **GOOD})
    )
    assert result.category == "wrong_params"
    assert result.server_ok is False


async def test_no_tool_call(mini_db, tmp_path):
    reply = {"message": {"role": "assistant", "content": "The top customers are ..."}}
    result, _ = await score(mini_db, tmp_path, TOP, reply)
    assert result.category == "no_tool_call"
    assert result.server_ok is None


async def test_unknown_tool_is_wrong_tool(mini_db, tmp_path):
    result, _ = await score(mini_db, tmp_path, TOP, call("run_sql", {"sql": "select 1"}))
    assert result.category == "wrong_tool"


async def test_describe_query_loop(mini_db, tmp_path):
    result, model = await score(
        mini_db,
        tmp_path,
        TOP,
        call("describe_query", {"name": "top_customers"}),
        run_query("top_customers", GOOD),
    )
    assert result.correct
    assert len(result.turns) == 2
    follow_up = model.requests[1]["messages"]
    assert follow_up[-1]["role"] == "tool"
    assert follow_up[-1]["tool_name"] == "describe_query"
    assert "min_orders" in follow_up[-1]["content"]
    assert follow_up[-2]["role"] == "assistant"


async def test_lookup_loop_is_bounded(mini_db, tmp_path):
    lookups = [call("list_queries", {}) for _ in range(MAX_TURNS + 2)]
    result, model = await score(mini_db, tmp_path, TOP, *lookups)
    assert len(model.requests) == MAX_TURNS == 3
    assert len(result.turns) == 3
    assert result.category == "wrong_tool"
    assert not result.correct


async def test_replay_hits_and_misses(mini_db, tmp_path):
    path = tmp_path / "run.jsonl"
    live = FakeModel(run_query("top_customers", GOOD))
    first = await run_eval(mini_db, [TOP], model="fake", cache=ModelCache(path), transport=live)
    assert first.k == 1 and len(live.requests) == 1

    # Replay: no transport at all, same result, same recorded timing.
    replayed = await run_eval(
        mini_db, [TOP], model="fake", cache=ModelCache(path, replay=True), transport=None
    )
    assert replayed.k == 1
    assert all(t.cached for t in replayed.results[0].turns)
    assert replayed.wall_s == first.wall_s

    # Non-replay with a warm cache also makes no model call.
    quiet = FakeModel()
    await run_eval(mini_db, [TOP], model="fake", cache=ModelCache(path), transport=quiet)
    assert quiet.requests == []

    # A changed question is a miss, and replay mode refuses to call anything.
    changed = Task("t1", "A different question", TOP.name, TOP.params)
    with pytest.raises(ReplayMiss):
        await run_eval(
            mini_db,
            [changed],
            model="fake",
            cache=ModelCache(path, replay=True),
            transport=live,
        )
    assert len(live.requests) == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


async def test_report_totals_and_markdown(mini_db, tmp_path):
    other = Task(
        "t2",
        "Late shipments in 1995",
        "late_shipments",
        {"start_date": "1995-01-01", "end_date": "1996-01-01"},
    )
    model = FakeModel(
        run_query("top_customers", GOOD),
        run_query("top_customers", GOOD),  # wrong query for t2
    )
    report = await run_eval(
        mini_db, [TOP, other], model="fake", cache=ModelCache(tmp_path / "r.jsonl"), transport=model
    )
    assert (report.k, report.n) == (1, 2)
    assert report.categories["wrong_query"] == 1
    assert report.categories["ok"] == 1
    body = report.to_dict()
    assert body["wilson95"][0] < 0.5 < body["wilson95"][1]
    text = render_markdown(report)
    assert "1/2 correct" in text and "wrong_query" in text


def test_wilson_known_values():
    low, high = wilson(15, 20)
    assert low == pytest.approx(0.531, abs=0.001)
    assert high == pytest.approx(0.888, abs=0.001)
    assert wilson(0, 20)[0] == 0.0
    assert wilson(20, 20)[1] == 1.0
    assert wilson(0, 0) == (0.0, 1.0)


# -- the frozen task file ------------------------------------------------------------


def test_task_file_shape():
    tasks = load_tasks(TASKS_FILE)
    assert len(tasks) == 20
    assert len({t.id for t in tasks}) == 20
    assert Counter(t.name for t in tasks) == Counter(
        {
            "daily_order_volume": 5,
            "late_shipments": 5,
            "revenue_by_dimension": 5,
            "top_customers": 5,
        }
    )


def test_reference_calls_are_valid_and_run(gateway):
    catalog = load_all()
    for task in load_tasks(TASKS_FILE):
        query = catalog[task.name]
        query.bind(task.params)  # raises on an unknown, mistyped or missing parameter
        for key in task.params:
            known = {p.name for p in query.params} | set(query.identifiers)
            assert key in known, (task.id, key)
        for param in query.params:
            if param.required and param.default is None:
                assert param.name in task.params, (task.id, param.name)
        assert gateway.run_query(task.name, task.params)["columns"], task.id


def test_task_texts_do_not_leak_parameter_names():
    leaked = ("start_date", "end_date", "min_orders", "dimension", "o_orderdate", "l_shipmode")
    for task in load_tasks(TASKS_FILE):
        assert not any(word in task.task for word in leaked), task.id
        assert json.loads(json.dumps(task.params)) == task.params
