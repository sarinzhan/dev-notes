---
title: SQL vs NoSQL: how to choose a database for a service
tags: [databases, nosql, system-design, data-modeling]
learned: 2026-10-27
---
## TL;DR
Start with a relational database (Postgres) unless a concrete access pattern or scale requirement rules it out. Choose by four questions: how strong must consistency be, how much does the schema change and nest, what are the access patterns (point lookups, ranges, joins, full-text, graph walks), and what are the write volume and growth. The NoSQL families (key-value, document, wide-column, graph, search) each give up something relational databases have (joins, ad-hoc queries, cross-row transactions) to win at one specific pattern. The families themselves are described in [[database-fundamentals]]; this note is the decision procedure.

## Key points

### The four questions
| Question | Points to SQL | Points to a NoSQL family |
|---|---|---|
| **Consistency** | money, inventory, anything with invariants across rows: ACID transactions ([[transactions-acid-and-isolation]]) | counters, caches, feeds, analytics that tolerate eventual consistency ([[consistency-models]]) |
| **Schema** | stable, well-understood entities with relations; constraints enforced by the DB ([[constraints-and-relationships]]) | heterogeneous documents, per-tenant custom fields, rapid product iteration: document store |
| **Access patterns** | ad-hoc queries, reporting, joins across entities | one or two known patterns at huge volume (key lookup, time-ordered writes per partition, text search, graph traversal) |
| **Scale** | up to tens of thousands of TPS and a few TB on one primary plus replicas | writes beyond one machine, multi-region active-active, petabytes: wide-column or managed KV ([[sharding-strategies]]) |

### What each NoSQL family is actually for
- **Key-value** (Redis, DynamoDB): O(1) lookup by key, TTL, atomic counters. Cache, sessions, locks, rate limits ([[redis-data-structures]], [[redis-patterns-and-persistence]]). No secondary queries worth the name.
- **Document** (MongoDB): the aggregate stored as one JSON document, indexed on inner fields, updated atomically as a unit. Catalogs, profiles, CMS ([[mongodb-basics]]). Joins are the weak spot.
- **Wide-column** (Cassandra, ScyllaDB; ClickHouse for analytics): partition key → sorted rows, writes append to a log, linear horizontal scale. Time series, event logs, telemetry ([[column-and-wide-column-stores]]). Queries must follow the partition key; no ad-hoc filtering.
- **Search** (Elasticsearch, OpenSearch): inverted index, relevance ranking, facets ([[elasticsearch-basics]]). Never the system of record.
- **Graph** (Neo4j): variable-depth traversals (friends of friends, fraud rings). In SQL this is recursive CTEs that get slow past a few hops.

### Why Postgres is the default
- `jsonb` with GIN indexes covers most "we need flexible fields" cases ([[postgres-jsonb-and-full-text]]); `tsvector` covers basic search; partitioning covers time-series retention ([[postgres-partitioning]]); logical replication covers feeding a search index or a warehouse.
- One system means one backup strategy, one set of transactional guarantees, one thing to monitor. Each extra database adds an [[outbox-pattern]] or CDC pipeline to keep it in sync and a new class of consistency bugs.
- Scaling path before leaving SQL: indexes → read replicas → caching → partitioning → sharding ([[database-scaling-path]]).

### Signs you really need a second store
- A query shape Postgres fundamentally cannot index: fuzzy full-text with relevance across millions of documents, geo plus text plus facets together, deep graph traversal.
- Write rate that saturates one primary even after batching and partitioning (hundreds of thousands of small writes per second, telemetry from millions of devices).
- Data whose lifetime is seconds to hours and whose loss is acceptable: use the cache, not the database.
- Multi-region active-active writes with conflict resolution requirements ([[multi-leader-and-leaderless]]).

### Polyglot persistence in practice
- Keep **one system of record** per entity; every other store is a derived view rebuilt from it (search index from the orders table, cache from the catalogue). That decides where a bug fix goes and what you restore from backup.
- Derived stores are populated asynchronously: outbox/CDC → Kafka → indexer ([[search-and-indexing-services]]). Accept read-your-writes gaps or route the read-after-write to the primary.
- Storage choice per building block is summarized in [[storage-choices]]; the interview framing "pick by access pattern, then by consistency" is the answer to most "which DB?" questions.

## Example
```sql
-- Typical eSIM order service: relational core with a jsonb escape hatch,
-- instead of a separate document store for operator-specific fields.
CREATE TABLE esim_orders (
    id            uuid PRIMARY KEY,
    subscriber_id bigint NOT NULL REFERENCES subscribers(id),
    status        text   NOT NULL CHECK (status IN ('NEW','RESERVED','ACTIVATED','FAILED')),
    operator_data jsonb  NOT NULL DEFAULT '{}',   -- varies per MNO: iccid, eid, profile type
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX esim_orders_operator_data_gin ON esim_orders USING gin (operator_data jsonb_path_ops);

-- Flexible filter that still uses an index
SELECT id FROM esim_orders
WHERE operator_data @> '{"profileType": "prepaid"}' AND status = 'ACTIVATED';
```
Search across order history by free text, or 50k telemetry events per second from devices, would be the point to add Elasticsearch or a wide-column store, fed from this table.

## Pitfalls
- Choosing MongoDB "because the schema will change": schema changes in Postgres are a migration file ([[schema-migrations-flyway-liquibase]]); schema drift in a document store is a runtime surprise three years later.
- Choosing Cassandra and then needing a query by a non-partition column; the fix is a second table per query, written by the application, with no transactions between them.
- Treating a cache or search index as the source of truth, then losing it on a restart or reindex.
- "NoSQL is schemaless": the schema exists, it just lives in application code without enforcement.
- Related: [[storage-choices]] for the system-design view, [[cap-and-pacelc]] for what "eventually consistent" costs, [[normalization-and-denormalization]] for the relational modeling side.
