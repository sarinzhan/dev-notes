---
title: Query planning and execution: from SQL text to rows
tags: [databases, query-planner, performance, statistics]
learned: 2026-10-27
---
## TL;DR
A SQL statement goes through four stages: the **parser** turns text into a tree, the **rewriter** expands views and rules, the **planner** (optimizer) enumerates possible execution plans and picks the one with the lowest estimated cost, and the **executor** pulls rows through that plan node by node. The planner never runs the query to decide; it relies on **statistics** about the data (row counts, distinct values, histograms) and a **cost model** with fixed weights for page reads and CPU work. Almost every "the database chose a bad plan" story is a wrong statistic or a cost parameter that does not match the hardware. How to read a concrete Postgres plan is in [[explain-analyze]].

## Key points

### The pipeline
| Stage | Input → output | What can go wrong |
|---|---|---|
| Parser | SQL text → parse tree; syntax and name resolution | syntax error, unknown column |
| Rewriter | views and `RULE`s inlined, `WITH` queries attached, row-level security predicates added | a 5-level view stack becomes an unreadable join |
| Planner | parse tree → cheapest plan tree | wrong estimates, join order explosion |
| Executor | plan tree → rows, streamed to the client | memory limits (`work_mem`) spill to disk |

- SQL is **declarative**: it says *what* rows, not *how*. The same query can run as a sequential scan plus hash join or as an index scan plus nested loop; the planner decides. Rewriting a query "for performance" really means giving the planner better options.
- Prepared statements split the pipeline: parse and plan once, execute many times (server-side after five executions in the Postgres JDBC driver). A **generic plan** built without the parameter values can be worse than a custom one; Postgres compares both and may switch (`plan_cache_mode`).

### What the planner chooses
- **Access method** per table: sequential scan, index scan, index-only scan, bitmap scan. Choice depends on selectivity and whether the index covers the columns ([[database-indexes]], [[postgres-indexes]]).
- **Join algorithm** per pair of inputs ([[sql-joins-and-commands]] has the SQL side):
  | Algorithm | Cost shape | Best when |
  |---|---|---|
  | Nested loop | outer rows × inner lookup | outer side is small and the inner side has an index on the join key |
  | Hash join | build hash of the smaller side, probe with the larger | equality joins on medium/large sets that fit in `work_mem` |
  | Merge join | sort both sides, zip | both inputs already sorted (index order) or very large sets |
- **Join order**: for *n* tables there are roughly *n!* orders. Up to `join_collapse_limit` (8 in Postgres) the planner tries them exhaustively; beyond that it switches to a genetic search (`geqo`) and the plan becomes non-deterministic in quality. Twelve-table ORM joins are hard for a reason.
- **Where to sort, aggregate, deduplicate**: a `Sort` node is avoided if an index already delivers the order; `HashAggregate` vs `GroupAggregate` depends on the estimated number of groups.

### The cost model
- Cost is a unitless number built from parameters: `seq_page_cost = 1`, `random_page_cost = 4` (set it to 1.1 on SSDs, the default assumes spinning disks), `cpu_tuple_cost = 0.01`, `cpu_operator_cost = 0.0025`, `effective_cache_size` (how much of the data the planner may assume is in the OS cache).
- Every plan node gets `startup cost` (before the first row) and `total cost`. A `LIMIT 10` query favours plans with low startup cost even if the total would be worse; this is why `ORDER BY created_at LIMIT 10` loves an index on `created_at`.
- Cost never measures milliseconds; two plans with costs 100 and 120 are "about the same", a plan of 100 000 vs 100 is a different access path.

### Statistics
- `ANALYZE` samples the table (`default_statistics_target = 100` → 30 000 rows) and stores per column: `null_frac`, `n_distinct`, most common values and their frequencies, a histogram of the rest, and `correlation` between column order and physical order (which decides whether an index range scan is cheap).
- Autovacuum runs `ANALYZE` after roughly 10 % of rows changed (`autovacuum_analyze_scale_factor`). A bulk load of ten million rows followed by queries before the next analyze gives plans built for an empty table: nested loops everywhere, minutes instead of milliseconds. Run `ANALYZE` explicitly after bulk operations ([[postgres-vacuum-and-bloat]]).
- Columns are assumed **independent**: `WHERE city = 'Bishkek' AND country = 'KG'` multiplies two selectivities and underestimates rows by orders of magnitude. `CREATE STATISTICS (dependencies, ndistinct) ON city, country FROM addresses` fixes it (Postgres 10+).
- Expressions (`date_trunc('day', ts)`) have no statistics unless you create an expression index or extended statistics on the expression (PG 14+).

### Executor model
- Volcano / iterator model: every node implements `next()`, the root pulls from children. Rows stream through without materializing, except at **blocking** nodes: `Sort`, `HashAggregate`, the build side of a `Hash Join`, `Materialize`. Those need memory bounded by `work_mem` per node (not per query); exceeding it spills to temp files (`Sort Method: external merge Disk:` in the plan).
- Postgres 9.6+ runs parallel workers for scans, joins and aggregates when the estimated cost exceeds `parallel_setup_cost`; a plan shows `Gather` with `Workers Planned/Launched`.
- JIT compilation (PG 11+) of expressions kicks in for expensive analytical queries; for OLTP it is usually disabled per query by cost thresholds.

## Example
```sql
-- A planner misestimate caused by correlated columns, and the fix
EXPLAIN SELECT * FROM sim_profiles WHERE operator = 'beeline_kg' AND country = 'KG';
--  Seq Scan on sim_profiles  (cost=0.00..21500.00 rows=120 width=96)
--    Filter: ((operator = 'beeline_kg') AND (country = 'KG'))
-- rows=120 is wrong: every beeline_kg profile is in KG, the real count is 400 000.

CREATE STATISTICS sim_profiles_op_country (dependencies)
  ON operator, country FROM sim_profiles;
ANALYZE sim_profiles;

EXPLAIN SELECT * FROM sim_profiles WHERE operator = 'beeline_kg' AND country = 'KG';
--  Seq Scan on sim_profiles  (cost=0.00..21500.00 rows=398500 width=96)
-- Now a join against this table will use a hash join instead of 400 000 nested-loop lookups.
```

## Pitfalls
- Trusting `EXPLAIN` without `ANALYZE`: estimates are the planner's opinion, actual rows are reality; the gap between them is the bug.
- `random_page_cost = 4` on NVMe storage pushes the planner towards sequential scans on tables that an index would serve in a tenth of the time.
- Tuning `work_mem` globally: it is per sort/hash node per backend, so 200 connections × 5 nodes × 256 MB can take the server down. Set it per session for reporting queries instead ([[postgres-performance-checklist]]).
- ORM-generated queries with `IN (?, ?, ..., ?)` of 10 000 ids defeat the plan cache and produce a different plan per batch size ([[jpa-n-plus-one]]).
- Reading real plans: [[explain-analyze]]; the index side of the planner's choices: [[database-indexes]].
