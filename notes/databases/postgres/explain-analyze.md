---
title: Reading EXPLAIN ANALYZE plans in Postgres
tags: [postgres, performance, query-planner, explain]
learned: 2026-10-27
---
## TL;DR
`EXPLAIN` prints the plan the planner chose with estimated costs; `EXPLAIN (ANALYZE, BUFFERS)` runs the query and adds actual rows, timing and page reads per node. Read it bottom-up and inside-out: find the node where **actual rows diverge from estimated rows**, the node with the biggest share of actual time, and any sort or hash that spilled to disk. The general theory of the planner, costs and statistics is in [[query-planning-and-execution]]; this note is about the Postgres output.

## Key points

### Options
- `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)` is the everyday form. `ANALYZE` executes the statement, so wrap a write in `BEGIN; ... ROLLBACK;`.
- `BUFFERS` shows `shared hit` (from `shared_buffers`), `read` (from OS cache or disk), `dirtied`, `written`, and `temp read/written` for spills. Postgres 17 adds `SERIALIZE` for output conversion cost and `MEMORY`.
- `VERBOSE` prints output columns per node (useful to see why an index-only scan was not chosen), `SETTINGS` shows non-default planner GUCs, `COSTS OFF` for pasting diffs.
- `auto_explain` logs plans of statements slower than `auto_explain.log_min_duration` in production; combine with `pg_stat_statements` to find what to look at ([[postgres-performance-checklist]]).

### Anatomy of one node
```
Index Scan using sim_msisdn_idx on sim  (cost=0.43..8.45 rows=1 width=120) (actual time=0.031..0.033 rows=1 loops=1)
  Index Cond: (msisdn = '996555123456'::text)
  Filter: (status = 'ACTIVE'::text)
  Rows Removed by Filter: 2
  Buffers: shared hit=4
```
- `cost=startup..total`: unitless estimates. `rows` is the estimate per loop; `width` bytes per row.
- `actual time=first..last` in ms per loop, `rows` per loop, `loops`: total work = per-loop value × loops. A nested loop inner side with `rows=1 loops=50000` is 50 000 index lookups.
- `Index Cond` is what the index answered; `Filter` is applied afterwards on fetched rows. A large `Rows Removed by Filter` means the index does not match the predicate well: add the column to the index or create a partial index ([[postgres-indexes]]).

### Scan nodes
| Node | Meaning | Good when |
|---|---|---|
| `Seq Scan` | read every heap page | small tables, predicates matching > ~10 % of rows |
| `Index Scan` | walk the index, fetch heap page per matching entry, in index order | high selectivity, or `ORDER BY` matches the index |
| `Index Only Scan` | answer from the index; `Heap Fetches: N` shows visibility-map misses | covering index and recently vacuumed table |
| `Bitmap Index Scan` + `Bitmap Heap Scan` | collect matching page ids into a bitmap, then read pages in physical order | medium selectivity, `OR` of several indexes; `Recheck Cond` and `lossy` appear when `work_mem` is tight |
| `Parallel Seq Scan` under `Gather` | several workers | large scans; check `Workers Launched` equals `Workers Planned` |

### Join nodes
- `Nested Loop`: for each outer row, run the inner plan. Fine when the outer side is small and the inner is an index scan; catastrophic when the planner estimated 10 outer rows and got 100 000.
- `Hash Join`: `Hash` builds a table from the smaller input (`Batches: 1` means it fit in `work_mem`; `Batches: 8` means it spilled). Probes with the other input.
- `Merge Join`: both inputs sorted; look for a `Sort` beneath it or an index providing order.
- Join order is read inside-out: the deepest nodes execute first.

### Other nodes worth recognizing
- `Sort` with `Sort Method: quicksort Memory: 25kB` vs `external merge Disk: 48000kB`: the second one needs more `work_mem` or an index that provides the order.
- `HashAggregate` / `GroupAggregate`, `Unique`, `Limit`, `Materialize` (caches an inner result for repeated nested-loop iterations), `CTE Scan` (CTEs with `MATERIALIZED` or referenced twice are not inlined), `SubPlan` (correlated subquery executed per row; often worth rewriting as a join), `InitPlan` (uncorrelated subquery executed once).
- `Incremental Sort` (PG 13+) when a prefix of the order is already sorted.

### A reading procedure
1. Compare total `actual time` with the query's wall time to confirm you are looking at the right statement.
2. Find the node with the largest **exclusive** time (its own time minus children). Tools such as the plan visualizer in pgAdmin or `explain.depesz` do the subtraction; by hand, start from the deepest node.
3. At that node compare `rows` estimated vs actual. Off by 10× or more: stale statistics (`ANALYZE`), correlated columns (`CREATE STATISTICS`), or an expression with no statistics.
4. Check `Buffers`: `read` ≫ `hit` means cold cache or a working set larger than memory; `temp written` means a spill.
5. Fix the root cause (index, statistics, query shape), re-run, compare.

### What the cost unit is
- Cost has no unit of time. It is measured in "sequential page fetches": `seq_page_cost = 1.0` by definition; `random_page_cost = 4.0` (1.1 on SSD is common), `cpu_tuple_cost = 0.01`, `cpu_index_tuple_cost = 0.005`, `cpu_operator_cost = 0.0025`.
- Seq Scan cost ≈ `pages × seq_page_cost + rows × cpu_tuple_cost`. You can reproduce the number from `pg_class.relpages` and `reltuples`.
- Costs are only comparable between plans for the same query on the same server; use them to understand the planner's choice, and `actual time` to measure.

### What EXPLAIN ANALYZE actually does
- Plain `EXPLAIN` plans the query and prints estimates; nothing runs. `EXPLAIN ANALYZE` **executes** it fully (an UPDATE really updates, so wrap in a rolled-back transaction) but discards the result rows instead of sending them, so network and client time are not included.
- It instruments every node with timers. On machines with a slow clock source the timing overhead can double the runtime of a query with millions of rows; `EXPLAIN (ANALYZE, TIMING OFF)` keeps row counts and drops per-node timing.
- The rows are not sent to the client, so serialization of wide or TOASTed values is skipped too; Postgres 17 `SERIALIZE` adds it back.

## Example
```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT o.id, s.msisdn
FROM esim_orders o
JOIN subscribers s ON s.id = o.subscriber_id
WHERE o.status = 'RESERVED' AND o.created_at > now() - interval '1 hour';

--  Nested Loop  (cost=0.86..412.10 rows=12 width=24) (actual time=0.05..318.40 rows=18450 loops=1)
--    Buffers: shared hit=74213
--    ->  Index Scan using esim_orders_created_idx on esim_orders o
--          (cost=0.43..90.20 rows=12 width=16) (actual time=0.03..41.20 rows=18450 loops=1)
--          Index Cond: (created_at > (now() - '01:00:00'::interval))
--          Filter: (status = 'RESERVED'::text)
--          Rows Removed by Filter: 61200
--    ->  Index Scan using subscribers_pkey on subscribers s
--          (cost=0.43..26.8 rows=1 width=16) (actual time=0.01..0.01 rows=1 loops=18450)
--  Planning Time: 0.4 ms
--  Execution Time: 320.1 ms
```
Two problems in one plan: the estimate `rows=12` vs actual 18 450 (status and time are correlated during an incident, and the planner assumed 1 % RESERVED), and `Rows Removed by Filter: 61200` because the index does not include `status`. A partial index `ON esim_orders (created_at) WHERE status = 'RESERVED'` removes the filter work; after `ANALYZE` the planner picks a hash join on `subscribers` instead of 18 450 loops.

## Pitfalls
- `EXPLAIN` without `ANALYZE` cannot show the misestimate; `ANALYZE` without `BUFFERS` cannot show the I/O. Use both.
- Timing overhead: `actual time` on machines with slow clock reads inflates small nodes; `EXPLAIN (ANALYZE, TIMING OFF)` keeps row counts without per-node timing.
- The plan for a prepared statement with bind parameters can differ from the plan for literals (generic plan). Check with `PREPARE`/`EXPLAIN EXECUTE` or `plan_cache_mode = force_generic_plan` to reproduce what the JDBC driver sees.
- A plan taken on a developer laptop with 1 000 rows says nothing about production; copy statistics or use a production-sized dataset.
- Index and planner background: [[postgres-indexes]], [[query-planning-and-execution]]; locks that make a fast plan wait anyway: [[postgres-locks]].
