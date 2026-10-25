---
title: Database indexes: how they work and when they hurt
tags: [databases, indexes, performance, b-tree]
learned: 2026-10-22
---
## TL;DR
An index is an extra data structure kept next to a table so that the DBMS can find rows matching a condition without scanning the whole table. The default structure is a B-tree: sorted keys with pointers to rows, O(log n) lookups, supports equality, ranges and ordering. Every index costs write time and disk, and is only used when the condition is selective enough that reading the index plus the rows is cheaper than reading the table. Postgres-specific types (GIN, BRIN, partial, `INCLUDE`) are in [[postgres-indexes]].

## Key points

### How an index works
- Without an index: **full table scan** (`Seq Scan`), O(n), reads every page.
- With a B-tree: root → internal pages → leaf pages hold `(key, row pointer)` sorted by key; a lookup descends 3–4 levels for millions of rows, then follows pointers to the table (heap) pages. In Postgres the pointer is a `ctid` (page, offset); in MySQL InnoDB the secondary index stores the primary key and the lookup goes through the clustered PK index.
- Leaf pages are linked, so a range (`BETWEEN`, `>`, `LIKE 'abc%'`) and an `ORDER BY` on the key are a single walk along the leaves; the index can also provide sorted output and skip a sort.
- A **unique** index is the physical implementation of `PRIMARY KEY` and `UNIQUE` constraints ([[constraints-and-relationships]]).

### Index types (general)
| Type | Supports | Notes |
|---|---|---|
| **B-tree** | `=`, `<`, `>`, `BETWEEN`, `IN`, `IS NULL`, `ORDER BY`, prefix `LIKE` | default everywhere; right for 90 % of columns |
| **Hash** | `=` only | slightly smaller/faster for equality on long values; no range, no ordering; in Postgres crash-safe only since PG 10 |
| **Clustered** | the table itself is stored in key order | InnoDB primary key and SQL Server clustered index; one per table. Postgres has no clustered index; `CLUSTER` reorders once and does not maintain it |
| **Non-clustered / secondary** | separate structure pointing at rows | any number per table; all Postgres indexes |
| Bitmap, GIN, GiST, BRIN, full-text, spatial | set membership, containment, ranges, text | engine-specific, see [[postgres-indexes]] |

### Composite (multi-column) indexes
- `(a, b, c)` is sorted by `a`, then `b` within `a`, then `c`. It serves `WHERE a = ?`, `WHERE a = ? AND b = ?`, `a = ? AND b > ?`, but **not** `WHERE b = ?` alone efficiently (leftmost-prefix rule).
- Put equality columns first, then the one range column, then columns for `ORDER BY`. `(status, created_at)` serves `WHERE status = 'NEW' ORDER BY created_at`; `(created_at, status)` does not.
- One composite index usually beats two single-column ones for a two-column predicate (the planner can combine them with a bitmap scan, at a cost).

### Covering indexes and index-only scans
- If every column the query needs is **in the index**, the engine never touches the table: an index-only scan. Add the extra columns with `INCLUDE (...)` (Postgres 11+, SQL Server) or just append them to the key.
- In Postgres an index-only scan still has to check row visibility via the visibility map; a heavily updated table without recent `VACUUM` falls back to heap fetches.

### Selectivity
- Selectivity = fraction of rows a condition returns. An index on `status` with three values returning 40 % of the table is useless: reading 40 % of the rows through random index pointers is slower than a sequential scan, and the planner knows it from statistics (`ANALYZE`).
- Rule of thumb: an index pays off when the predicate returns well under ~5–10 % of rows, or when it provides ordering/limit (`ORDER BY created_at DESC LIMIT 20`).
- Low-cardinality columns still make sense inside a composite or partial index (`WHERE status = 'PENDING'` on a queue table where 0.1 % of rows are pending).
- Statistics go stale after bulk loads; run `ANALYZE`, check `EXPLAIN (ANALYZE, BUFFERS)` for "rows estimated vs actual" mismatches.

### When indexes hurt
- **Writes**: every `INSERT`/`DELETE` updates every index; an `UPDATE` of an indexed column too (Postgres HOT updates avoid it only when no indexed column changed and the page has room). Ten indexes on a hot table can halve insert throughput.
- **Disk and cache**: indexes compete with table pages for `shared_buffers`/OS cache; a large unused index evicts useful data.
- **Bloat**: deleted keys leave dead entries until vacuum; B-trees never shrink; `REINDEX CONCURRENTLY` fixes it.
- **Functions and casts kill usage**: `WHERE lower(email) = ?`, `WHERE created_at::date = ?`, `WHERE id = '42'` (text vs bigint) skip a plain index; create an expression index or rewrite the predicate as a range.
- Leading wildcard `LIKE '%abc%'` cannot use a B-tree.
- Unused indexes should be found (`pg_stat_user_indexes.idx_scan`) and dropped.

## Example
```sql
-- Queue-style table: workers pull the oldest pending rows for a given shard
CREATE TABLE esim_orders (
    id          bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    shard       smallint    NOT NULL,
    status      varchar(16) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    payload     jsonb       NOT NULL
);

-- equality columns first, then the range/order column
CREATE INDEX esim_orders_pending_idx
    ON esim_orders (shard, created_at)
    WHERE status = 'PENDING';           -- partial: tiny, only the rows that matter

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, payload FROM esim_orders
WHERE shard = 3 AND status = 'PENDING'
ORDER BY created_at
LIMIT 100 FOR UPDATE SKIP LOCKED;
-- Expect: Index Scan using esim_orders_pending_idx, no Sort node
```

## Pitfalls
- Indexing every column "just in case" slows writes and leaves the useful ones competing for cache.
- An index that is never used because the application casts or wraps the column in a function: compare the query text in `pg_stat_statements` with the index definition.
- Creating an index on production without `CONCURRENTLY` locks writes for the whole build (see [[postgres-indexes]]).
- Forgetting indexes on foreign-key columns, which Postgres does not create automatically ([[constraints-and-relationships]]).
- An index cannot fix a query that loops in the application; for ORM-generated per-row queries see [[jpa-n-plus-one]]; for the join algorithms that consume indexes see [[sql-joins-and-commands]].
