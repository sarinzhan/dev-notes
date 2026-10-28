---
title: Redis patterns and persistence: cache, locks, rate limits, streams
tags: [redis, cache, distributed-lock, persistence]
learned: 2026-10-27
---
## TL;DR
Redis is used for five recurring jobs in a backend: cache in front of the database, short-lived distributed lock, rate limiter, pub/sub bus, and lightweight queue or stream. Each pattern is a few commands plus one atomicity trick: `SET NX PX` for locks, `INCR` + `EXPIRE` or a sorted set for rate limits, a Lua script or `MULTI` when several commands must be atomic. Persistence is optional and configurable: RDB snapshots (compact, lossy), AOF (append log, durable to one second or one write), or both. Treat Redis as a store you can lose unless you have deliberately configured it otherwise. The data types themselves are in [[redis-data-structures]].

## Key points

### Cache
- Cache-aside is the default: read Redis, on miss read Postgres and `SET key value EX ttl`. Write-through and write-behind variants and their invalidation trade-offs are in [[caching]]; the Spring annotations are in [[spring-cache-abstraction]].
- Always set a TTL; use `maxmemory` with `allkeys-lru` or `allkeys-lfu` eviction so a full cache evicts instead of returning errors. Add jitter to TTLs so a batch of keys does not expire in the same second (stampede, see [[distributed-cache-coherence]]).
- Hot keys and big values serialize on the single command thread; split a 5 MB list of tariffs into per-tariff hashes.

### Distributed lock
- `SET lock:order:42 <token> NX PX 30000` acquires; release only if the token matches, in a Lua script (`if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end`). Without the token check, a slow client deletes the lock a second client already holds.
- The lock is a **lease**: if the holder pauses (GC, network) past the TTL, two holders exist. A Redis lock is good for reducing duplicate work, not for guaranteeing exclusivity; the storage system must still reject stale writers with a fencing token ([[distributed-locks-and-fencing]]).
- **Redlock** (acquire on a majority of N independent Redis nodes) is debated: it assumes bounded clock drift and process pauses, which real systems violate; a single Redis with a short TTL plus idempotent downstream writes is usually the honest design. For correctness-critical mutual exclusion use Postgres advisory locks ([[postgres-locks]]) or an etcd/ZooKeeper lease.
- Spring: `RedisLockRegistry` from Spring Integration, or Redisson's `RLock` with its watchdog that renews the TTL while the thread is alive.

### Rate limiting
| Algorithm | Keys | Notes |
|---|---|---|
| Fixed window | `INCR rl:<client>:<minute>` then `EXPIRE` on first hit | simplest; allows 2× burst at the window edge |
| Sliding window log | sorted set of request timestamps, `ZADD`, `ZREMRANGEBYSCORE`, `ZCARD` | exact; memory grows with the limit |
| Token bucket | hash with `tokens` and `last_refill`, updated in a Lua script | smooth bursts; one script call per request |
- The read-modify-write must be atomic across instances, which is why these are Lua scripts (`EVALSHA`) or Redis functions, never two round trips. Algorithms and placement are in [[rate-limiting]] and [[design-rate-limiter]].

### Pub/sub vs lists vs streams
- **Pub/sub**: fire-and-forget to currently connected subscribers; nothing is stored. Cache invalidation across pods, websocket fan-out ([[websockets-sse-and-long-polling]]).
- **List** as a queue: `LPUSH` / `BRPOP`; a message popped by a crashed worker is lost. `BLMOVE` to a processing list gives at-least-once with manual cleanup.
- **Stream** (`XADD`, `XREADGROUP`, `XACK`, `XPENDING`, `XAUTOCLAIM`): persistent log with consumer groups, per-consumer pending entries, and re-delivery of unacknowledged messages. The closest thing to Kafka in Redis, bounded by memory (`MAXLEN ~ 100000`). Good for small-volume event feeds inside one service; for durable multi-consumer streams at scale use [[kafka-architecture|Kafka]] ([[message-queues-and-streams]]).

### Persistence and durability
| Mode | What is written | Loss on crash | Cost |
|---|---|---|---|
| None | nothing | everything | fastest; pure cache |
| RDB | point-in-time snapshot every N seconds/changes (`save 60 1000`), fork + copy-on-write ([[memory-management]]) | minutes | fork memory spike on large datasets |
| AOF `appendfsync everysec` | every write command appended, fsync once per second; rewritten periodically | ≤ 1 s | small; the recommended durable mode |
| AOF `appendfsync always` | fsync per write | none | slow |
| RDB + AOF (Redis 7 multi-part AOF) | both; AOF used on restart | ≤ 1 s | default for "I need my data back" |

- Replication (`REPLICAOF`) is asynchronous; a failover by Sentinel or Cluster loses the last writes not yet replicated. `WAIT n timeout` blocks until `n` replicas acknowledged, but does not make it synchronous for everyone ([[leader-follower-replication]]).
- Redis Cluster shards keys by hash slot (16384 slots); multi-key commands and Lua scripts need all keys in one slot (`{order:42}:lock` hash tags). Sentinel provides failover for a non-sharded primary.
- Memory is the capacity: watch `used_memory` vs `maxmemory`, `evicted_keys`, `keyspace_misses`, and latency with `redis-cli --latency` or the `latency-monitor-threshold`.

## Example
```java
@Component
@RequiredArgsConstructor
public class RedisLeaseLock {
    private static final RedisScript<Long> RELEASE = RedisScript.of("""
        if redis.call('GET', KEYS[1]) == ARGV[1] then
          return redis.call('DEL', KEYS[1])
        end
        return 0
        """, Long.class);

    private final StringRedisTemplate redis;

    /** Runs action at most once per key across all pods while the lease holds; duplicates are skipped. */
    public boolean runOnce(String key, Duration lease, Runnable action) {
        String token = UUID.randomUUID().toString();
        Boolean acquired = redis.opsForValue().setIfAbsent("lock:" + key, token, lease);
        if (!Boolean.TRUE.equals(acquired)) return false;
        try {
            action.run();                    // must itself be idempotent: the lease can expire mid-run
            return true;
        } finally {
            redis.execute(RELEASE, List.of("lock:" + key), token);
        }
    }
}
```

## Pitfalls
- `DEL` on release without a token check: a client whose lease expired deletes somebody else's lock.
- A lock TTL shorter than the work: two workers run the job; a TTL much longer than the work: a crashed worker blocks the job for the whole TTL. Pick the TTL from the p99 duration and make the work idempotent ([[idempotency]]).
- Running Redis as "the database" with RDB only, then discovering after a restart that the last 10 minutes of orders are gone.
- `KEYS`, `FLUSHALL`, `SMEMBERS` on a huge set, or a Lua script that loops over 1 million entries blocks every other client for its duration.
- Related: [[caching]] for strategy, [[redis-data-structures]] for the commands, [[distributed-locks-and-fencing]] for why a lock alone is not enough.
