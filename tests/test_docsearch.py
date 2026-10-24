"""Document search added to the server: a fake searcher, no retrieval code needed."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from mcp import Client

from mcpquery.docsearch import (
    MAX_QUERY_CHARS,
    Doc,
    Hit,
    RetrieverAdapter,
    register_docs,
)
from mcpquery.server import GuardedServer

CORPUS = {
    f"d{i}": Doc(f"d{i}", f"Title {i}", f"body {i} about {topic}", url=f"https://docs.test/{i}")
    for i, topic in enumerate(["incremental models", "snapshots", "seeds", "tests", "docs", "jobs"])
}


class FakeSearcher:
    def __init__(self) -> None:
        self.last_k: int | None = None
        self.gets: list[str] = []

    def search(self, query: str, k: int) -> list[Hit]:
        self.last_k = k
        words = query.lower().split()
        scored = [
            (sum(w in doc.text for w in words), doc) for doc in CORPUS.values()
        ]  # fmt: skip
        scored.sort(key=lambda pair: (-pair[0], pair[1].doc_id))
        return [Hit(d.doc_id, float(s), d.title, d.text[:20], d.url) for s, d in scored[:k]]

    def get(self, doc_id: str) -> Doc | None:
        self.gets.append(doc_id)
        return CORPUS.get(doc_id)


def build(searcher: FakeSearcher, *, register: bool = True) -> GuardedServer:
    server = GuardedServer("docs-test")
    if register:
        register_docs(server, searcher)
    return server


def error_of(result) -> dict:
    assert result.is_error
    return json.loads(result.content[0].text)


async def test_tool_and_resource_appear_only_after_register():
    async with Client(build(FakeSearcher(), register=False)) as client:
        assert (await client.list_tools()).tools == []
        assert (await client.list_resource_templates()).resource_templates == []
    async with Client(build(FakeSearcher())) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        templates = (await client.list_resource_templates()).resource_templates
    assert set(tools) == {"search_docs"}
    assert tools["search_docs"].annotations.read_only_hint is True
    assert [t.uri_template for t in templates] == ["docs://{doc_id}"]


async def test_search_returns_ranked_hits():
    async with Client(build(FakeSearcher())) as client:
        result = await client.call_tool("search_docs", {"query": "snapshots", "k": 3})
    body = result.structured_content
    assert not result.is_error
    assert body["k"] == 3
    assert body["hits"][0]["doc_id"] == "d1"
    assert len(body["hits"]) == 3
    scores = [h["score"] for h in body["hits"]]
    assert scores == sorted(scores, reverse=True)
    assert {"doc_id", "score", "title", "snippet", "url"} <= set(body["hits"][0])


@pytest.mark.parametrize(("asked", "used"), [(0, 1), (-4, 1), (1000, 20), (7, 7)])
async def test_k_is_clamped(asked, used):
    searcher = FakeSearcher()
    async with Client(build(searcher)) as client:
        result = await client.call_tool("search_docs", {"query": "tests", "k": asked})
    assert not result.is_error
    assert searcher.last_k == used
    assert result.structured_content["k"] == used


async def test_default_k():
    searcher = FakeSearcher()
    async with Client(build(searcher)) as client:
        await client.call_tool("search_docs", {"query": "tests"})
    assert searcher.last_k == 5


@pytest.mark.parametrize("query", ["x" * (MAX_QUERY_CHARS + 1), "   "])
async def test_bad_query_is_rejected_without_echo(query):
    async with Client(build(FakeSearcher())) as client:
        result = await client.call_tool("search_docs", {"query": query})
    text = result.content[0].text
    assert error_of(result)["code"] == "BAD_PARAM"
    assert "xxxx" not in text


async def test_query_at_the_cap_is_accepted():
    async with Client(build(FakeSearcher())) as client:
        result = await client.call_tool("search_docs", {"query": "a" * MAX_QUERY_CHARS})
    assert not result.is_error


async def test_unknown_argument_is_rejected():
    async with Client(build(FakeSearcher())) as client:
        result = await client.call_tool("search_docs", {"query": "tests", "arm": "bm25"})
    assert error_of(result)["code"] == "BAD_PARAM"


async def test_resource_read():
    async with Client(build(FakeSearcher())) as client:
        contents = (await client.read_resource("docs://d2")).contents
    doc = json.loads(contents[0].text)
    assert doc["doc_id"] == "d2"
    assert doc["text"].startswith("body 2")


@pytest.mark.parametrize("doc_id", ["missing", "a" * 500, "zz'; DROP TABLE x; --"])
async def test_unknown_ids_are_rejected_without_echo(doc_id):
    async with Client(build(FakeSearcher())) as client:
        with pytest.raises(Exception) as caught:
            await client.read_resource(f"docs://{doc_id}")
    assert "BAD_PARAM" in str(caught.value)
    assert doc_id not in str(caught.value)


@pytest.mark.parametrize(
    "uri",
    [
        "docs://../../etc/passwd",
        "docs://..%2F..%2Fetc%2Fpasswd",
        "docs://%2Fetc%2Fpasswd",
        "docs://sub/d1",
        "file:///etc/passwd",
    ],
)
async def test_path_like_ids_never_reach_the_searcher(uri):
    searcher = FakeSearcher()
    async with Client(build(searcher)) as client:
        with pytest.raises(Exception):  # noqa: B017 - the SDK's protocol error type
            await client.read_resource(uri)
    # Either the URI template does not match, or the id was refused; no id with a path
    # separator is ever looked up. (The SDK's own "Unknown resource" text does quote the URI.)
    assert not [i for i in searcher.gets if "/" in i or ".." in i]


# -- the adapter, against an object shaped like the #24 Retriever ---------------------------


@dataclass(frozen=True)
class FakeChunk:
    chunk_id: str
    title: str
    url: str
    text: str
    breadcrumb: str = ""


@dataclass
class FakeResult:
    ranked: list[tuple[str, float]]


class FakeRetriever:
    def __init__(self) -> None:
        self.chunks = {
            "c1": FakeChunk("c1", "dbt", "https://x.test/1", "line one\n\n  line   two", "dbt > A"),
            "c2": FakeChunk("c2", "airflow", "https://x.test/2", "other text"),
        }
        self.calls: list[tuple[str, str, int]] = []

    def retrieve(self, query: str, *, arm: str = "hybrid", k: int = 10, **_: object):
        self.calls.append((query, arm, k))
        return FakeResult(ranked=[("c1", 0.9), ("c2", 0.5)])

    def chunk(self, chunk_id: str) -> FakeChunk:
        return self.chunks[chunk_id]


def test_adapter_maps_retriever_shapes():
    retriever = FakeRetriever()
    adapter = RetrieverAdapter(retriever, arm="bm25", snippet_chars=12)
    hits = adapter.search("models", 1)
    assert retriever.calls == [("models", "bm25", 1)]
    assert [h.doc_id for h in hits] == ["c1"]
    assert hits[0].title == "dbt > A"  # breadcrumb preferred when the chunk has one
    assert hits[0].snippet == "line one lin"  # whitespace collapsed, then cut
    doc = adapter.get("c2")
    assert doc is not None
    assert (doc.title, doc.text) == ("airflow", "other text")
    assert adapter.get("nope") is None


async def test_adapter_end_to_end():
    server = GuardedServer("docs-test")
    register_docs(server, RetrieverAdapter(FakeRetriever()))
    async with Client(server) as client:
        hits = (await client.call_tool("search_docs", {"query": "models"})).structured_content
        doc = await client.read_resource(f"docs://{hits['hits'][1]['doc_id']}")
    assert json.loads(doc.contents[0].text)["title"] == "airflow"
