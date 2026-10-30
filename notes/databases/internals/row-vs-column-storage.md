---
title: Row-oriented vs column-oriented storage
tags: [databases, storage, columnar, olap, oltp]
learned: 2026-10-30
order: 2
---
## TL;DR
A **row store** keeps all columns of a row next to each other in a page, so reading or writing one whole row is one page access: ideal for OLTP (`SELECT * FROM orders WHERE id = ?`). A **column store** keeps each column in its own run of pages, so an aggregate over one column reads only that column and compresses it well: ideal for OLAP (`SELECT sum(amount) FROM orders`). Each is slow at the other's job. Postgres and MySQL are row stores; ClickHouse, Redshift, BigQuery, Snowflake, DuckDB are column stores.

## Key points
### Row-oriented (row store)
- Rows are stored one after another: `1,John,Smith,101,555-1234,1000 | 2,Rick,...`. A page holds several complete rows.
- **Point lookup / fetch whole row**: find the row (index or scan), and all its columns are already in the page you read.
- **Aggregate over one column** (`SUM(salary)`): every page must be read, because every page holds a bit of `salary`, and you drag the other columns along. 1 million rows × 100 bytes = all of the table, to use 8 bytes per row.
- Writes are cheap: one row = one place to write.
- With an index on the filter column, `WHERE id = 7` becomes a couple of page reads; without one, a full scan stops early only if the row is found and the query has `LIMIT`.

### Column-oriented (column store)
- Each column lives separately: `id: 1,2,3,...`, `first_name: John,Rick,...`, `salary: 1000,2000,...`. Values of a column are matched to their row by position (row id).
- **Aggregate** (`SUM(salary)`): read only the `salary` pages. Same-type values sit together, so they compress very well (run-length, dictionary, delta encoding) and the CPU processes them in vectors.
- **Filter + several columns** (`SELECT first, last WHERE ssn = 666`): scan the `ssn` column to find row ids, then jump into `first` and `last` at those positions. Cost grows with the number of columns requested.
- **`SELECT *` of one row**: one read per column, as many IOs as there are columns. Terrible.
- **Writes**: inserting one row touches every column file; updates and deletes are expensive. Column stores batch inserts and are usually append-mostly.

### Comparison
| | Row store | Column store |
|---|---|---|
| Best for | OLTP: many small reads/writes of whole rows | OLAP: scans and aggregates over few columns |
| `SELECT *` one row | 1 page | 1 read per column |
| `SUM(col)` over all rows | reads whole table | reads one column, compressed |
| Writes / updates | cheap | expensive, batched |
| Compression | modest | high (same-type runs) |
| Multi-column filters | efficient | each column read separately |
| Examples | Postgres, MySQL, Oracle, SQL Server | ClickHouse, Redshift, BigQuery, Snowflake, DuckDB, Parquet files |

```anim
row-vs-column-read
```

### Why `SELECT *` hurts even in a row store
- It reads TOASTed (out-of-line) columns you do not need: each wide `text`/`jsonb` value means extra page reads and decompression ([[how-tables-and-indexes-are-stored]]).
- It kills **index-only scans**: if the index covers `id, status` and you ask for `*`, the database must visit the heap for every row.
- More bytes to serialize, send over the network and deserialize: the wire format ([[database-wire-protocols]]) carries every column, the client allocates objects for each.
- It breaks silently when a column is added (ORM mapping, `INSERT ... SELECT *`).
- In a column store it is worst: every column = a separate read.

### Mixed approaches
- Postgres extensions add columnar tables (Citus columnar, Hydra); SQL Server has columnstore indexes; MySQL HeatWave; Oracle In-Memory column store. Common pattern: OLTP in a row store, stream changes (CDC) into a column store for analytics.
- Wide-column stores (Cassandra) are **not** column stores: they are row stores with flexible columns per row ([[column-and-wide-column-stores]]).

## Example
```sql
-- OLTP on Postgres (row store): one page via the primary key index
SELECT * FROM orders WHERE id = 1001;

-- OLAP: on a row store this reads every column of every row
SELECT date_trunc('day', created_at) AS day, sum(amount)
FROM orders WHERE created_at >= now() - interval '30 days'
GROUP BY 1;

-- the same query on ClickHouse (column store) reads only created_at and amount
SELECT toDate(created_at) AS day, sum(amount)
FROM orders WHERE created_at >= now() - INTERVAL 30 DAY
GROUP BY day;
```

## Pitfalls
- Running dashboards with full-table aggregates on the production OLTP database; move them to a replica or a column store.
- Using a column store for a workload of single-row updates (status changes, counters): every update rewrites column parts.
- `SELECT *` in application code: fetch only the columns you use; it also enables covering indexes.
- Confusing "wide-column" (Cassandra) with "columnar" (ClickHouse).
- Related: [[storage-choices]], [[sql-vs-nosql-choosing]], [[cpu-caches-and-cache-lines]].
