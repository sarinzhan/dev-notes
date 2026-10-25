---
title: Kafka architecture — brokers, topics, partitions, segments
tags: [kafka, messaging, distributed-systems, architecture]
learned: 2026-10-22
---
## TL;DR
Kafka is a distributed, append-only **commit log** split into topics and partitions and spread across brokers. Producers append to the end of a partition, consumers read it by offset, and nothing is deleted on read — retention or compaction decides what stays. Replication (leader + followers, ISR) gives fault tolerance; since Kafka 3.3 the cluster metadata lives in the built-in KRaft controller quorum, and Kafka 4.0 dropped ZooKeeper entirely.

## Key points

### Why a log instead of a database
- Classic applications model the world as objects and persist their *state*. Event-driven systems persist *what happened*. Storing events in a relational table works but is clumsy and slow; a sequential log file is the natural structure.
- Kafka decouples producers from consumers: a producer does not know who reads, consumers do not know who wrote, and an outage on one side does not stop the other. This is what makes it the backbone of microservice communication and the [[outbox-pattern]].
- Terminology: a **message** (record) is what the producer sends and the consumer receives; the **log** is the on-disk structure on the broker that holds those records.

### Cluster, brokers, controller
- A **cluster** is a set of **brokers** (JVM processes). The cluster is the "operating system", a broker is a "process". Producers write to the end of a broker's log; consumers read from it.
- Cluster metadata (which topics exist, which broker leads which partition, broker membership, ACLs) is managed by the **controller**. Historically that was ZooKeeper, an external ensemble with its own consensus protocol (ZAB). **KRaft** (KIP-500) moves the metadata into Kafka itself: a small quorum of controller nodes runs a Raft log called `__cluster_metadata`, and brokers replicate it. KRaft is production-ready since 3.3 (2022), ZooKeeper mode was deprecated in 3.5 and removed in **4.0** (2025).
- Why KRaft is better: one system to operate and secure, faster controller failover, metadata is an event log like everything else, and the cluster scales to millions of partitions instead of ~200k. Nodes can run as `broker`, `controller`, or both (`process.roles`).

### Topics and partitions
- A **topic** is a named logical stream. It consists of one or more **partitions**, which may live on different brokers; partitions spread load across brokers and are the unit of parallelism and ordering.
- A partition is a strictly ordered sequence; each record gets a monotonically increasing **offset**, assigned by the partition. Ordering is guaranteed **only within a partition**, never across the whole topic.
- The cluster places partitions on brokers when the topic is created, but it does **not** rebalance them later by itself. `kafka-reassign-partitions.sh` moves partitions between brokers, `kafka-topics.sh --alter` adds partitions (never removes them). Automatic balancing comes from external tools such as Cruise Control or managed platforms (Confluent, MSK).
- Adding partitions changes key → partition mapping, which breaks per-key ordering for existing keys. Decide the partition count upfront (see [[kafka-producer]] and [[kafka-consumer]]).

### Replication and ISR
- A partition stored on one broker is lost when that broker dies, so each partition has a configurable **replication factor** (typically 3). One replica is the **leader**; the others are **followers** that fetch from the leader and append to their own logs.
- Producers always write to the leader. Consumers read from the leader by default; since 2.4 (KIP-392) they may read from the closest follower (`replica.selector.class`, `client.rack`), accepting slightly stale data.
- **ISR (in-sync replicas)** is the set of replicas that are fully caught up with the leader (within `replica.lag.time.max.ms`, default 30 s). A write is "committed" when all ISR members have it; only committed records are visible to consumers (the **high watermark**).
- `min.insync.replicas` (topic or broker setting) is the minimum ISR size for a write with `acks=all` to be accepted. With replication factor 3 and `min.insync.replicas=2`, one broker can be down and writes continue; two down and producers get `NotEnoughReplicasException`. More required replicas = more durability, more latency.
- Leadership is not static. When a leader dies, the controller elects a new leader from the ISR, and clients reconnect transparently. `unclean.leader.election.enable=false` (default) prefers unavailability over electing an out-of-sync replica and losing data.

```anim
kafka-replication
```

### Segments and the on-disk format
- A partition on disk is a directory of **segments**, each an immutable, append-only log file. Records are appended to the **active segment** (the file currently open by the broker); when it reaches `segment.bytes` (default 1 GB) or `segment.ms` (default 7 days) it is **rolled**: closed and a new one opened. Closed segments are never modified, only deleted.
- Each segment is a set of files named by its base offset, e.g. `00000000000000368769`:
  - `.log` — the records: offset, byte position, timestamp (set by the producer, or by the broker with `log.message.timestamp.type=LogAppendTime`), key, value, headers.
  - `.index` — sparse offset → byte-position index for seeking by offset.
  - `.timeindex` — timestamp → offset index for seeking by time (`offsetsForTimes`).
- A record (since format v2, Kafka 0.11) is stored inside a **record batch** with a batch header (base offset, producer id, producer epoch, base sequence, compression codec, CRC) and a compact per-record body (offset delta, timestamp delta, key, value, headers). The broker stores the batch as the producer sent it, so compression is end-to-end.
- The broker writes through the OS page cache and serves consumers with zero-copy `sendfile`; sequential disk I/O is why Kafka is fast on cheap disks.

### Retention and compaction
- `cleanup.policy=delete` (default): closed segments older than `retention.ms` (default 7 days) or beyond `retention.bytes` are deleted whole. Retention is per segment, so a record can outlive `retention.ms` by up to one segment's lifetime.
- `cleanup.policy=compact`: the log cleaner keeps the **latest record per key** and removes older values from closed segments. A record with a `null` value is a **tombstone** and deletes the key after `delete.retention.ms`. Compacted topics act as a changelog / table: `__consumer_offsets`, Kafka Streams state stores, Debezium snapshots, "current state of every account".
- Both can be combined (`compact,delete`). Compaction only touches closed segments, so the head of the log always has duplicates for a while.
- **Tiered storage** (KIP-405, production-ready in 3.9 / 4.0) offloads closed segments to object storage so retention is no longer bound by broker disks.

### Why Kafka vs a classic broker
- A traditional queue ([[rabbitmq-basics]], [[activemq-basics]]) deletes a message when it is acknowledged and pushes to consumers; Kafka keeps the log and consumers pull by offset. That gives replay, multiple independent consumer groups on the same data, and very high throughput at the price of per-message routing features.

## Example
Durable topic for the eSIM activation stream: 12 partitions for consumer parallelism, replication factor 3, tolerate one broker down, 30-day retention.

```bash
kafka-topics.sh --bootstrap-server kafka-1:9092 --create \
  --topic esim.activation \
  --partitions 12 \
  --replication-factor 3 \
  --config min.insync.replicas=2 \
  --config retention.ms=2592000000 \
  --config segment.bytes=536870912

# compacted "current state" topic for subscriber profiles
kafka-topics.sh --bootstrap-server kafka-1:9092 --create \
  --topic subscriber.profile --partitions 12 --replication-factor 3 \
  --config cleanup.policy=compact --config min.insync.replicas=2

kafka-topics.sh --bootstrap-server kafka-1:9092 --describe --topic esim.activation
# Topic: esim.activation  PartitionCount: 12  ReplicationFactor: 3
#   Partition: 0  Leader: 2  Replicas: 2,0,1  Isr: 2,0,1
```

## Pitfalls
- Replication factor 3 with the default `min.insync.replicas=1` gives you no durability guarantee: `acks=all` is satisfied by the leader alone when followers fall out of the ISR. Always set `min.insync.replicas` ≥ 2 for topics that matter.
- Ordering is per partition. A "total order" requirement means one partition, which means one consumer and no parallelism.
- Partitions are not free: each one is open file handles, replication fetchers, and controller metadata. Thousands per broker are fine, hundreds of thousands per cluster need KRaft and planning.
- Compaction is not deduplication for consumers: the active segment and recently closed segments still contain old values, and tombstones disappear after `delete.retention.ms` (default 24 h), so a slow consumer may miss deletes.
- Course material written for ZooKeeper-era Kafka (clients fetching metadata "from ZooKeeper") is outdated: clients have fetched metadata from brokers since 0.9, and ZooKeeper is gone in 4.0.
- Related: [[kafka-producer]] (how records get into partitions, acks), [[kafka-consumer]] (groups, offsets, lag), [[spring-kafka-consumer]] (Spring side), [[outbox-pattern]].
- Links (sources of the original notes): habr.com/ru/companies/kuper/articles/738634 (Kafka internals overview, Russian); youtube.com/watch?v=-AZOi3kP9Js (Kafka architecture talk); youtube.com/watch?v=ivPGD-BxJ74 (ZooKeeper vs KRaft).
