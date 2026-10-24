/* {
  "description": "Daily order count and value, optionally restricted to one market segment.",
  "params": {
    "start_date": {"type": "date"},
    "end_date":   {"type": "date"},
    "segment":    {"type": "enum", "required": false, "default": "ALL",
                   "values": ["ALL", "AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]}
  }
} */
SELECT
    o.o_orderdate                     AS order_date,
    count(*)                          AS orders,
    round(sum(o.o_totalprice), 2)     AS gross_value,
    round(avg(o.o_totalprice), 2)     AS avg_order_value
FROM orders o
JOIN customer c ON c.c_custkey = o.o_custkey
WHERE o.o_orderdate >= $start_date
  AND o.o_orderdate <  $end_date
  AND ($segment = 'ALL' OR c.c_mktsegment = $segment)
GROUP BY 1
ORDER BY order_date
