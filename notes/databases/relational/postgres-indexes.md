---
title: Postgres index types and when to use them
tags: [postgres, performance, indexes]
learned: 2026-08-29
---
## TL;DR
B-tree is the default and right 90% of the time. Reach for GIN for `jsonb` / arrays / full-text / trigram, GiST for geometry and ranges, BRIN for huge append-only tables, and partial, expression or covering indexes before adding more columns. General B-tree theory and selectivity live in [[database-indexes]]; this note is about what Postgres specifically offers and how to check that the planner actually uses it.

## Key points

### Index access methods
| Method | Operators | Use when |
|---|---|---|
| **B-tree** | `=`, `<`, `>`, `BETWEEN`, `IN`, `IS NULL`, `ORDER BY`, `LIKE 'abc%'` (with `text_pattern_ops` under non-C collations) | default; any scalar column |
| **Hash** | `=` only | long text keys where you never range; crash-safe since PG 10, rarely worth it over B-tree |
| **GIN** | `@>`, `?`, `&&`, `@@`, `%` (pg_trgm) | `jsonb`, arrays, full-text `tsvector`, `LIKE '%x%'` via `pg_trgm`; big, slow to update, fast to search; `fastupdate` pending list |
| **GiST** | `&&`, `<@`, `<->`, `=` | ranges, geometry/PostGIS, exclusion constraints (`EXCLUDE USING gist`), nearest-neighbour |
| **BRIN** | ranges over physically ordered data | append-only tables with `created_at`; a few KB for billions of rows, useless if data is not correlated with physical order |

### Index variants
- **Composite** (multi-column) B-tree: usable from the **leftmost prefix**. `(status, created_at)` serves `WHERE status = ?` and `WHERE status = ? AND created_at > ?`, but not `WHERE created_at > ?` alone. Put equality columns first, the range or `ORDER BY` column last.
- **Partial**: `WHERE published_at IS NULL` indexes only the hot rows. The query must contain a predicate the planner can prove implies the index predicate. Perfect for queue tables ([[outbox-pattern]]).
- **Expression**: `ON users (lower(email))` lets `WHERE lower(email) = ?` use the index; the query must use the identical expression.
- **Covering** (`INCLUDE (col)`, PG 11): non-key columns stored in leaf pages so the query can be answered by an **index-only scan**. Included columns are not part of uniqueness and cannot be searched.

### Index-only scans and the visibility map
- An index does not know whether a row version is visible to your transaction (MVCC). The planner uses the **visibility map**: a bit per heap page saying "all tuples visible". Pages with the bit set are skipped; others are fetched (`Heap Fetches` in `EXPLAIN`).
- After heavy updates the map is stale and index-only scans degrade to normal index scans. `VACUUM` (autovacuum) sets the bits; a high `Heap Fetches` count means vacuum more often.

### When the planner ignores an index
- **Low selectivity**: the condition matches more than roughly 5–10% of the table; a sequential scan is cheaper than random heap reads. Statistics (`ANALYZE`) decide, not you.
- **Function on the column**: `WHERE date(created_at) = ?` or `lower(email)` without an expression index.
- **Type mismatch**: `WHERE id = '42'` on a `bigint` works (cast on the literal), but `WHERE varchar_col = 42` casts the column and disables the index; same for a JDBC parameter sent as the wrong type.
- **Leading wildcard**: `LIKE '%foo%'` cannot walk a B-tree; use `pg_trgm` + GIN.
- **Small table**: a few pages are cheaper to scan than to traverse an index; this is correct behaviour.

### Reading EXPLAIN ANALYZE
- `EXPLAIN (ANALYZE, BUFFERS)` runs the query and prints estimated vs actual rows per node. A large gap means stale statistics or a correlated-column misestimate (`CREATE STATISTICS`).
- Node types: `Seq Scan`, `Index Scan` (index, then heap per row), `Index Only Scan`, `Bitmap Index Scan` + `Bitmap Heap Scan` (collect matching pages first, then read them in order; chosen for medium selectivity or `OR` across indexes).
- `Buffers: shared hit/read` shows cache vs disk.

### Maintenance and write cost
- `CREATE INDEX` takes a `SHARE` lock: reads continue, writes block. `CREATE INDEX CONCURRENTLY` does two table scans without blocking writes, cannot run in a transaction (Flyway: `-- flyway:executeInTransaction=false`), and leaves an `INVALID` index if it fails: drop and retry.
- Every index adds work to each `INSERT`/`UPDATE`/`DELETE` and defeats HOT updates when an indexed column changes. Five indexes on a hot table can halve write throughput.
- Bloat: dead index tuples accumulate; B-tree deduplication (PG 13) and bottom-up deletion (PG 14) reduce it. Measure with `pgstattuple`/`pg_stat_user_indexes`, fix with `REINDEX INDEX CONCURRENTLY` (PG 12). Drop unused ones (`pg_stat_user_indexes.idx_scan = 0`).

## Example
```sql
-- queue table: tiny partial index on the hot rows only
CREATE INDEX CONCURRENTLY outbox_pending_idx
  ON outbox (created_at)
  WHERE published_at IS NULL;

-- composite + covering: subscriber lookups answered by an index-only scan
CREATE INDEX CONCURRENTLY sim_msisdn_status_idx
  ON sim (msisdn, status) INCLUDE (iccid);

-- check the plan
EXPLAIN (ANALYZE, BUFFERS)
SELECT iccid FROM sim WHERE msisdn = '996555123456' AND status = 'ACTIVE';
--  Index Only Scan using sim_msisdn_status_idx on sim  (actual rows=1 loops=1)
--    Heap Fetches: 0
--    Buffers: shared hit=4
```

## Pitfalls
- `CREATE INDEX` without `CONCURRENTLY` in a migration locks writes on a production table for the duration of the build.
- `LIKE '%foo%'` cannot use a B-tree; use `pg_trgm` + GIN (`gin_trgm_ops`).
- A partial index with `WHERE status = 'PENDING'` is ignored when the query uses a bind parameter the planner cannot prove equals `'PENDING'` at plan time (generic plans after 5 executions with `plan_cache_mode = auto`).
- Isolation interacts with index scans under [[postgres-isolation-levels]] only through visibility, not locking.
- Related: [[database-indexes]] for the theory, [[jpa-n-plus-one]] for why ORM queries often miss composite indexes.
