---
title: Postgres architecture: processes, memory and the life of an UPDATE
tags: [postgres, architecture, processes, mvcc, hot]
learned: 2026-10-30
order: 1
---
## TL;DR
Postgres is **multi-process**, not multi-threaded: a **postmaster** forks one **backend process** per client connection, plus a set of background processes (checkpointer, background writer, WAL writer, autovacuum, WAL senders, stats). They share one big memory area, **shared buffers** (the page cache of the database), plus WAL buffers and lock tables. Every UPDATE writes a **new row version** instead of changing the old one (MVCC), and unless the update is **HOT**, every index on the table gets a new entry. That design explains connection limits, the need for a pooler, bloat and VACUUM.

## Key points
### Processes
- **postmaster**: the parent. Listens on 5432, authenticates, forks a backend per connection, restarts children if one crashes (a crashed backend forces all others to reset shared memory).
- **Backend**: one per connection, a full OS process with its own memory (`work_mem` for sorts and hashes, catalog caches). Parses, plans and executes that client's queries ([[database-wire-protocols]]). Idle backends still cost ~5–10 MB and a slot in `max_connections`.
- **Background workers**: parallel query workers (`max_parallel_workers_per_gather`), logical replication workers, extensions (pg_cron).
- **Auxiliary processes**:
  - **checkpointer**: at each checkpoint flushes all dirty pages and records the redo start point ([[wal-redo-undo-and-durability]]);
  - **background writer**: trickles dirty pages to the OS between checkpoints so backends rarely have to write themselves;
  - **WAL writer**: flushes WAL buffers to disk periodically (for async commits);
  - **autovacuum launcher + workers**: VACUUM and ANALYZE tables that crossed thresholds ([[postgres-vacuum-and-bloat]]);
  - **WAL sender / receiver**: stream WAL to replicas ([[postgres-replication-and-failover]]);
  - **archiver**, **logger**, **stats** (shared memory stats since 15).
- Why processes: isolation (a crash in one backend does not corrupt another's private memory), portability in 1996. Cost: process creation per connection and a high per-connection memory footprint, so 10,000 direct connections is not realistic; use a pool (HikariCP in the app, PgBouncer in front) ([[hikaricp-pool-sizing]], [[process-management]]).

### Memory
- **shared_buffers** (commonly ~25 % of RAM): the database's own page cache, 8 KB pages. Pages are also in the OS page cache, so data can be cached twice; that is why Postgres does not take all RAM.
- **WAL buffers**: WAL records before they are written.
- **work_mem** per sort/hash node per backend (a query with 5 sorts may use 5× it); **maintenance_work_mem** for VACUUM and CREATE INDEX.
- Lock table, proc array (who is running, which snapshot), CLOG/pg_xact (commit status of each transaction id).

```anim
pg-update-hot
```

### Life of an UPDATE (MVCC on disk)
- Each row version (tuple) has `xmin` (transaction that created it) and `xmax` (transaction that deleted or replaced it). A snapshot decides which versions are visible.
- `UPDATE t SET status = 'PAID' WHERE id = 7`:
  1. find the visible version through an index;
  2. take a row lock (set `xmax` to our transaction id);
  3. write a **new tuple** with `xmin` = us, in the same page if there is room, otherwise elsewhere;
  4. old tuple's `t_ctid` points to the new one;
  5. insert a new entry pointing to the new ctid **into every index** of the table;
  6. write WAL for the heap and each index change.
- So an UPDATE touching one column of a table with 8 indexes writes 9 structures. That is write amplification, and it is what Uber complained about in 2016 ([[primary-vs-secondary-indexes]]).

### HOT updates (heap-only tuples)
- If **no indexed column changes** and the new tuple **fits in the same page**, Postgres skips the index inserts: indexes keep pointing at the old tuple, which redirects inside the page to the new one (a HOT chain). Pruning later removes dead versions in the page without VACUUM.
- Make updates HOT-friendly: do not index frequently updated columns unless needed, and leave room in pages: `ALTER TABLE t SET (fillfactor = 80)`.
- Watch the ratio: `n_tup_hot_upd / n_tup_upd` in `pg_stat_user_tables`.

### Postgres vs MySQL in one sentence
- Postgres: heap + version-in-heap + ctid indexes + VACUUM. InnoDB: clustered PK + in-place update + undo log + PK-pointing secondary indexes + purge threads. Neither is "better"; they move the cost to different places.

## Example
```sql
-- processes you can see from SQL
SELECT pid, backend_type, state, query FROM pg_stat_activity ORDER BY backend_type;

-- MVCC in action
CREATE TABLE t (id int PRIMARY KEY, status text) WITH (fillfactor = 80);
INSERT INTO t VALUES (7, 'NEW');
SELECT ctid, xmin, xmax, * FROM t;            -- (0,1) xmin=1001
UPDATE t SET status = 'PAID' WHERE id = 7;
SELECT ctid, xmin, xmax, * FROM t;            -- (0,2): a new tuple

-- how many updates were HOT?
SELECT relname, n_tup_upd, n_tup_hot_upd FROM pg_stat_user_tables WHERE relname = 't';
```

## Pitfalls
- `max_connections = 2000` instead of a pooler: memory blow-up and contention on the proc array; keep a few hundred at most.
- Huge `work_mem` set globally: many concurrent sorts multiply it and the OOM killer takes the postmaster's children.
- Indexing a column that changes on every update (e.g. `updated_at`): kills HOT, doubles write cost.
- Killing a backend with `kill -9`: postmaster restarts all sessions; use `pg_terminate_backend(pid)`.
- Related: [[postgres-vacuum-and-bloat]], [[postgres-performance-checklist]], [[how-tables-and-indexes-are-stored]].
