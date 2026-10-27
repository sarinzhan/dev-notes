---
title: Caching strategies — cache-aside, read/write-through, write-behind, TTL, invalidation, stampede
tags: [system-design, caching, redis, performance]
learned: 2026-10-27
---
## TL;DR
A cache trades freshness for latency and database load: a read served from memory costs 0.1–1 ms instead of 1–10 ms and costs the database nothing. The strategy answers three questions: who populates the cache (the application in **cache-aside**, the cache itself in **read-through**), when writes reach the store (**write-through** synchronously, **write-behind** later), and how stale data leaves (**TTL**, explicit **invalidation**, or versioned keys). The failure modes are stale reads, the **stampede** on a hot key's expiry, and hot keys that overload one node. Redis commands live in [[redis-data-structures]] and [[redis-patterns-and-persistence]]; the Spring annotations in [[spring-cache-abstraction]]; this note owns the strategies.

## Key points

### Where the cache sits
| Layer | Latency | Shared across pods | Typical content |
|---|---|---|---|
| Client / browser, CDN | 0 (local) / edge RTT | no / yes | static assets, public GETs ([[http-caching]], [[cdn]]) |
| In-process (Caffeine) | ~100 ns | no, each pod its own copy | config, templates, feature flags, hot reference data |
| Distributed (Redis) | 0.2–1 ms | yes | sessions, user preferences, computed views, counters |
| Database buffer cache | ~10 µs | n/a | pages; already there, tune `shared_buffers` first |

Two tiers (Caffeine in front of Redis) give the speed of the first and the consistency of the second; the in-process tier needs a short TTL or a pub/sub invalidation channel ([[distributed-cache-coherence]]).

### Population and write strategies
| Strategy | Read path | Write path | Consistency | Use when |
|---|---|---|---|---|
| Cache-aside (lazy) | app: cache miss → DB → `SET` with TTL | app writes DB, then `DEL` key | window of staleness between write and delete; simple | default for read-heavy data |
| Read-through | cache loads from DB on miss (loader function) | same as aside | same | library-managed caches (Caffeine `LoadingCache`, Spring `@Cacheable`) |
| Write-through | hit | app writes cache and DB synchronously | cache always fresh; writes slower; caches data never read | read-after-write must be exact |
| Write-behind (write-back) | hit | app writes cache, cache flushes to DB asynchronously | fastest writes; **data loss if cache dies** before flush | counters, view stats, where loss is acceptable |
| Refresh-ahead | hit; refresh before TTL ends if recently used | n/a | hides miss latency for hot keys | popular keys with expensive loads |

- With cache-aside, **delete on write, do not update**: two concurrent writers updating the cache can leave the older value last; a delete lets the next read repopulate. Delete *after* commit, or a concurrent reader can repopulate the old value from a not-yet-committed snapshot.
- The write-DB-then-delete-cache order still has a race (read miss → DB read old → write commits → delete → read sets old value). Small window, usually accepted; eliminate with a short TTL or a version in the key.

### Expiry and invalidation
- **Always a TTL**, even with explicit invalidation: it bounds how wrong a missed invalidation can be. Pick by tolerance: 30 s for balances, 10 min for templates, 24 h for immutable blobs.
- **Jitter the TTL** (`ttl + random(0..10 %)`) so keys loaded together do not expire together.
- **Versioned keys** (`prefs:v2:{userId}`, or `product:{id}:{updatedAt}`) make invalidation implicit: a new version is a new key, old ones expire. Good for whole-object caches; the version must be cheap to know.
- **Event-driven invalidation**: an `entity.updated` event ([[outbox-pattern]], [[domain-events-and-integration-events]]) drives `DEL` in every consumer that caches it; the only way to keep many services' caches coherent.
- Negative caching: cache "not found" briefly (10–60 s) so a flood of lookups for missing ids does not hit the DB; distinguish `null` from "absent" in the cache value.

### Stampede, hot keys, eviction
- **Stampede / dog-pile**: a hot key expires and 1 000 concurrent requests all miss and all query the DB. Fixes, from cheap to strong: request coalescing in-process (one loader per key, `ConcurrentHashMap.computeIfAbsent` or Caffeine), a short-lived distributed lock in Redis (`SET lock:key NX PX 2000`) so one pod loads while others wait or serve stale, refresh-ahead, or serving stale while revalidating ([[distributed-locks-and-fencing]]).
- **Hot key**: one key (a celebrity, a global config) gets 100 K rps and lands on one Redis node. Replicate it into the in-process tier, or shard it (`key:{0..15}` chosen at random on read, all written on update).
- **Eviction**: when memory is full Redis evicts by policy (`allkeys-lru`, `allkeys-lfu`, `volatile-ttl`); size the memory from the hot set ([[back-of-envelope-estimation]]) and watch the hit ratio, not just memory. The LRU algorithm itself is in [[lru-cache-design]].
- **Penetration**: queries for keys that never exist (random ids from an attacker) always miss; negative caching or a [[bloom-filters-and-probabilistic|Bloom filter]] in front.

### What to measure
Hit ratio per cache, p99 of a miss vs a hit, DB QPS before and after, evictions per second. A cache with a 60 % hit ratio on cheap queries may cost more than it saves; a 99 % hit ratio on expensive ones carries the system ([[grafana-dashboards-and-red-use]]).

## Example
Cache-aside for subscriber preferences with stampede protection: in-process coalescing per key, a Redis lock so only one pod loads a missing key, and stale-while-locked fallback. Spring `@Cacheable` is skipped on purpose to show the mechanics.

```java
@Service
public class SubscriberPrefsCache {
    private static final Duration TTL = Duration.ofMinutes(10);
    private static final Duration LOCK_TTL = Duration.ofSeconds(3);

    private final StringRedisTemplate redis;
    private final SubscriberPrefsRepository repo;
    private final ObjectMapper json;
    private final ConcurrentHashMap<String, CompletableFuture<Prefs>> inFlight = new ConcurrentHashMap<>();

    public Prefs get(long subscriberId) {
        String key = "prefs:v1:" + subscriberId;
        String cached = redis.opsForValue().get(key);
        if (cached != null) return decode(cached);

        // 1. Coalesce concurrent misses inside this pod: one loader per key.
        CompletableFuture<Prefs> f = inFlight.computeIfAbsent(key, k -> CompletableFuture.supplyAsync(() -> loadWithLock(k, subscriberId)));
        try {
            return f.join();
        } finally {
            inFlight.remove(key, f);
        }
    }

    private Prefs loadWithLock(String key, long subscriberId) {
        // 2. One loader across pods: SET NX with a short TTL. No fencing token needed, the lock only
        //    reduces DB load; correctness does not depend on it.
        Boolean locked = redis.opsForValue().setIfAbsent(key + ":lock", "1", LOCK_TTL);
        if (Boolean.TRUE.equals(locked)) {
            try {
                Prefs prefs = repo.findById(subscriberId).orElse(Prefs.defaults());
                long jitter = ThreadLocalRandom.current().nextLong(0, TTL.toSeconds() / 10);   // 0..60 s
                redis.opsForValue().set(key, encode(prefs), TTL.plusSeconds(jitter));
                return prefs;
            } finally {
                redis.delete(key + ":lock");
            }
        }
        // 3. Someone else is loading: wait briefly for the value, then fall back to the DB once.
        for (int i = 0; i < 20; i++) {
            LockSupport.parkNanos(Duration.ofMillis(50).toNanos());
            String v = redis.opsForValue().get(key);
            if (v != null) return decode(v);
        }
        return repo.findById(subscriberId).orElse(Prefs.defaults());
    }

    @Transactional
    public void update(long subscriberId, Prefs prefs) {
        repo.save(subscriberId, prefs);
        // 4. Delete, not update, and only after commit, so a reader cannot cache a pre-commit snapshot.
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override public void afterCommit() { redis.delete("prefs:v1:" + subscriberId); }
        });
    }

    private Prefs decode(String s) { try { return json.readValue(s, Prefs.class); } catch (IOException e) { throw new UncheckedIOException(e); } }
    private String encode(Prefs p) { try { return json.writeValueAsString(p); } catch (IOException e) { throw new UncheckedIOException(e); } }
}
```

For a thundering herd of 1 000 requests on one key this does one DB query per pod at most, usually one in the whole cluster, instead of 1 000.

## Pitfalls
- No TTL "because we invalidate": one missed event (consumer down, bug) and the key is wrong forever.
- Caching before measuring: adding Redis in front of a query that takes 1 ms and runs 10 times a second adds an operational dependency for nothing ([[postgres-performance-checklist]] first).
- Updating the cache inside the transaction: if the transaction rolls back, the cache holds a value that never existed; and a concurrent reader can store the pre-commit value.
- Serialised objects with a changed class: a deploy that changes the DTO makes every cached value undeserialisable; version the key or the payload.
- Treating Redis as the source of truth with write-behind and no persistence: a restart loses the counters ([[redis-patterns-and-persistence]]).
- Related: [[distributed-cache-coherence]] for multi-service invalidation; [[hibernate-caching]] for the ORM's second-level cache and why it is rarely what you want.
