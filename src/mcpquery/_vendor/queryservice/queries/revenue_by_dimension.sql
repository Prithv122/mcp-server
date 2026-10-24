/* {
  "description": "Net revenue and order count in a date window, grouped by one allowlisted dimension.",
  "params": {
    "start_date": {"type": "date", "description": "Inclusive lower bound on o_orderdate"},
    "end_date":   {"type": "date", "description": "Exclusive upper bound on o_orderdate"}
  },
  "identifiers": {
    "dimension": ["n_name", "r_name", "c_mktsegment", "o_orderpriority", "l_shipmode"]
  }
} */
SELECT
    {dimension}                                                     AS dimension,
    round(sum(l.l_extendedprice * (1 - l.l_discount)), 2)           AS net_revenue,
    count(DISTINCT o.o_orderkey)                                    AS orders,
    round(avg(l.l_discount), 4)                                     AS avg_discount
FROM lineitem l
JOIN orders   o ON o.o_orderkey  = l.l_orderkey
JOIN customer c ON c.c_custkey   = o.o_custkey
JOIN nation   n ON n.n_nationkey = c.c_nationkey
JOIN region   r ON r.r_regionkey = n.n_regionkey
WHERE o.o_orderdate >= $start_date
  AND o.o_orderdate <  $end_date
GROUP BY 1
ORDER BY net_revenue DESC
