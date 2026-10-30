---
title: Sharding strategies, range, hash and directory
tags: [distributed-systems, partitioning, sharding, scaling]
learned: 2026-10-27
---
## TL;DR
Sharding (horizontal partitioning across nodes) splits one logical dataset by a **shard key** so each node owns a slice of keys and a slice of the load. Three ways to map key → shard: **range** (contiguous key intervals, good for range scans, prone to hot ranges), **hash** (uniform spread, kills range queries), **directory** (an explicit lookup table, flexible but a dependency on every request). The shard key choice is the whole design: it decides which queries stay local, which become scatter-gather, and where hot spots appear. Postgres declarative partitioning inside one node is a cousin of this and lives in [[postgres-partitioning]].

## Key points

### Why shard, and why not yet
- Shard when one node cannot hold the data or sustain the write rate, after indexes, read replicas and caching are exhausted ([[database-scaling-path]]). Reads alone rarely justify sharding; replicas are cheaper ([[read-replicas-and-stale-reads]]).
- Sharding is irreversible in practice and removes things you took for granted: cross-shard joins, foreign keys, unique constraints, single-node transactions ([[cross-shard-queries-and-transactions]]).

### The three strategies
| Strategy | Mapping | Range queries | Hot-spot risk | Rebalancing | Examples |
|---|---|---|---|---|---|
| **Range** | key intervals: `[a–f] → s1, [g–m] → s2` | yes, locality by key | high: timestamps or sequential ids pile on the last shard | split a range, move it | HBase regions, Bigtable tablets, MongoDB ranged, Spanner |
| **Hash** | `hash(key) mod N` or hash ranges | no, adjacent keys scatter | low for key distribution; still high for one hot key | `mod N` moves almost everything; hash ranges / consistent hashing move 1/N ([[consistent-hashing]]) | Kafka partitions, Cassandra token ring, DynamoDB, Redis Cluster slots |
| **Directory** | lookup service: `tenant 42 → shard 7` | depends on what you put together | you control placement | move one entry at a time | multi-tenant SaaS, Vitess vindexes, Citus with `tenant_id` |

- Hybrids are the norm: hash the tenant to a shard, range-partition by time inside it; Cassandra's `(partition_key, clustering_key)` does exactly that.

### Choosing the shard key
- Pick the key most queries already filter by: `subscriber_id` for a telecom, `tenant_id` for SaaS, `order_id` for orders, `account_id` for a ledger ([[design-payment-processing]]).
- All data that must be touched in one transaction should share the key: subscriber, its SIM profiles, its orders. That is the aggregate boundary from DDD ([[aggregates-and-invariants]]).
- Queries that do not include the key become **scatter-gather** over all shards; keep them rare or serve them from a separate read model ([[cqrs]], [[search-and-indexing-services]]).
- Cardinality and spread: a key with few values (country) yields few, uneven shards; a key with skew (one celebrity account, one giant tenant) yields a hot shard regardless of strategy.

### Hot partitions
- Causes: sequential keys under range sharding (today's writes all land on one shard), one hot key under hash sharding (a viral post, a tenant with 40% of traffic), time-of-day skew.
- Fixes: **salting** the key (`userId + random(0..9)`, which spreads writes and costs 10 reads), **splitting** the hot range, **isolating** the heavy tenant onto its own shard via a directory, **caching** the hot key in front ([[caching]]), and for counters, sharded counters summed on read.
- Kafka version: one key with most messages means one partition and one consumer carry the load; the fix is a better key or sub-keys, not more partitions ([[kafka-architecture]]).

### Secondary indexes under sharding
- **Local** (document-partitioned) index: each shard indexes its own rows; reads by the secondary attribute are scatter-gather. Writes stay local. MongoDB, Cassandra SAI, Elasticsearch shards.
- **Global** (term-partitioned) index: the index itself is sharded by the indexed value; reads hit one shard, but a write updates another shard's index, asynchronously (DynamoDB GSIs, with eventual consistency).

### Rebalancing
- Fixed number of partitions larger than nodes (e.g. 1024 for 10 nodes), move whole partitions when nodes join: Elasticsearch, Riak, Redis Cluster's 16384 slots. Simple and predictable.
- Dynamic splitting when a range grows past a size: HBase, MongoDB.
- Never `hash mod N` with N = number of nodes; adding one node remaps nearly every key ([[consistent-hashing]]).
- Rebalancing competes with production traffic for IO; throttle it and do not run it automatically on a flapping node ([[failure-modes-and-timeouts]]).

### Sharding by hand (the course demo)
- Three Postgres containers (`pgshard1..3`) with the same `url_table(id serial, url text, url_id char(5))` schema. The application holds a **consistent hash ring** of the three servers ([[consistent-hashing]]).
- **Write**: hash the URL to a 5-character `url_id`, ask the ring which server owns that id, insert there, return the id. **Read**: the id alone tells the app which shard to query, one round trip, no scatter.
- What the demo shows: routing is application code, every client must share the same ring, adding a fourth server means migrating the keys that move, and there is no cross-shard query or transaction.

### Advantages and disadvantages
| Advantages | Disadvantages |
|---|---|
| scales storage, writes and memory beyond one machine | complex client or proxy, routing must be correct everywhere |
| smaller indexes per shard, better cache hit ratio | no cross-shard transactions, joins or global constraints |
| failure of one shard affects only its keys | schema changes on N servers; rebalancing is a project |
| shards can be placed near their users | queries without the key fan out to every shard |
- When to consider sharding, in order: optimize queries and indexes, add cache, add read replicas, partition, scale up the server, split by service (each service its own DB). Shard only when writes or data size still outgrow one node; YouTube's Vitess and Instagram's Postgres shards are examples of the "we had no choice" stage ([[database-scaling-path]]).

## Example
A directory-style router for a multi-tenant Postgres setup: one `DataSource` per shard, tenants mapped by a small table cached in memory, the hot tenant isolated on its own shard.
```java
@Component
public class ShardRouter {
    private final Map<String, DataSource> shards;              // "s0", "s1", "s2-big-tenant"
    private final LoadingCache<Long, String> tenantToShard;    // Caffeine, refreshed every 60 s

    public ShardRouter(Map<String, DataSource> shards, JdbcTemplate directoryDb) {
        this.shards = shards;
        this.tenantToShard = Caffeine.newBuilder().refreshAfterWrite(Duration.ofMinutes(1))
            .build(tenantId -> directoryDb.queryForObject(
                "SELECT shard FROM tenant_shard WHERE tenant_id = ?", String.class, tenantId));
    }

    public JdbcTemplate forTenant(long tenantId) {
        String shard = tenantToShard.get(tenantId);
        return new JdbcTemplate(shards.get(shard));
    }

    /** Scatter-gather for the rare cross-tenant query; runs in parallel, merges in memory. */
    public <T> List<T> acrossAllShards(Function<JdbcTemplate, List<T>> query) {
        return shards.values().parallelStream()
            .flatMap(ds -> query.apply(new JdbcTemplate(ds)).stream()).toList();
    }
}
```
Every tenant-scoped repository calls `forTenant(tenantId)` first; migrating a tenant is: copy rows to the new shard, flip the directory row, delete from the old one ([[multi-tenancy-strategies]], [[schema-migrations-flyway-liquibase]]).

## Pitfalls
- Choosing a sequential id or a timestamp as a range shard key and discovering that 100% of inserts hit one node.
- Sharding before fixing the schema: N+1 queries and missing indexes get N times worse ([[postgres-performance-checklist]]).
- Forgetting that `UNIQUE(email)` is now per shard; global uniqueness needs the directory, a dedicated lookup shard, or a deterministic placement by that attribute.
- A directory service that is a single point of failure on the hot path; cache it aggressively and make it replicated.
- Related: [[consistent-hashing]] for hash sharding that rebalances gracefully, [[cross-shard-queries-and-transactions]] for what breaks across shards, [[postgres-partitioning]] for partitioning inside one Postgres node.
