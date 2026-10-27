---
title: Distributed cache coherence, invalidation and thundering herds
tags: [distributed-systems, caching, redis, consistency]
learned: 2026-10-27
---
## TL;DR
A cache is a replica with no replication protocol: nothing tells it when the source of truth changed, so staleness is the default and every "coherence" mechanism is a trade-off between staleness, write cost and complexity. The practical toolkit: short **TTLs** as the safety net, **invalidate on write** (delete, not update) for things users notice, **versioned keys** when invalidation is unreliable, and **request coalescing** plus **jittered expiry** so that an expired hot key does not send a thundering herd to the database. Cache *strategies* (aside, read-through, write-through, write-behind) are owned by [[caching]]; this note is about keeping many caches and the database from disagreeing.

## Key points

### Why caches go stale
- **Cache-aside** writes go to the DB and the cache is updated or invalidated by the application; a crash between the two, or two concurrent writers, leaves the cache wrong until TTL.
- **Many caches**: each service instance has a local Caffeine cache plus a shared Redis; invalidating Redis does nothing for the local copies ([[spring-cache-abstraction]]).
- **Replication lag**: a cache filled from a read replica can cache a value that was already overwritten on the primary ([[read-replicas-and-stale-reads]]).
- There is no coherence protocol like a CPU's MESI between application caches ([[cpu-caches-and-cache-lines]]); you build whatever consistency you need.

### Invalidate, do not update
- **Delete on write** (cache-aside): after the DB commit, `DEL key`. The next reader repopulates. A delete is idempotent and cannot write a stale value; an **update** can, when two writers race: writer A reads v1, writer B writes v2 to DB and cache, A writes v1 to cache. Stale until TTL.
- The classic remaining race: reader misses, reads v1 from DB; writer commits v2 and deletes; reader writes v1 into the cache. Rare (the reader's DB read must be slower than a whole write) but real; a short TTL or a version check bounds it.
- Delete **after** commit, not before: deleting before commit lets a concurrent reader refill the old value during the transaction. In Spring, `@CacheEvict` runs when the method returns, which with `@Transactional` on the same method is *before* commit; use `@TransactionalEventListener(AFTER_COMMIT)` or `TransactionSynchronization.afterCommit` to evict ([[spring-transactional]]).

### Propagating invalidation to every cache
| Mechanism | Latency | Reliability | Notes |
|---|---|---|---|
| **TTL only** | up to TTL | always works | the floor every design keeps |
| **Redis pub/sub** broadcast of keys | ms | fire-and-forget; a disconnected instance misses it | Caffeine + Redis two-level caches do this; combine with TTL |
| **Kafka topic of change events** (from outbox/CDC) | ms–s | durable, replayable; late joiners catch up | heavier; natural if you already have [[outbox-pattern]] / [[event-driven-architecture]] |
| **Versioned keys** `tariff:v{version}:{id}` | immediate for new readers | no invalidation needed; old versions age out | version must be cheaply available (a counter in Redis, an entity version column) |
| **Lease / validation** (ETag-style `If-None-Match` to the source) | per read | exact | a round trip per read; HTTP caches do this ([[http-caching]]) |

### Thundering herd (cache stampede)
- A hot key expires; 2 000 concurrent requests all miss, all query the DB for the same row, the DB saturates, latency spikes, more requests pile in ([[backpressure-and-flow-control]]).
- Fixes, usually combined:
  - **Request coalescing / single-flight**: only one loader per key per instance; others wait on the same future. Caffeine's `LoadingCache.get` already does this per JVM; across instances use a short Redis lock (`SET key:lock NX PX 2000`) so one instance refreshes and the others serve stale or wait.
  - **Jittered TTL**: `ttl + random(0, 10%)` so keys populated together do not expire together.
  - **Early / probabilistic refresh** (XFetch): refresh slightly before expiry with a probability that grows as expiry approaches; `refreshAfterWrite` in Caffeine is the simple version (serve stale, refresh in background).
  - **Serve stale while revalidating**: keep an expired value for a grace period and return it while one request refreshes.
  - **Negative caching**: cache "not found" briefly so a miss storm on a non-existent key (an attack or a bug) does not hit the DB.
- Related herd: a cold cache after a deploy or Redis restart; warm critical keys at startup or roll out gradually ([[kubernetes-basics]]).

### Deciding how much coherence to buy
- Reference data changed weekly: hours-long TTL plus a manual flush endpoint.
- State the user just changed (profile, cart): delete on write after commit, seconds-long local caches, read-your-writes via the primary ([[consistency-models]]).
- Decision data (balance, inventory, locks): do not cache, or verify the version against the source at decision time.

## Example
A two-level cache (Caffeine per instance, Redis shared) with coalesced loading, jittered TTL, and after-commit invalidation broadcast over Redis pub/sub.
```java
@Service
@RequiredArgsConstructor
public class TariffCache {
    private final StringRedisTemplate redis;
    private final TariffRepository repo;
    private final LoadingCache<Long, Tariff> local = Caffeine.newBuilder()
        .maximumSize(10_000).expireAfterWrite(Duration.ofSeconds(30))
        .build(this::loadShared);                        // single-flight per key per JVM

    public Tariff get(long id) { return local.get(id); }

    private Tariff loadShared(long id) {
        String key = "tariff:" + id;
        String json = redis.opsForValue().get(key);
        if (json != null) return Json.read(json, Tariff.class);
        Tariff t = repo.findById(id).orElseThrow();
        long ttl = 600 + ThreadLocalRandom.current().nextLong(60);   // 10 min + jitter
        redis.opsForValue().set(key, Json.write(t), Duration.ofSeconds(ttl));
        return t;
    }

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)   // only after the DB commit
    public void onChanged(TariffChangedEvent e) {
        redis.delete("tariff:" + e.id());
        redis.convertAndSend("cache-invalidate", "tariff:" + e.id());    // other instances drop local copy
    }

    @EventListener
    public void onInvalidateMessage(InvalidateMessage m) {
        if (m.key().startsWith("tariff:")) local.invalidate(Long.parseLong(m.key().substring(7)));
    }
}
```
The 30 s local TTL bounds the damage if a pub/sub message is lost; the Redis TTL bounds it if the invalidation code path is skipped entirely ([[redis-patterns-and-persistence]]).

## Pitfalls
- `@CacheEvict` on a `@Transactional` method: eviction happens before commit, a concurrent read refills the cache with pre-commit data.
- Updating the cache with the written value "to save a read": loses the race against a concurrent writer; delete instead.
- Same TTL for every key, set in one warm-up loop: they all expire together, every N minutes the DB gets a wave.
- Caching with no TTL "because we invalidate on every write": the first missed invalidation (deploy, network blip, a write path someone forgot) is stale forever.
- Related: [[caching]] for strategy selection, [[consistent-hashing]] for sharding a cache cluster without mass misses, [[cdn]] for the same problem at the edge.
