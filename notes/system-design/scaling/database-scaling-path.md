---
title: Database scaling path — from indexes to sharding
tags: [system-design, scaling, postgres, databases]
learned: 2026-10-27
---
## TL;DR
Scale a relational database in a fixed order, and stop at the first step that works: fix queries and indexes, scale up the box, add read replicas for reads, add a cache in front, partition large tables, and only then shard writes across nodes. Each step is roughly ten times cheaper in engineering effort than the next, and each later step adds a permanent operational cost (lag, invalidation, cross-shard queries). In an interview, saying "shard" for 500 writes/s is the classic mistake; one well-tuned Postgres primary serves most problems up to tens of thousands of QPS.

## Key points

### The ladder
| Step | Solves | Costs | Capacity gain |
|---|---|---|---|
| 1. Queries and indexes | slow reads, lock contention | developer time | 10–1000× on bad queries |
| 2. Vertical scaling | CPU, RAM for buffer cache, IOPS | money, restart | until the biggest instance |
| 3. Connection pooling (PgBouncer) | too many connections, context switching | one more component | stable throughput under many clients |
| 4. Read replicas | read throughput, reporting isolation | replication lag, stale reads | reads scale linearly |
| 5. Caching | repeated reads of hot data | invalidation, memory | 10–100× for hot keys |
| 6. Partitioning | huge tables, retention, vacuum time | planner constraints | management, not raw throughput |
| 7. Sharding | write throughput, dataset larger than one node | cross-shard joins/transactions, rebalancing | linear in shards |

### Step 1: look at the queries first
- `pg_stat_statements` ranks queries by total time; the top three usually account for most load. Fix N+1 from the ORM ([[jpa-n-plus-one]]), add composite and partial indexes ([[postgres-indexes]], [[database-indexes]]), read plans with [[explain-analyze]].
- Over-indexing is also a scaling problem: every index slows writes and inflates vacuum work ([[postgres-vacuum-and-bloat]]).
- Statement and lock timeouts stop one bad query from taking the primary down ([[postgres-performance-checklist]], [[postgres-locks]]).

### Step 2–3: bigger box and a pooler
- Postgres is process-per-connection; thousands of idle connections waste RAM and CPU. PgBouncer in transaction mode multiplexes many app connections onto a few dozen server connections ([[hikaricp-pool-sizing]], [[sockets-and-connection-pooling]]).
- Buffer cache sized so the working set fits in RAM turns random disk reads into memory reads; this alone is often the whole "scaling" story.

### Step 4: read replicas
- Streaming replication ships WAL to replicas ([[postgres-replication-and-failover]]); reads route to replicas, writes to the primary ([[read-write-splitting-and-cqrs-lite]]).
- Lag is the price. Read-after-write flows ("create order, redirect to order page") must read the primary or wait for the replica to reach the write's LSN ([[read-replicas-and-stale-reads]], [[leader-follower-replication]]).
- Replicas do not help write-heavy workloads; every write is still applied on every replica.

### Step 5: cache
- Cache-aside with Redis in front of the hottest reads; strategies, TTLs and stampede protection are in [[caching]]. The cache hides read load but makes correctness your problem.

### Step 6: partitioning (one node)
- Declarative partitioning splits one logical table into child tables by range (time), list (region) or hash ([[postgres-partitioning]]). Benefits: partition pruning on the key, dropping old data by `DETACH`, smaller indexes and faster vacuum per partition.
- It is still one node, one primary; it buys manageability of big tables, not write capacity.

### Step 7: sharding (many nodes)
- Each shard owns a disjoint subset of rows, chosen by a shard key (tenant id, user id). Strategies and hot-partition handling: [[sharding-strategies]], [[consistent-hashing]].
- Costs that never go away: cross-shard queries and transactions ([[cross-shard-queries-and-transactions]]), unique constraints only per shard, resharding when a shard fills, and a routing layer (Citus, Vitess-style proxy, or the application).
- Alternative before sharding: move the write-heavy table to a store designed for it (Kafka for events, a wide-column store for time series) ([[storage-choices]], [[column-and-wide-column-stores]]).

### Numbers for estimates
- One primary on a good instance: tens of thousands of indexed reads/s, several thousand transactional writes/s; with `synchronous_commit = off` for non-critical data, more.
- Replication lag: single-digit milliseconds in a healthy LAN, seconds under heavy write bursts or long-running queries on the replica.
- Table size where partitioning starts to pay: hundreds of millions of rows, or when retention deletes are measured in hours.

## Example
Routing with a `AbstractRoutingDataSource` so that read-only transactions go to the replica, and the eSIM order page reads its freshly created order from the primary:

```java
public class ReplicaRoutingDataSource extends AbstractRoutingDataSource {
    @Override
    protected Object determineCurrentLookupKey() {
        boolean readOnly = TransactionSynchronizationManager.isCurrentTransactionReadOnly();
        return readOnly ? "replica" : "primary";
    }
}

@Service
@RequiredArgsConstructor
public class OrderQueryService {
    private final OrderRepository orders;

    @Transactional(readOnly = true)            // routed to the replica
    public Page<OrderView> history(String msisdn, Pageable page) {
        return orders.findByMsisdn(msisdn, page).map(OrderView::from);
    }

    @Transactional                             // primary: just-created order must be visible
    public OrderView justCreated(UUID orderId) {
        return OrderView.from(orders.findById(orderId).orElseThrow());
    }
}
```

The `LazyConnectionDataSourceProxy` wrapper is required so the lookup happens after Spring has set the read-only flag, not when the transaction opens ([[spring-transactional]]).

## Pitfalls
- Adding read replicas when the problem is writes or lock contention; the primary is still the bottleneck.
- Caching before indexing: the cache hides a bad query until the first cold start or key eviction storm.
- Sharding by a key the main queries do not contain (sharding by `order_id` when every screen filters by `user_id`) turns each query into scatter-gather.
- Long-running analytics on the primary; move reporting to a replica or a separate store ([[sql-vs-nosql-choosing]]).
- Related: [[horizontal-vs-vertical-scaling]], [[back-of-envelope-estimation]], [[transactions-acid-and-isolation]].
