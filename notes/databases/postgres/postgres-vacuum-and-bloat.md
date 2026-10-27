---
title: Postgres MVCC, vacuum and bloat
tags: [postgres, mvcc, vacuum, maintenance]
learned: 2026-10-27
---
## TL;DR
Postgres never updates a row in place: an `UPDATE` writes a new row version and marks the old one as expired for later transactions (MVCC). Expired versions, **dead tuples**, stay on disk until **vacuum** reclaims them for reuse. If vacuum cannot keep up, tables and indexes grow with garbage (**bloat**), scans slow down, and eventually the 32-bit transaction id counter approaches **wraparound**, at which point Postgres refuses writes. Autovacuum handles this by default; tuning it per hot table and keeping transactions short is most of the operational work. Isolation semantics built on MVCC are in [[postgres-isolation-levels]].

## Key points

### MVCC on disk
- Every row version carries `xmin` (transaction that created it) and `xmax` (transaction that deleted or replaced it). A snapshot decides visibility by comparing these with the set of committed transactions; the `commit log` (`pg_xact`) records the outcome of each transaction id.
- `DELETE` sets `xmax`; `UPDATE` sets `xmax` on the old version and inserts a new one, possibly on another page. Indexes point to the new version too, unless the update is **HOT** (heap-only tuple): no indexed column changed and the page has free space, so the index is untouched and the old version chains to the new one on the same page. `fillfactor = 90` on hot-update tables leaves room for HOT.
- Readers never block writers because the old version is still there. The cost is that somebody must clean up.

### What VACUUM does
| Operation | Blocks | Effect |
|---|---|---|
| `VACUUM` (plain, what autovacuum runs) | nothing except DDL | marks dead tuples as reusable space inside pages, prunes HOT chains, updates the visibility map and free space map, removes dead index entries, freezes old xids; does not return space to the OS except trailing empty pages |
| `VACUUM FULL` / `CLUSTER` | whole table, `ACCESS EXCLUSIVE` | rewrites the table into a new file, returns space; needs double the disk; use `pg_repack` for an online equivalent |
| `ANALYZE` | nothing | refreshes planner statistics ([[query-planning-and-execution]]) |
| `VACUUM (INDEX_CLEANUP, PARALLEL n)` | nothing | PG 13+ vacuums indexes in parallel |

- Dead tuples are only removable when no running transaction could still see them: the oldest active snapshot (`backend_xmin` in `pg_stat_activity`), an idle-in-transaction session, a stale replication slot or a standby with `hot_standby_feedback` all hold back vacuum for the entire cluster. A long-running report blocks cleanup of a hot table it never touches.

### Autovacuum tuning
- Triggered per table when `dead tuples > autovacuum_vacuum_threshold (50) + autovacuum_vacuum_scale_factor (0.2) × rows`. For a 100-million-row table that is 20 million dead rows before the first run; set `autovacuum_vacuum_scale_factor = 0.01` and a `autovacuum_vacuum_cost_delay` of 2 ms (PG 12 default) on such tables. PG 13 added `autovacuum_vacuum_insert_threshold` so insert-only tables get vacuumed (and their visibility map set) too.
- `autovacuum_max_workers = 3` by default; one worker per table at a time, so a few huge tables can starve the rest. Raise workers and lower cost delay on servers with I/O headroom.
- Watch `pg_stat_user_tables`: `n_dead_tup`, `n_live_tup`, `last_autovacuum`, `autovacuum_count`. A table whose `n_dead_tup` keeps climbing between runs is under-vacuumed.
- Queue-style tables (outbox, job tables) with `DELETE` or status updates on every row are the classic bloat source: small live set, huge dead set. Partial indexes keep the index side small ([[postgres-indexes]]); batch deletes and aggressive per-table autovacuum settings keep the heap small; partitioning and `DROP` beat `DELETE` for retention ([[postgres-partitioning]]).

### Bloat
- Bloat = table or index size minus the size its live rows would need. Symptoms: sequential scans and index scans read more pages than the row count justifies, `Buffers: shared read` grows, cache hit ratio drops.
- Measure: `pgstattuple` extension (`SELECT * FROM pgstattuple('outbox')` gives `dead_tuple_percent` and `free_percent`), or the `pg_bloat_check` style query on `pg_class.relpages` vs expected. Index bloat: `pgstatindex`, `avg_leaf_density`.
- Fix: `REINDEX INDEX CONCURRENTLY` (PG 12+) for indexes; `pg_repack` or `VACUUM FULL` in a maintenance window for tables. B-tree deduplication (PG 13) and bottom-up index deletion (PG 14) reduced index bloat from non-HOT updates significantly.

### Transaction id wraparound
- Transaction ids are 32-bit and compared circularly; a row version older than about 2 billion transactions would suddenly look like it belongs to the future and vanish. Vacuum **freezes** old rows (marks them as visible to everyone) so their xid no longer matters. `autovacuum_freeze_max_age` (200 million) forces an **anti-wraparound vacuum** on a table even when autovacuum is disabled.
- Monitor `age(datfrozenxid)` per database; at 1 billion it is urgent, at ~2 billion Postgres stops accepting writes and you must vacuum in single-user mode. Causes: autovacuum disabled "because it was slow", long-open prepared transactions (`pg_prepared_xacts`), a stuck replication slot.

## Example
```sql
-- Who is holding back vacuum right now
SELECT pid, state, now() - xact_start AS xact_age, backend_xmin, left(query, 60) AS query
FROM pg_stat_activity
WHERE backend_xmin IS NOT NULL
ORDER BY age(backend_xmin) DESC
LIMIT 5;
--  pid  | state               | xact_age | backend_xmin | query
--  4182 | idle in transaction | 02:13:41 | 48811237     | SELECT * FROM outbox WHERE ...
-- An idle-in-transaction JDBC connection from a forgotten @Transactional over an HTTP call.

-- Per-table autovacuum for a hot queue table
ALTER TABLE outbox SET (
    autovacuum_vacuum_scale_factor = 0.01,
    autovacuum_vacuum_threshold    = 1000,
    autovacuum_vacuum_cost_delay   = 1,
    fillfactor                     = 85
);

-- Wraparound distance per database
SELECT datname, age(datfrozenxid) AS xid_age FROM pg_database ORDER BY 2 DESC;
```
Set `idle_in_transaction_session_timeout = '5min'` server-side so forgotten transactions cannot hold back vacuum for hours ([[postgres-performance-checklist]]).

## Pitfalls
- Disabling autovacuum on a "busy" table because vacuum showed up in `top`: the table bloats, every query slows down and the anti-wraparound vacuum arrives anyway, now with a much bigger job.
- `VACUUM FULL` in production on a large table takes an exclusive lock for the whole rewrite; the application sees a complete outage on that table.
- A `DELETE FROM events WHERE created_at < ...` of 50 million rows: the space is not returned, the table is the same size, now mostly dead; partition instead.
- Updating an indexed column on every row (`updated_at` indexed, touched by every write) disables HOT and doubles index write amplification.
- Isolation semantics on top of MVCC: [[postgres-isolation-levels]]; replication interaction (`hot_standby_feedback`, slots): [[postgres-replication-and-failover]].
