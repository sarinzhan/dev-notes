---
title: Redis data structures cheat sheet
tags: [redis, cache, nosql]
learned: 2026-10-19
---
## TL;DR
Redis is a single-threaded in-memory server with typed values. Pick the structure by the operation you need, not by the shape of the data.

## Key points
- **String**: cache entries, counters (`INCR`), distributed locks (`SET k v NX PX 30000`).
- **Hash**: one object with fields you update independently.
- **List**: queues (`LPUSH` / `BRPOP`).
- **Set / Sorted set**: uniqueness, leaderboards, rate limiting by score = timestamp.
- **Stream**: append-only log with consumer groups; a lightweight alternative to Kafka for small volumes.
- Keys expire with `EX`; always set a TTL on cache keys.
- **Keys and limits**: a key is any binary-safe string (up to 512 MB, keep it short: `idem:<id>`, `cache:user:<id>`), a single instance addresses up to 2^32 keys (> 4 billion), and one value (String, or one element of a collection) is capped at 512 MB. Values are byte strings; typed structures sit on top of them.
- **Pub/Sub** (`PUBLISH` / `SUBSCRIBE`, pattern `PSUBSCRIBE`): fire-and-forget fan-out, no persistence and no delivery to offline subscribers. Use it for cache invalidation or live notifications; for a queue you need, use a List or a Stream.
- **Geo** (`GEOADD` / `GEOSEARCH`): points with lat/lon stored as a Sorted set with a geohash score; radius and box queries in O(log n).
- Typical roles in one stack: cache in front of Postgres, idempotency and lock keys, rate limiter, session store, lightweight message queue or stream, pub/sub bus. For durable, replayable, high-volume streams it is not a replacement for [[kafka-architecture|Kafka]].


### Internals
- **One command thread** runs an event loop over all client sockets (epoll, [[socket-management]]): every command is atomic with no locks, and one slow command (`KEYS *`, `HGETALL` on a huge hash, a big Lua script) stalls every client. Since 6.0, optional I/O threads parse and write sockets; execution is still single-threaded.
- The keyspace is one big **hash table** with incremental rehashing: when it grows, each command moves a few buckets so there is no stop-the-world resize.
- Each type has compact and full **encodings**, switched automatically by size: small hashes, lists and sorted sets are a **listpack** (one contiguous block, cache friendly); bigger ones become a hash table, a **quicklist** (linked list of listpacks), or a **skiplist + hash table** for sorted sets (O(log n) by score, O(1) by member). Small sets of integers are an **intset**. `OBJECT ENCODING key` shows which.
- Memory is everything: values, keys and per-key overhead (~50–70 bytes) must fit in RAM; `maxmemory` + an eviction policy (`allkeys-lru`, `allkeys-lfu`, `volatile-ttl`) decide what goes when it is full.
- Persistence (RDB snapshots via fork + copy-on-write, AOF log) and its trade-offs: [[redis-patterns-and-persistence]], [[wal-redo-undo-and-durability]].
- Replication is asynchronous (a replica can lag and lose acknowledged writes on failover); Redis Cluster splits keys into 16,384 hash slots across primaries; multi-key commands work only when all keys share a slot (use `{hash tags}`).
- Compared with Memcached: typed values and persistence vs a simpler multithreaded string cache ([[memcached-architecture]]).
## Example
```java
// idempotency key with 24h TTL, see idempotency note
Boolean first = redis.opsForValue().setIfAbsent("idem:" + requestId, "1", Duration.ofHours(24));
if (!Boolean.TRUE.equals(first)) return cachedResponse(requestId);
```

## Pitfalls
- `KEYS *` blocks the server; use `SCAN`.
- Big values (> 1 MB) and hot keys serialize everything behind them.
- Related: [[idempotency]], [[spring-cache-abstraction]]
