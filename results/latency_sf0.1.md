| tool | baseline cold | baseline cached | memory cold | memory cached | stdio cold | stdio cached |
|---|---:|---:|---:|---:|---:|---:|
| list_queries | 0.00 / 0.00 | 0.00 / 0.00 | 1.75 / 2.23 | 1.82 / 2.38 | 5.09 / 7.32 | 5.33 / 5.94 |
| describe_query | 0.00 / 0.00 | 0.00 / 0.00 | 1.83 / 2.25 | 1.99 / 2.27 | 4.59 / 5.49 | 4.91 / 5.72 |
| run_query:daily_order_volume | 11.79 / 15.20 | 5.05 / 14.86 | 21.96 / 23.63 | 17.39 / 29.17 | 30.56 / 34.17 | 22.02 / 31.56 |
| run_query:late_shipments | 41.53 / 48.48 | 5.40 / 8.16 | 34.55 / 44.69 | 8.72 / 12.56 | 49.43 / 58.31 | 11.80 / 14.80 |
| run_query:revenue_by_dimension | 58.39 / 75.43 | 5.33 / 11.49 | 48.85 / 67.94 | 9.16 / 11.99 | 64.80 / 99.97 | 11.80 / 13.72 |
| run_query:top_customers | 108.51 / 149.47 | 6.53 / 33.06 | 146.14 / 177.05 | 57.20 / 72.63 | 165.11 / 190.32 | 56.37 / 69.59 |
| run_sql | 18.18 / 34.63 | 18.74 / 30.27 | 21.07 / 29.24 | 23.39 / 31.88 | 25.04 / 33.27 | 30.98 / 40.55 |

p50 / p95 in ms, 30 timed calls per cell after one warm-up. `list_queries`, `describe_query` and `run_sql` never use the cache, so their cold and cached columns measure the same thing twice.
stdio server startup (spawn to initialized client): cold 9.9 s, cached 7.3 s.
Not a quiet machine: other work was running during the measurement, so absolute numbers are informative only. Compare columns within a row, not across runs.
wrote results\latency_sf0.1.json
