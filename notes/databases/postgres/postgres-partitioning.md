---
title: Postgres declarative partitioning
tags: [postgres, partitioning, performance, retention]
learned: 2026-10-27
---
## TL;DR
Partitioning splits one logical table into child tables by a key (range of dates, list of values, hash of an id) inside one Postgres server. The planner reads only the partitions a query can touch (**partition pruning**), old data is removed with `DROP TABLE` or `DETACH PARTITION` instead of a million-row `DELETE`, and indexes and vacuum work on smaller pieces. It is not sharding: all partitions live on one instance and share its CPU and disk ([[sharding-strategies]] is the multi-node version). It pays off for large (tens of millions of rows), time-ordered or retention-driven tables, and costs little otherwise except planning time.

## Key points

### Strategies
| Strategy | `PARTITION BY` | Use when |
|---|---|---|
| **Range** | `RANGE (created_at)` | time series, logs, events, anything with retention; the common case |
| **List** | `LIST (region)` | small fixed set of values with different lifecycles or sizes (tenant, country) |
| **Hash** | `HASH (subscriber_id)` with `MODULUS n REMAINDER i` | spread a big table evenly when there is no natural range; smaller indexes, parallel vacuum |

- Declarative partitioning (PG 10) replaced inheritance plus triggers. Since PG 11: default partition, primary keys and unique constraints (must include the partition key), `INSERT` routing, `UPDATE` moving rows between partitions, indexes created on the parent propagate to every partition. PG 12: pruning at execution time for prepared statements and nested-loop parameters, foreign keys referencing partitioned tables. PG 13: logical replication of partitioned tables. PG 14: `DETACH PARTITION CONCURRENTLY`. PG 17: identity columns and exclusion constraints on partitioned tables.
- Sub-partitioning is possible (range by month, then hash by tenant); keep it simple unless measured.

### Pruning
- The planner removes partitions whose bounds cannot match the `WHERE` clause. Requires the predicate to be on the **partition key itself** with constants or parameters: `WHERE created_at >= '2026-10-01'` prunes; `WHERE date_trunc('day', created_at) = ...` does not; `WHERE created_at >= now() - interval '1 day'` prunes at execution time (PG 12+) because `now()` is stable, not immutable.
- `EXPLAIN` shows `Partitions removed: 11` or lists only the surviving `Append` children; `enable_partition_pruning` is on by default ([[explain-analyze]]).
- A query without the partition key scans every partition: `Append` of 36 index scans. Every important query on a partitioned table must carry the key, which drives the schema: an `orders` table partitioned by month cannot answer "order by id" cheaply unless the id encodes time (UUID v7 or a sequence correlated with time, see [[time-money-and-ids]]).

### Constraints and indexes
- A `PRIMARY KEY` or `UNIQUE` constraint must include all partition columns, because uniqueness is enforced per partition. `PRIMARY KEY (id, created_at)` is the usual compromise; global uniqueness of `id` alone relies on the generator.
- Foreign keys **from** other tables **to** a partitioned table need the full key as well; often it is simpler to reference the parent entity (subscriber) instead of the partitioned event row.
- `CREATE INDEX ON parent (...)` creates a matching index on each partition and future ones. To build it without locking: `CREATE INDEX CONCURRENTLY` on each partition, then `CREATE INDEX ON ONLY parent` plus `ALTER INDEX ... ATTACH PARTITION` ([[postgres-indexes]]).

### Maintenance
- Create partitions **ahead of time**: an insert with no matching partition fails with `no partition of relation "events" found for row`. A default partition catches strays but then blocks creating a partition whose range overlaps rows already in the default (it must be scanned). The `pg_partman` extension or a scheduled job creates monthly partitions in advance.
- Retention: `ALTER TABLE events DETACH PARTITION events_2026_04 CONCURRENTLY; DROP TABLE events_2026_04;` is instant and leaves no bloat, versus a `DELETE` that writes a million dead tuples and keeps the space until vacuum ([[postgres-vacuum-and-bloat]]).
- Autovacuum works per partition, so a hot current month gets vacuumed while cold months are untouched; `ANALYZE` on the parent gathers statistics across children.
- Partition-wise joins and aggregates (`enable_partitionwise_join`) join matching partitions pairwise when both tables share the layout; off by default because of planning cost.

### Planning cost
- Each partition is a relation the planner must consider; thousands of partitions make simple queries plan slowly (milliseconds per query, noticeable at high QPS). Aim for tens to low hundreds of partitions: monthly for a few years, daily only for very large volumes with short retention.
- Lock count: a query touching all partitions takes a lock per partition and can exceed `max_locks_per_transaction` in a big `SELECT`.

### Vertical vs horizontal partitioning
- **Horizontal**: split **rows** into tables with the same columns (`events_2026_10`, `events_2026_11`). This is what `PARTITION BY` does and what "partitioning" usually means.
- **Vertical**: split **columns** into separate tables: move a rarely-read wide `blob`/`jsonb` column into `orders_details(order_id, payload)` so the hot table has narrow rows, more rows per page and a smaller cache footprint ([[row-vs-column-storage]]). Postgres TOAST does a little of this automatically for big values.

### Partitioning vs sharding
- **Partitioning**: the pieces live in **one database server**; the client queries the parent table and the planner routes. Transactions, joins and constraints still work (within the partition-key limits above).
- **Sharding**: the pieces live on **different servers**; the client (or a proxy like Citus or Vitess) must know which server holds a key. Cross-shard transactions and joins become distributed problems ([[sharding-strategies]], [[cross-shard-queries-and-transactions]]).
- Hash partitioning on one server is the natural rehearsal for sharding with the same key.

### Advantages and disadvantages
| Advantages | Disadvantages |
|---|---|
| queries that carry the key scan one small partition (or its small index) | queries without the key scan all partitions, slower than one big index |
| bulk loads and retention: attach/detach, drop instead of delete | moving a row between partitions (updating the key) is a delete + insert |
| per-partition vacuum, indexes fit in memory | more relations to plan and lock; schema changes touch every partition |
| cold partitions can go to cheaper tablespaces | uniqueness only together with the partition key |
- Demo from the course: a 10-million-row `grades_org` table partitioned by grade range into 4 tables; `WHERE g = 30` uses one small partition's index, and each partition's index is a quarter of the size of the original.

### Automating partition creation
- Partitions must exist before rows arrive. Options: **pg_partman** (`create_parent()` + `run_maintenance()` creates future and drops expired partitions), a scheduled job with **pg_cron**, or the application's migration/scheduler creating next month's partition with `CREATE TABLE IF NOT EXISTS ... PARTITION OF`.
- Pre-create several periods ahead and alert when the newest partition is less than N periods in the future.

## Example
```sql
-- V12__partition_sim_events.sql: event log kept for 12 months, queried by subscriber and time
CREATE TABLE sim_events (
    id            uuid        NOT NULL,
    subscriber_id bigint      NOT NULL,
    type          text        NOT NULL,
    payload       jsonb       NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE INDEX ON sim_events (subscriber_id, created_at);   -- propagated to each partition

CREATE TABLE sim_events_2026_10 PARTITION OF sim_events
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE sim_events_2026_11 PARTITION OF sim_events
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE sim_events_default PARTITION OF sim_events DEFAULT;

-- Pruned: only sim_events_2026_10 is scanned
EXPLAIN SELECT type, payload FROM sim_events
WHERE subscriber_id = 4711 AND created_at >= '2026-10-20' AND created_at < '2026-10-27';
--  Index Scan using sim_events_2026_10_subscriber_id_created_at_idx on sim_events_2026_10
--    Index Cond: ((subscriber_id = 4711) AND (created_at >= ...) AND (created_at < ...))

-- Monthly retention job
ALTER TABLE sim_events DETACH PARTITION sim_events_2025_10 CONCURRENTLY;
DROP TABLE sim_events_2025_10;
```
With JPA the entity maps to `sim_events` as usual; the `@Id` must be the composite `(id, created_at)` or the repository must always filter by `created_at`, otherwise a `findById` scans every partition.

## Pitfalls
- Partitioning a 2 GB table "for the future": no benefit, extra planning cost, composite primary key forced on the ORM. Partition when the table is large or retention is the problem.
- Forgetting to create next month's partition: inserts fail at midnight on the first of the month. Automate it and alert when fewer than two future partitions exist.
- `UPDATE` of the partition key moves the row (delete + insert); with `FOR UPDATE` concurrency it can raise `tuple to be locked was already moved to another partition`.
- Converting an existing table: `ATTACH PARTITION` of the old table as one big range partition is instant if a `CHECK` constraint proving the bounds already exists; otherwise it scans the whole table under lock.
- Multi-node splitting is [[sharding-strategies]]; the place of partitioning on the scaling path is [[database-scaling-path]].
