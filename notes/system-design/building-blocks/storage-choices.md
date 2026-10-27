---
title: Storage choices — RDBMS, key-value, document, wide-column, blob, search, time-series by access pattern
tags: [system-design, databases, storage, nosql]
learned: 2026-10-27
---
## TL;DR
Pick a store by the **access pattern** (how data is written, how it is read, how it grows), not by the data's shape or by fashion. A relational database covers most services: transactions, joins, secondary indexes, mature tooling. Specialised stores earn their place when one pattern dominates and the RDBMS pays for it: a key-value store for sub-millisecond lookups by id, a wide-column store for write-heavy time-ordered data at tens of thousands of writes per second, blob storage for large immutable files, a search engine for full-text and faceted queries, a time-series database for metrics. The default interview answer is "Postgres, plus X for the one pattern that needs it"; the SQL/NoSQL decision tree is in [[sql-vs-nosql-choosing]].

## Key points

### The catalogue
| Store | Data model | Strong at | Weak at | Examples |
|---|---|---|---|---|
| Relational | tables, rows, FK, SQL | transactions, joins, ad-hoc queries, constraints, indexes | horizontal write scaling, very wide/sparse rows | Postgres, MySQL |
| Key-value | key → opaque value / structures | 0.2–1 ms lookups, counters, TTL, 100 K+ ops/s per node | queries by anything but the key | Redis, DynamoDB (also document-ish) |
| Document | key → JSON tree, secondary indexes | flexible schema, whole-aggregate reads, nested data | multi-document transactions, joins, analytics | MongoDB, Couchbase; Postgres JSONB covers a lot |
| Wide-column | partition key → sorted rows → columns | write-heavy, time-ordered, linear scaling, multi-DC | ad-hoc queries, joins, strong consistency, transactions | Cassandra, ScyllaDB, HBase |
| Blob / object | bucket + key → bytes + metadata | huge immutable files, cheap per GB, HTTP access, CDN origin | listing, mutation, small-object latency | S3, MinIO, GCS |
| Search | inverted index over documents | full text, relevance, facets, fuzzy, aggregations | source of truth, transactions, write latency | Elasticsearch, OpenSearch |
| Time-series | (metric, tags, time) → value, compressed | high ingest, downsampling, range queries by time | relational joins, updates | Prometheus/Mimir, TimescaleDB, ClickHouse |
| Graph | nodes, edges, traversals | multi-hop relationships (fraud rings, social) | bulk analytics, simple CRUD | Neo4j |
| Columnar / OLAP | columns, compressed, scans | analytics over billions of rows, aggregates | point reads, updates | ClickHouse, BigQuery |
| Log / stream | append-only partitions | events, replay, decoupling | queries | Kafka ([[message-queues-and-streams]]) |

### Questions that select the store
1. **How is it read?** By primary key only → KV or document. By many attributes with joins → relational. By text → search. By time range per entity → wide-column or time-series. Multi-hop → graph.
2. **How is it written?** Thousands of writes/s with transactions → Postgres primary (5–10 K/s). Tens of thousands/s, append-mostly, no cross-row transactions → wide-column or a log. Large files → blob.
3. **Consistency needed?** Money and inventory → ACID in one store ([[transactions-acid-and-isolation]]). Feeds, history, analytics → eventual is fine ([[consistency-models]]).
4. **Size and growth?** Under a few TB → one Postgres with [[postgres-partitioning|partitioning]] and replicas. Tens of TB of hot data → a natively sharded store or [[sharding-strategies|sharding]].
5. **Who operates it?** Each new store is a new on-call responsibility: backups, upgrades, failover. Managed services or Postgres extensions (JSONB, full-text, TimescaleDB) first ([[postgres-jsonb-and-full-text]]).

### Polyglot in practice: one system, several stores
A notification service ([[design-notification-service]]): Postgres for notifications, preferences and outbox (transactions, status queries); Redis for rate limits, dedup keys and the preferences cache; Kafka as the pipeline; S3 for attachments and provider logs; Elasticsearch optionally for operator search by text; Mimir for metrics. Each store holds one kind of data and the **relational store is the source of truth**; the others are derived and rebuildable ([[search-and-indexing-services]], [[caching]]).

### Modelling consequences
- Wide-column and document stores are **query-first**: design the table/collection per query (denormalise, duplicate), there are no joins to save you later ([[column-and-wide-column-stores]], [[mongodb-basics]], [[normalization-and-denormalization]]).
- Relational stays **data-first**: normalise, index per query, denormalise only measured hot paths ([[data-modeling-for-services]], [[postgres-indexes]]).
- Blob storage is **write-once**: versioning and lifecycle rules replace updates; metadata in Postgres, bytes in the bucket ([[file-and-blob-storage]]).

### Rough capacity anchors
| Store (one well-sized node) | Reads | Writes | Data |
|---|---|---|---|
| Postgres 17 | 20–50 K point reads/s | 5–10 K tx/s | up to several TB comfortably |
| Redis | 100 K+ ops/s | same | RAM-bound, tens of GB |
| Cassandra node | ~10 K/s | ~10–20 K/s | 1–2 TB per node, scale linearly |
| Elasticsearch node | 1–5 K queries/s | 5–20 K docs/s bulk | hundreds of GB per node |
| S3-style bucket | effectively unlimited, 3 500 PUT / 5 500 GET per prefix per second | | PB |

Use them with [[back-of-envelope-estimation]] to see whether a single Postgres is enough (it usually is).

## Example
Schema and placement for the notification domain, with the reasons as comments: the one place where a wide-column store would be justified (delivery attempts at very high volume) is kept in partitioned Postgres until the numbers demand otherwise.

```sql
-- Postgres: source of truth, transactional writes, status queries, outbox in the same transaction.
CREATE TABLE notification (
    id             uuid PRIMARY KEY,
    subscriber_id  bigint      NOT NULL,
    template       text        NOT NULL,
    params         jsonb       NOT NULL,                       -- flexible per-template data; no document DB needed
    priority       smallint    NOT NULL DEFAULT 1,
    status         text        NOT NULL DEFAULT 'ACCEPTED',
    created_at     timestamptz NOT NULL DEFAULT now()
) PARTITION BY RANGE (created_at);                             -- daily partitions, drop after 90 days
CREATE INDEX ON notification (subscriber_id, created_at DESC); -- history page per subscriber (keyset pagination)

-- Delivery attempts: 18 M rows/day, append-only, read by notification id or for ops dashboards.
-- Postgres handles ~200 inserts/s easily; the day a second provider pushes this to 20 K/s,
-- this table is the candidate for Cassandra/ClickHouse (partition key = notification_id, clustering = attempted_at).
CREATE TABLE delivery_attempt (
    notification_id uuid        NOT NULL,
    channel         text        NOT NULL,
    attempt_no      smallint    NOT NULL,
    provider        text        NOT NULL,
    result          text        NOT NULL,
    provider_ref    text,
    attempted_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (notification_id, channel, attempt_no)
) PARTITION BY RANGE (attempted_at);

-- Attachments: bytes in object storage, only the reference here.
CREATE TABLE attachment (
    id              uuid PRIMARY KEY,
    notification_id uuid NOT NULL REFERENCES notification(id),
    object_key      text NOT NULL,                             -- s3://notif-attachments/2026/10/27/<uuid>.pdf
    size_bytes      bigint NOT NULL,
    content_type    text NOT NULL
);

-- Redis (not SQL, listed for placement): rl:api:<client> token buckets, dedup:<idempotencyKey> with 24 h TTL,
-- prefs:v1:<subscriberId> cache with 10 min TTL.  Elasticsearch (optional): operator search over
-- notification text and provider errors, fed by CDC from the outbox.
```

## Pitfalls
- Choosing NoSQL "for scale" at 100 writes/s, then rebuilding joins and transactions in application code.
- Using Redis as a primary store without persistence and replication: a restart loses the data ([[redis-patterns-and-persistence]]).
- Storing files in `bytea` columns: backups, replication and vacuum all slow down; blob storage plus a reference is the pattern ([[postgres-vacuum-and-bloat]]).
- Treating Elasticsearch as the source of truth: it is an index; it loses writes under node failure in ways a database does not, and reindexing from the truth must stay possible.
- Many stores, no owner: each one needs backups, upgrade windows and monitoring; the ops cost is paid every month ([[requirements-and-constraints]]).
- Related: [[sql-vs-nosql-choosing]] for the decision tree; [[database-scaling-path]] for how far one Postgres goes before any of this is needed.
