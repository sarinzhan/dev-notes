---
title: Postgres index types and when to use them
tags: [postgres, performance, indexes]
learned: 2026-08-29
---
## TL;DR
B-tree is the default and right 90% of the time. Reach for GIN for `jsonb` / arrays / full-text, BRIN for huge append-only tables, and partial or covering indexes before adding more columns.

## Key points
- Column order matters in a composite B-tree: equality columns first, then the range column.
- **Partial index**: `WHERE status = 'PENDING'` keeps the index tiny for queue-like tables (the [[outbox-pattern]] table is the classic case).
- **Covering index** (`INCLUDE (...)`) enables index-only scans; needs a well-vacuumed visibility map.
- `EXPLAIN (ANALYZE, BUFFERS)` is the source of truth. `Seq Scan` on a small table is fine.
- Indexes slow writes and bloat; drop unused ones (`pg_stat_user_indexes.idx_scan = 0`).

## Example
```sql
CREATE INDEX CONCURRENTLY outbox_pending_idx
  ON outbox (created_at)
  WHERE published_at IS NULL;
```

## Pitfalls
- `CREATE INDEX` without `CONCURRENTLY` locks writes on the table.
- `LIKE '%foo%'` cannot use a B-tree; use `pg_trgm` + GIN.
- Isolation interacts with index scans under [[postgres-isolation-levels]] only through visibility, not locking.
