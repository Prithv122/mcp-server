/* {
  "description": "Share of line items received after their committed date, by order priority.",
  "params": {
    "start_date": {"type": "date"},
    "end_date":   {"type": "date"}
  }
} */
SELECT
    o.o_orderpriority                                            AS priority,
    count(*)                                                     AS line_items,
    sum(CASE WHEN l.l_receiptdate > l.l_commitdate THEN 1 END)   AS late_line_items,
    round(
        sum(CASE WHEN l.l_receiptdate > l.l_commitdate THEN 1 ELSE 0 END) * 1.0 / count(*),
        4
    )                                                            AS late_share,
    round(
        avg(CASE WHEN l.l_receiptdate > l.l_commitdate
                 THEN date_diff('day', l.l_commitdate, l.l_receiptdate) END),
        2
    )                                                            AS avg_days_late
FROM lineitem l
JOIN orders o ON o.o_orderkey = l.l_orderkey
WHERE o.o_orderdate >= $start_date
  AND o.o_orderdate <  $end_date
GROUP BY 1
ORDER BY late_share DESC
