/* {
  "description": "Highest-revenue customers in a region and date window. Row cap comes from the guard layer, not the SQL.",
  "params": {
    "region":     {"type": "enum", "values": ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]},
    "start_date": {"type": "date"},
    "end_date":   {"type": "date"},
    "min_orders": {"type": "int", "required": false, "default": 1, "minimum": 1, "maximum": 1000}
  }
} */
SELECT
    c.c_custkey,
    c.c_name,
    n.n_name                                                AS nation,
    c.c_mktsegment                                          AS segment,
    round(sum(l.l_extendedprice * (1 - l.l_discount)), 2)   AS net_revenue,
    count(DISTINCT o.o_orderkey)                            AS orders
FROM lineitem l
JOIN orders   o ON o.o_orderkey  = l.l_orderkey
JOIN customer c ON c.c_custkey   = o.o_custkey
JOIN nation   n ON n.n_nationkey = c.c_nationkey
JOIN region   r ON r.r_regionkey = n.n_regionkey
WHERE r.r_name       = $region
  AND o.o_orderdate >= $start_date
  AND o.o_orderdate <  $end_date
GROUP BY 1, 2, 3, 4
HAVING count(DISTINCT o.o_orderkey) >= $min_orders
ORDER BY net_revenue DESC
