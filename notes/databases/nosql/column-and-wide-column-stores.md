---
title: Column-oriented and wide-column stores: Cassandra and ClickHouse
tags: [nosql, cassandra, clickhouse, column-store, time-series]
learned: 2026-10-27
---
## TL;DR
Two different things hide behind "column" databases. A **wide-column store** (Cassandra, ScyllaDB, HBase, DynamoDB) is a distributed key-value store where each partition key maps to a sorted set of rows; it is built for massive write throughput and linear horizontal scale, at the price of designing one table per query and giving up joins and ad-hoc filters. A **column-oriented analytical store** (ClickHouse, BigQuery, Redshift, Parquet files) stores each column separately so that scans and aggregates over billions of rows read only the columns they need and compress extremely well; it is built for OLAP, not for point updates. Both share a write path based on an append-only log plus sorted immutable files (LSM tree) rather than B-trees.

## Key points

### LSM tree write path (both families)
- A write goes to a commit log (durability) and an in-memory sorted table (**memtable**). When the memtable fills it is flushed to disk as an immutable sorted file (**SSTable**). Reads check the memtable, then SSTables newest-first, using Bloom filters ([[bloom-filters-and-probabilistic]]) and sparse indexes to skip files. Background **compaction** merges SSTables and drops overwritten or deleted entries.
- Consequences: writes are sequential and fast regardless of table size; reads can touch several files (read amplification); deletes are **tombstones** that take space until compaction; updates are just newer writes. Compare with the B-tree in-place model of Postgres ([[database-indexes]], [[postgres-vacuum-and-bloat]]).

### Wide-column: Cassandra / ScyllaDB
- Data model: `PRIMARY KEY ((partition_key), clustering_col1, clustering_col2)`. The partition key is hashed to a token on a ring ([[consistent-hashing]]) and decides the node; rows inside a partition are stored sorted by the clustering columns. A query must specify the full partition key and may then range over clustering columns in order; anything else needs `ALLOW FILTERING` (a full scan) or a secondary index that fans out to all nodes.
- **Query-first modeling**: list the queries, then create one table per query with the data duplicated and denormalized. "Events by device ordered by time" and "events by type for the last hour" are two tables, both written by the application (or by a materialized view, which has had reliability caveats). No joins, no foreign keys, no multi-partition transactions; lightweight transactions (`IF NOT EXISTS`, Paxos) exist per partition and are slow.
- Partition sizing: aim below ~100 MB and a few hundred thousand rows per partition; bucket time series by `(device_id, day)` so one device does not become an unbounded partition. A hot partition key (one tenant producing 80 % of traffic) overloads one node regardless of cluster size ([[sharding-strategies]]).
- Replication and consistency: leaderless, each partition on `RF` nodes; per-query consistency level `ONE`, `QUORUM`, `LOCAL_QUORUM`, `ALL`. `QUORUM` reads + `QUORUM` writes with RF 3 give read-your-writes; `ONE` gives eventual consistency. Conflicts resolve by last-write-wins timestamp, so clock skew matters ([[multi-leader-and-leaderless]], [[time-and-clocks]]). Repair runs anti-entropy to fix divergent replicas.
- Operational facts: every node is equal, add nodes to scale writes linearly, multi-datacenter replication is native, TTL per row makes retention free. Tombstone-heavy workloads (queues, frequent deletes) are the known anti-pattern.
- Use cases: time series and telemetry, device event logs, message history, activity feeds, anything write-heavy with known access paths. DynamoDB is the managed equivalent with the same partition-key discipline.

### Column-oriented analytics: ClickHouse
- Each column is a separate compressed file; a query reading 5 columns out of 200 reads 2.5 % of the data, and similar values compress 10–100×. Vectorized execution processes columns in CPU-cache-sized batches.
- `MergeTree` engines: data sorted by an `ORDER BY` key into parts that merge in the background (LSM again). A sparse primary index (one entry per 8192 rows) plus skip indexes replace B-trees. `PARTITION BY toYYYYMM(ts)` drops months instantly ([[postgres-partitioning]]).
- No real `UPDATE`/`DELETE` of single rows: mutations rewrite parts asynchronously and are meant for GDPR-style corrections, not OLTP. `ReplacingMergeTree`/`CollapsingMergeTree` give eventual deduplication on merge.
- Insert in batches (thousands of rows), never one row per request; each insert creates a part. Kafka engine tables or a buffer pull from [[kafka-architecture|Kafka]] directly.
- Use cases: event analytics, metrics and logs (the storage behind many observability products), reporting over billions of rows with sub-second `GROUP BY`. Joins are supported but the right-hand side should fit in memory; dimension tables are small, facts are wide.

### Comparison
| | Postgres | Cassandra / Scylla | ClickHouse |
|---|---|---|---|
| Model | relational, B-tree, MVCC | partition → sorted rows, LSM | columns, sorted parts, LSM |
| Scale | one primary + replicas, partition locally | linear horizontal, leaderless | sharded + replicated, one query uses many cores/nodes |
| Writes | thousands–tens of thousands/s per primary | hundreds of thousands/s per cluster | millions of rows/s in batches |
| Reads | point lookups, joins, any predicate | by partition key only | full scans and aggregates over few columns |
| Transactions | ACID across rows | per-partition, LWT | none |
| Updates | in place | newer write wins | batch mutations only |
| Typical role | system of record | high-volume operational events | analytics, metrics, logs |

## Example
```sql
-- Cassandra (CQL): device telemetry, bucketed by day so partitions stay bounded,
-- newest readings first, 30-day retention via TTL
CREATE TABLE esim.device_events (
    device_id   text,
    day         date,
    ts          timestamp,
    event_type  text,
    payload     text,
    PRIMARY KEY ((device_id, day), ts)
) WITH CLUSTERING ORDER BY (ts DESC)
  AND default_time_to_live = 2592000
  AND compaction = { 'class': 'TimeWindowCompactionStrategy', 'compaction_window_unit': 'DAYS', 'compaction_window_size': 1 };

-- The only cheap query shape: full partition key + range on the clustering column
SELECT ts, event_type, payload FROM esim.device_events
WHERE device_id = '8999600000000000001' AND day = '2026-10-27' AND ts > '2026-10-27T10:00:00Z';

-- ClickHouse: the same events for analytics, inserted in batches from Kafka
CREATE TABLE device_events (
    ts          DateTime,
    device_id   String,
    event_type  LowCardinality(String),
    latency_ms  UInt32
) ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (event_type, device_id, ts);

SELECT event_type, quantile(0.99)(latency_ms) AS p99, count() AS n
FROM device_events WHERE ts >= now() - INTERVAL 1 DAY
GROUP BY event_type ORDER BY n DESC;
```

## Pitfalls
- Choosing Cassandra and then asking "find all events where payload contains X": that is a full-cluster scan; the query must be known before the table exists.
- A Cassandra queue (insert, read, delete): tombstones pile up and reads slow down until compaction; use Kafka or a Redis stream for queues ([[redis-patterns-and-persistence]]).
- Treating ClickHouse as an OLTP store with single-row inserts and updates: "too many parts" errors and merges that never catch up.
- Running `QUORUM` writes with RF 2: a quorum of 2 is 2, so one node down stops writes; RF 3 is the minimum for availability math ([[quorums-and-leader-election]]).
- Related: [[storage-choices]] for picking by access pattern, [[sql-vs-nosql-choosing]] for the decision procedure.
