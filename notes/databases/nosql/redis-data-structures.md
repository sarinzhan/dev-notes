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
