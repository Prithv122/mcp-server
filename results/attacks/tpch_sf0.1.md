## Attack suite

- Warehouse: `warehouse.duckdb`; transports: memory, stdio
- Payloads: 111 (221 results across transports)
- Agreement with the bare service: 175/189 (92.6%) over payloads that have a direct equivalent
- Bypasses: 0 (write 0, over-cap 0, over-size 0, looser 0, accepted expect-reject 0)
- Client errors: 0; unverifiable results: 0
- Echoes: 0 (unfiltered by the server's own vocabulary: 106)
- Tool rejections with a valid code: 165/165
- Prompt rejections without a code: 4/12 (not gated: the code rule covers tool calls)
- Query time limit: 2.0 s on both sides
- Not sent (cannot be encoded on that transport): ['ex-07/stdio']
- Warehouse unchanged: yes (file sha256 27d04a247746)
- Verdict: PASS

| kind | payloads | MCP rejected | MCP ok |
|---|---:|---:|---:|
| stacked | 6 | 6 | 0 |
| non_read | 20 | 20 | 0 |
| identifier_injection | 8 | 8 | 0 |
| enum_abuse | 6 | 6 | 0 |
| type_abuse | 14 | 14 | 0 |
| limit_abuse | 11 | 7 | 4 |
| exhaustion | 5 | 4 | 1 |
| disclosure | 1 | 0 | 1 |
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
| ex-08 | memory | ok | reject | stricter_by_design | an empty date window is rejected; the bare service returns an empty result |
| lm-04 | stdio | ok | reject | stricter_by_design | the declared type is integer, so a string is rejected; the bare service calls int() on it |
| lm-05 | stdio | ok | reject | stricter_by_design | bool is an int in Python, so the bare service reads True as 1; the tool rejects it |
| lm-06 | stdio | ok | reject | stricter_by_design | the declared type is integer; the bare service truncates 2.5 to 2 with int() |
| ua-01 | stdio | ok | reject | stricter_by_design | unknown top-level arguments are rejected; the SDK would drop them silently |
| ua-03 | stdio | ok | reject | stricter_by_design | unknown top-level arguments are rejected; the SDK would drop them silently |
| ps-02 | stdio | ok | reject | stricter_by_design | params keys that name the service's own keyword arguments are rejected; the bare service would take use_cache=False as its own switch |
| ex-08 | stdio | ok | reject | stricter_by_design | an empty date window is rejected; the bare service returns an empty result |

