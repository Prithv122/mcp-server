# Interview Prep — MCP server over guarded warehouse queries

**Five questions, five answers.** Draft answers from the build; rewrite each in your own words
before an interview.

---

### 1. "An MCP server that wraps four queries sounds thin. What's the actual engineering here?"

The wrapper is thin on purpose; the work is proving it did not weaken anything. The service it
exposes (#15) already had four guard layers for a human caller. A model is a different caller: it
sends JSON, it reads error text as instructions, and anything in its context can be injected. So I
built a differential attack suite: the same 102 payloads go to the MCP server and to the bare
service, and the pass rule, written before the suite, is zero writes, zero over-cap results, nothing
the server accepts that the service rejects, and no error that repeats caller input. The first run
failed, and that failure is the most useful thing in the repo.

### 2. "What did the first run find?"

Two things. First, a real hole in the original guards: `SELECT * FROM read_text('C:/Windows/win.ini')`
read the file. A table function is a single `SELECT`, so the parser check passes it, and a read-only
connection blocks writes, not reads of other files. Any file the process could read was readable
through ad-hoc SQL. I fixed it where it belonged, upstream in #15, by opening the connection with
DuckDB's external access disabled and its configuration locked, then re-vendored. Second, the SDK
coerces `limit: "5"` to 5 and `true` to 1 in lax mode, so a stricter check I had declared never ran.
Not a security hole, but the declared behaviour did not happen, so `limit` is now a strict integer.
The rerun passes on the fixture and on TPC-H, over both transports.

### 3. "Why not just pass the guard's error message to the client?"

Because the messages quote the input. #15's `UnsafeQuery` says `got 'X'` for a bad value, a DuckDB
binder error quotes the SQL line, the SDK's validation errors include `input_value=...`, and the SDK
repeats an unknown resource URI. To a terminal user that is helpful; to a model it is a channel for
injected text to come back as an authoritative-looking server message. Every error here is a stable
code plus a template filled with names the server already publishes. The cost is that a model
writing ad-hoc SQL gets "the warehouse rejected the query, read the schema resource" instead of the
column name it got wrong, which costs it a retry. I think that is the right trade.

### 4. "Your local model scored 11/20. What does that tell you?"

Mostly one thing: 7 of the 9 misses were an inclusive end date, `1995-12-31` for "1995", even though
the description says `end_date` is exclusive. The server accepted every one of those calls and
returned a plausible answer missing the last day. That is the most damaging failure in the project,
and no guard can catch it, because the call is valid. With n = 20 the interval is 0.34 to 0.74, so I
would not compare models with it. The fix is in the interface, not the guards: a `period` argument
the server expands, or echoing the resolved window back in every result so the mistake is visible.
The same eval also found a crash: a model put `limit` inside `params`, which collided with the
service's own keyword argument.

### 5. "What does the protocol cost, and what would you change at scale?"

On a cache hit the MCP layer adds about 3 ms to a 5-row answer and about 51 ms to a 1,000-row one:
the cost is serialising rows to JSON, twice (structured content plus a text copy), not the hop. The
stdio pipe adds a few ms; starting the server takes 7-10 s, so clients keep one process alive. The
machine was busy when I measured, so I report it as informative. At scale I would page results and
send rows once (or Arrow), move the read-only guarantee into a database role with grants on views
and a statement timeout, and switch to the HTTP transport with authentication and an audit log once
more than one user shares a server. And the attack suite runs on every DuckDB upgrade, because layer
3 depends on the parser's statement types.

---

**Pitch (30 seconds):** I exposed a guarded SQL service to AI assistants over the Model Context
Protocol and pre-registered a differential attack suite to prove the guards survived. Its first run
found that a single `SELECT` could read any local file through a table function, a hole in the
original service, which I fixed upstream. After the fixes, 102 payloads pass with zero bypasses on
both transports. A local 7B model picked the right query every time, but in 7 of 20 tasks it wrote
an inclusive end date and got a silently wrong answer, which is the next thing to design out.
