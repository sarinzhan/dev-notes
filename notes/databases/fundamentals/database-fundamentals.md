---
title: Databases, DBMS, relational vs NoSQL, and views
tags: [databases, sql, fundamentals, views]
learned: 2026-10-22
---
## TL;DR
A database is an organized, structured store of related data; a DBMS (Postgres, MySQL, Oracle) is the software that stores it, enforces structure and constraints, runs queries and guarantees transactions. A **relational** database keeps data in tables of rows and columns linked by primary and foreign keys, has a fixed schema, is queried with SQL and offers ACID transactions. **NoSQL** systems drop one or more of those (tables, fixed schema, FK relations, strict ACID) to scale horizontally or to fit a particular data shape. A **view** is a saved `SELECT` that behaves like a table; a **materialized view** stores the result and must be refreshed.

## Key points

### What a database is
- Organized structure for storing, changing and processing large volumes of interrelated information. A phone book is the textbook example: entries (rows), fields (name, number), an ordering (an index).
- The **DBMS** sits between the application and the files: SQL parser and planner, storage engine, buffer cache, write-ahead log, lock manager/MVCC, access control. The application reaches it through JDBC, a pool ([[hikaricp-pool-sizing]]) and usually an ORM ([[jpa-entities-and-relationships]]).

### Relational databases
- Data lives in **tables**; each table has a strict set of typed columns (the schema) and rows (records).
- Tables are linked by keys: a **primary key** uniquely identifies a row; a **foreign key** is a column that references a primary key in another table, which is what makes the data "relational" (see [[constraints-and-relationships]]).
- Schema is defined up front and enforced: types, `NOT NULL`, uniqueness, checks.
- The language is **SQL**, split into DDL, DML, DCL, TCL (see [[sql-joins-and-commands]]).
- Transactions with **ACID** guarantees (see [[transactions-acid-and-isolation]]); the standard way to make a multi-step change all-or-nothing.
- The schema is designed through **normalization** to remove redundancy, then selectively denormalized for read speed (see [[normalization-and-denormalization]]).
- Reads are accelerated by **indexes** (see [[database-indexes]], [[postgres-indexes]]).
- Examples: PostgreSQL (the default choice for new services), MySQL/MariaDB, Oracle, SQL Server, SQLite.

### NoSQL databases
Chosen for very large volumes, high write throughput, flexible or nested data, or horizontal scaling across many nodes. The main families:
| Type | Example | Data model | Typical use |
|---|---|---|---|
| Key-value | Redis, DynamoDB | key → opaque value or small structure | caches, sessions, counters, queues ([[redis-data-structures]]) |
| Document | MongoDB, Couchbase | JSON-like documents, nested, schema-flexible | product catalogs, user profiles, CMS |
| Graph | Neo4j | nodes and edges with properties | social graphs, fraud rings, recommendations |
| Wide-column | Apache Cassandra, HBase, ScyllaDB | partition key → sorted rows of columns | time series, event logs, write-heavy at scale |
- Trade-offs: usually weaker transactions (per document / per partition), eventual consistency across replicas, no joins (you denormalize and duplicate), query patterns must be known at design time.
- Postgres covers a lot of the document use case with `jsonb` + GIN indexes before you need a second database.

### Views
- A **view** is a virtual table defined by a `SELECT` over one or more tables. It stores no data: every query against it runs the underlying `SELECT` at access time (the planner inlines it).
- Why: **security** (expose only some columns/rows, grant on the view and not on the tables), **simplification** (hide a 5-table join behind a name), **stability** (the app keeps querying `v_customer_summary` while tables are refactored underneath).
- Simple views (one table, no aggregates) are updatable in Postgres; `INSERT`/`UPDATE` go through to the base table. `WITH CHECK OPTION` prevents writing rows the view would not show.
- A **materialized view** physically stores the result; it is as fast as a table and can be indexed, but shows data as of the last `REFRESH MATERIALIZED VIEW` (use `CONCURRENTLY` with a unique index to refresh without blocking readers). Good for dashboards and heavy aggregates on a schedule; wrong for anything that must be current.
- A view can be mapped as a read-only `@Entity` (`@Immutable`) in JPA.

### Scaling (the source left this to be written)
- **Vertical**: bigger machine; simplest, has a ceiling.
- **Replication**: one primary takes writes, replicas (streaming WAL) serve reads; replication lag means a read-after-write on a replica may be stale. Spring routes reads with a routing `DataSource` and `@Transactional(readOnly = true)`.
- **Sharding**: rows split across independent databases by a key (customer id, region); each shard is smaller and faster, but cross-shard joins and transactions become application problems. Postgres: Citus, or application-level sharding.
- **Partitioning**: one logical table split into child tables inside one server (by range of dates, by hash); keeps indexes small and makes dropping old data a `DROP TABLE`.
- Stored **procedures/functions** (PL/pgSQL) and **triggers** run logic inside the DBMS: fast for data-local work (audit tables, counters), hard to version and test. Prefer application code unless the logic is purely about data integrity.

## Example
```sql
-- A view that hides the join and exposes only non-sensitive columns
CREATE VIEW v_active_subscriptions AS
SELECT s.id, c.msisdn, t.code AS tariff, s.activated_at
FROM subscriptions s
JOIN customers c ON c.id = s.customer_id
JOIN tariffs   t ON t.id = s.tariff_id
WHERE s.status = 'ACTIVE';

GRANT SELECT ON v_active_subscriptions TO reporting;

-- A materialized view for a dashboard, refreshed by a scheduler
CREATE MATERIALIZED VIEW mv_daily_activations AS
SELECT date_trunc('day', activated_at) AS day, tariff_id, count(*) AS n
FROM subscriptions GROUP BY 1, 2;
CREATE UNIQUE INDEX ON mv_daily_activations (day, tariff_id);
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_daily_activations;
```

## Pitfalls
- A view over a view over a view: the planner inlines them, but nobody can read the resulting plan; keep one level.
- `SELECT *` in a view definition freezes the column list at creation time; new table columns do not appear until the view is recreated.
- Materialized views are not auto-refreshed; forgetting the scheduler shows last month's numbers forever.
- "We need NoSQL for scale" before measuring: a well-indexed Postgres on one box handles tens of thousands of transactions per second.
- Deeper dives: [[transactions-acid-and-isolation]] and [[postgres-isolation-levels]] for concurrency, [[database-indexes]] for speed, [[normalization-and-denormalization]] for schema design.
