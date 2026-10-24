## Attack suite

- Warehouse: `mini.duckdb`; transports: memory, stdio
- Payloads: 94 (188 results across transports)
- Agreement with the bare service: 150/156 (96.2%) over payloads that have a direct equivalent
- Bypasses: 10 (write 0, over-cap 0, looser 0, accepted expect-reject 10)
- Echoes: 0 (unfiltered by the server's own vocabulary: 86)
- Tool rejections with a valid code: 126/126
- Warehouse unchanged: yes (file sha256 31a39808863d)
- Verdict: FAIL

| kind | payloads | MCP rejected | MCP ok |
|---|---:|---:|---:|
| stacked | 5 | 5 | 0 |
| non_read | 15 | 12 | 3 |
| identifier_injection | 8 | 8 | 0 |
| enum_abuse | 6 | 6 | 0 |
| type_abuse | 12 | 12 | 0 |
| limit_abuse | 11 | 5 | 6 |
| unknown_query | 8 | 8 | 0 |
| unknown_tool | 3 | 3 | 0 |
| unknown_argument | 4 | 4 | 0 |
| resource_uri | 4 | 4 | 0 |
| prompt_injection | 6 | 6 | 0 |
| benign | 12 | 0 | 12 |

Tagged stricter_by_design but not diverging: ['lm-04/memory', 'lm-05/memory', 'lm-04/stdio', 'lm-05/stdio']

### Divergences

| id | transport | direct | MCP | class | reason |
|---|---|---|---|---|---|
| lm-06 | memory | ok | reject | stricter_by_design | the declared type is integer; the bare service truncates 2.5 to 2 with int() |
| ua-01 | memory | ok | reject | stricter_by_design | unknown top-level arguments are rejected; the SDK would drop them silently |
| ua-03 | memory | ok | reject | stricter_by_design | unknown top-level arguments are rejected; the SDK would drop them silently |
| lm-06 | stdio | ok | reject | stricter_by_design | the declared type is integer; the bare service truncates 2.5 to 2 with int() |
| ua-01 | stdio | ok | reject | stricter_by_design | unknown top-level arguments are rejected; the SDK would drop them silently |
| ua-03 | stdio | ok | reject | stricter_by_design | unknown top-level arguments are rejected; the SDK would drop them silently |

### Findings

| id | transport | what | detail |
|---|---|---|---|
| fs-01 | memory | expected reject, MCP ok |  |
| fs-02 | memory | expected reject, MCP ok |  |
| fs-03 | memory | expected reject, MCP ok |  |
| lm-04 | memory | expected reject, MCP ok |  |
| lm-05 | memory | expected reject, MCP ok |  |
| fs-01 | stdio | expected reject, MCP ok |  |
| fs-02 | stdio | expected reject, MCP ok |  |
| fs-03 | stdio | expected reject, MCP ok |  |
| lm-04 | stdio | expected reject, MCP ok |  |
| lm-05 | stdio | expected reject, MCP ok |  |

FAIL: 10 bypass(es): {'write': 0, 'over_cap': 0, 'looser': 0, 'accepted_expect_reject': 10, 'total': 10}
