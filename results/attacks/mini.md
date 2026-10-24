## Attack suite

- Warehouse: `mini.duckdb`; transports: memory, stdio
- Payloads: 102 (204 results across transports)
- Agreement with the bare service: 160/172 (93.0%) over payloads that have a direct equivalent
- Bypasses: 0 (write 0, over-cap 0, looser 0, accepted expect-reject 0)
- Echoes: 0 (unfiltered by the server's own vocabulary: 104)
- Tool rejections with a valid code: 152/152
- Warehouse unchanged: yes (file sha256 31a39808863d)
- Verdict: PASS

| kind | payloads | MCP rejected | MCP ok |
|---|---:|---:|---:|
| stacked | 5 | 5 | 0 |
| non_read | 20 | 20 | 0 |
| identifier_injection | 8 | 8 | 0 |
| enum_abuse | 6 | 6 | 0 |
| type_abuse | 12 | 12 | 0 |
| limit_abuse | 11 | 7 | 4 |
| unknown_query | 8 | 8 | 0 |
| unknown_tool | 3 | 3 | 0 |
| unknown_argument | 7 | 7 | 0 |
| resource_uri | 4 | 4 | 0 |
| prompt_injection | 6 | 6 | 0 |
| benign | 12 | 0 | 12 |

### Divergences

| id | transport | direct | MCP | class | reason |
|---|---|---|---|---|---|
| lm-04 | memory | ok | reject | stricter_by_design | the declared type is integer, so a string is rejected; the bare service calls int() on it |
| lm-05 | memory | ok | reject | stricter_by_design | bool is an int in Python, so the bare service reads True as 1; the tool rejects it |
| lm-06 | memory | ok | reject | stricter_by_design | the declared type is integer; the bare service truncates 2.5 to 2 with int() |
| ua-01 | memory | ok | reject | stricter_by_design | unknown top-level arguments are rejected; the SDK would drop them silently |
| ua-03 | memory | ok | reject | stricter_by_design | unknown top-level arguments are rejected; the SDK would drop them silently |
| ps-02 | memory | ok | reject | stricter_by_design | params keys that name the service's own keyword arguments are rejected; the bare service would take use_cache=False as its own switch |
| lm-04 | stdio | ok | reject | stricter_by_design | the declared type is integer, so a string is rejected; the bare service calls int() on it |
| lm-05 | stdio | ok | reject | stricter_by_design | bool is an int in Python, so the bare service reads True as 1; the tool rejects it |
| lm-06 | stdio | ok | reject | stricter_by_design | the declared type is integer; the bare service truncates 2.5 to 2 with int() |
| ua-01 | stdio | ok | reject | stricter_by_design | unknown top-level arguments are rejected; the SDK would drop them silently |
| ua-03 | stdio | ok | reject | stricter_by_design | unknown top-level arguments are rejected; the SDK would drop them silently |
| ps-02 | stdio | ok | reject | stricter_by_design | params keys that name the service's own keyword arguments are rejected; the bare service would take use_cache=False as its own switch |

