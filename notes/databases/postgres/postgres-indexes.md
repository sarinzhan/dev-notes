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

### Seq scan vs index scan vs bitmap scan
- **Index Scan**: for each matching index entry, fetch its heap page. Best for a handful of rows; with many rows the same heap page may be read many times in random order.
- **Bitmap Index Scan + Bitmap Heap Scan**: first read all matching index entries and build a bitmap of **pages** (one bit per page), then read those pages once each in physical order and re-check the condition (`Recheck Cond`). Good for medium selectivity and for combining indexes with `BitmapAnd`/`BitmapOr`; it loses the index order, so an `ORDER BY` needs a sort.
- **Seq Scan**: when most pages contain a matching row anyway, reading them sequentially is cheapest.
- Heap fetches instead of index-only: if `Heap Fetches` is high in an Index Only Scan, the visibility map is stale; VACUUM the table.
- Index scan vs index seek: Postgres has one "Index Scan" node; an `Index Cond` means a seek to a key or range, and without one (only for ordering) it reads the index end to end.

### CREATE INDEX CONCURRENTLY, step by step
1. Creates the index entry in the catalog as **invalid** and waits for all transactions that could modify the table to finish.
2. First table scan builds the index; writes during the build are not in it yet.
3. Waits again, then a second scan adds rows changed during the first pass.
4. Waits for old snapshots to finish and marks the index **valid**.
- If it fails (duplicate key for a unique index, cancel), an **invalid** index stays behind: it costs writes but is never used. Find with `pg_index.indisvalid = false`, drop and retry.
- A plain `CREATE INDEX` on a busy table blocks all writes for the whole build: minutes of downtime on a large table ([[schema-migrations-flyway-liquibase]]).

### Postgres vs MySQL indexing
- Postgres indexes point to the heap tuple (ctid); InnoDB secondary indexes store the primary key. Consequences for update cost, UUID keys and covering indexes: [[primary-vs-secondary-indexes]].
- InnoDB secondary indexes are implicitly covering for the PK columns (they are in every entry); in Postgres you add them with `INCLUDE`.

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
