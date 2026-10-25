# Resume Bullets — MCP server over guarded warehouse queries

Form: **action → technical specifics → measured outcome.**

---

**AI Engineer (primary)**

- Built a Model Context Protocol server (Python, MCP SDK 2.x, stdio) exposing an allowlisted,
  read-only TPC-H query service to AI assistants; a pre-registered differential attack suite of 111
  payloads found a file-read hole in the original guards (`SELECT * FROM read_text(...)`), fixed
  upstream, then passed with 0 bypasses and 0 echoed inputs across in-process and stdio transports.
- Measured a local 7B model's tool use on 20 frozen analyst tasks: 11/20 correct (95% CI 0.34-0.74),
  with 7 misses from inclusive end dates that the server accepted silently; profiled protocol
  overhead at ~3 ms per small result and ~51 ms per 1,000 rows, dominated by JSON serialisation.

**Data Engineer / Python Developer**

- Hardened a DuckDB query API for LLM clients: strict argument schemas, stable error codes with no
  caller input in error text, external file access disabled at the connection, and a per-query time
  limit that abandons and replaces a connection when DuckDB ignores the interrupt; verified with a
  differential test harness comparing protocol and direct verdicts on 111 attack payloads.

**Keywords:** Model Context Protocol (MCP), LLM tool use, prompt injection, SQL injection, DuckDB,
TPC-H, differential testing, pre-registration, Python 3.13, pytest, Ollama.
