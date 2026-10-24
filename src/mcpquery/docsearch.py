"""Optional document search: the same guarded-tool pattern over a text retriever.

The warehouse tools answer questions about numbers. This module shows the pattern is not
specific to SQL: a second, independent capability (search over a document corpus) is added
to the same server by one function, ``register_docs``, with the same rules:

* read-only annotations, so a client can auto-approve the call;
* every argument validated before it reaches the searcher, errors carrying a stable code
  and never the caller's text;
* resource ids checked against the searcher's own data, never turned into a path.

The server does not import any retrieval code. It depends on the small ``DocSearcher``
protocol below, and ``RetrieverAdapter`` fits the hybrid retriever from the sibling
``24-production-rag`` repo to it by duck typing, so nothing is installed for it and the
tests run on a fake. The shapes it relies on (``production_rag.pipeline``)::

    Retriever.retrieve(query, *, arm="hybrid", k=10, ...) -> RetrievalResult
    RetrievalResult.ranked: list[tuple[chunk_id, score]]      # best first
    Retriever.chunk(chunk_id) -> Chunk                         # KeyError when unknown
    Chunk: chunk_id, doc_id, title, url, heading_path, text    # frozen dataclass
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from . import errors
from .errors import QueryError
from .server import READ_ONLY

MAX_QUERY_CHARS = 500
DEFAULT_K = 5
MIN_K, MAX_K = 1, 20
MAX_ID_CHARS = 200
SNIPPET_CHARS = 240


@dataclass(frozen=True)
class Hit:
    """One search result: enough to decide whether to read the full document."""

    doc_id: str
    score: float
    title: str
    snippet: str
    url: str = ""


@dataclass(frozen=True)
class Doc:
    doc_id: str
    title: str
    text: str
    url: str = ""


class DocSearcher(Protocol):
    def search(self, query: str, k: int) -> list[Hit]: ...

    def get(self, doc_id: str) -> Doc | None: ...


class RetrieverAdapter:
    """Fits an object shaped like ``production_rag.pipeline.Retriever`` to ``DocSearcher``.

    Each retrievable chunk is exposed as one document, keyed by its ``chunk_id``: that is
    the unit the retriever ranks, so a hit's id can always be read back.
    """

    def __init__(self, retriever: Any, *, arm: str = "hybrid", snippet_chars: int = SNIPPET_CHARS):
        self._retriever = retriever
        self._arm = arm
        self._snippet_chars = snippet_chars

    def search(self, query: str, k: int) -> list[Hit]:
        result = self._retriever.retrieve(query, arm=self._arm, k=k)
        return [self._hit(chunk_id, score) for chunk_id, score in result.ranked[:k]]

    def get(self, doc_id: str) -> Doc | None:
        try:
            chunk = self._retriever.chunk(doc_id)
        except KeyError:
            return None
        return Doc(doc_id=doc_id, title=_title(chunk), text=chunk.text, url=_url(chunk))

    def _hit(self, chunk_id: str, score: float) -> Hit:
        chunk = self._retriever.chunk(chunk_id)
        text = " ".join(chunk.text.split())
        return Hit(
            doc_id=chunk_id,
            score=round(float(score), 6),
            title=_title(chunk),
            snippet=text[: self._snippet_chars],
            url=_url(chunk),
        )


def _title(chunk: Any) -> str:
    return str(getattr(chunk, "breadcrumb", None) or getattr(chunk, "title", ""))


def _url(chunk: Any) -> str:
    return str(getattr(chunk, "url", ""))


def _raise(error: QueryError) -> None:
    """Same convention as ``server.py``: ``GuardedServer`` maps this back to a coded result."""
    raise ToolError(error.to_json()) from error


def register_docs(server: MCPServer, searcher: DocSearcher) -> None:
    """Add ``search_docs`` and the ``docs://{doc_id}`` resource to ``server``."""
    # A retriever holds numpy matrices and may not tolerate concurrent calls, and the SDK
    # runs sync handlers on worker threads, so calls are serialised like the DuckDB ones.
    lock = threading.Lock()

    @server.tool(annotations=READ_ONLY)
    def search_docs(query: str, k: int = DEFAULT_K) -> dict[str, Any]:
        """Search the document collection. Returns ranked hits with an id, score and snippet;
        read a whole document with the docs://{doc_id} resource."""
        if not isinstance(query, str) or not query.strip():
            _raise(errors.bad_param("query must be a non-empty string."))
        if len(query) > MAX_QUERY_CHARS:
            _raise(errors.bad_param(f"query is limited to {MAX_QUERY_CHARS} characters."))
        k = min(max(k, MIN_K), MAX_K)
        with lock:
            hits = searcher.search(query, k)
        return {"hits": [asdict(hit) for hit in hits[:k]], "k": k}

    @server.resource("docs://{doc_id}", name="doc", mime_type="application/json")
    def doc(doc_id: str) -> str:
        """One document by the id search_docs returned."""
        found = None
        if isinstance(doc_id, str) and 0 < len(doc_id) <= MAX_ID_CHARS:
            with lock:
                found = searcher.get(doc_id)
        if found is None:
            # The id is never echoed: the message is the same for any miss.
            raise ResourceError("No document with that id. Use search_docs to find ids.")
        return json.dumps(asdict(found), indent=2)
