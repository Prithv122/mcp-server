# Tool selection: qwen2.5:7b-instruct-q4_K_M

**11/20 correct** (Wilson 95% interval 0.342 to 0.742).
Mean turns per task 2.00; model wall time 828.3 s.

| Category | Tasks |
|---|---|
| ok | 11 |
| no_tool_call | 0 |
| wrong_tool | 0 |
| wrong_query | 0 |
| wrong_params | 8 |
| server_rejected | 1 |

| Task | Expected | Result | Turns | Server | Detail |
|---|---|---|---|---|---|
| dov-1 | daily_order_volume | ok | 2 | ok |  |
| dov-2 | daily_order_volume | ok | 2 | ok |  |
| dov-3 | daily_order_volume | ok | 2 | ok |  |
| dov-4 | daily_order_volume | wrong_params | 2 | ok | end_date: got '1993-01-31', expected '1993-02-01' |
| dov-5 | daily_order_volume | ok | 2 | ok |  |
| late-1 | late_shipments | ok | 2 | ok |  |
| late-2 | late_shipments | ok | 2 | ok |  |
| late-3 | late_shipments | server_rejected | 2 | rejected | {"code": "INTERNAL", "message": "Internal error. The server log has the details."} |
| late-4 | late_shipments | wrong_params | 3 | rejected | unknown parameter 'o_orderpriority' |
| late-5 | late_shipments | wrong_params | 2 | ok | end_date: got '1994-12-31', expected '1995-01-01' |
| rev-1 | revenue_by_dimension | ok | 2 | ok |  |
| rev-2 | revenue_by_dimension | ok | 2 | ok |  |
| rev-3 | revenue_by_dimension | wrong_params | 2 | ok | end_date: got '1994-12-31', expected '1995-01-01' |
| rev-4 | revenue_by_dimension | wrong_params | 2 | ok | end_date: got '1996-11-30', expected '1996-12-01' |
| rev-5 | revenue_by_dimension | ok | 2 | ok |  |
| top-1 | top_customers | wrong_params | 2 | ok | end_date: got '1996-12-31', expected '1997-01-01' |
| top-2 | top_customers | ok | 2 | ok |  |
| top-3 | top_customers | wrong_params | 1 | ok | end_date: got '1995-12-31', expected '1996-01-01' |
| top-4 | top_customers | ok | 2 | ok |  |
| top-5 | top_customers | wrong_params | 2 | ok | end_date: got '1993-12-31', expected '1994-01-01' |

