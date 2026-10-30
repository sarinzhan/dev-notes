---
title: Memcached architecture
tags: [memcached, caching, nosql, in-memory]
learned: 2026-10-30
---
## TL;DR
Memcached is a deliberately simple, multithreaded, in-memory **key-value cache**: strings in, strings out, no persistence, no replication, no data types, no clustering on the server side. Memory is managed by a **slab allocator** (fixed-size chunks grouped by size class) to avoid fragmentation, with a per-class **LRU** for eviction. Threads share one hash table protected by fine-grained locks. "Distribution" is done by the **client**, which hashes each key to one server with consistent hashing; servers do not know about each other. It is fast and predictable; Redis replaced it in most new systems because of richer types and persistence.

## Key points
### Data model
- Keys up to 250 bytes, values up to 1 MB by default (`-I` to raise). Commands: `get`, `set`, `add`, `replace`, `delete`, `incr/decr`, `cas` (check-and-set with a version token), `touch`.
- Every item has a TTL; expired items are removed lazily (on access) and by an LRU crawler.
- No persistence: a restart empties the cache. It is a cache, never the source of truth.

### Memory: slabs, pages, chunks
- Memory is split into 1 MB **pages**. Each page is assigned to a **slab class** and cut into equal **chunks** (class 1 = 96 B, class 2 = 120 B, ... growing by factor 1.25).
- An item goes into the smallest chunk that fits: a 100-byte item uses a 120-byte chunk. Wasted space per item, but **no fragmentation** and O(1) allocation.
- Once a page is given to a class it used to stay there: if your value sizes change, one class may be starved while another has free memory ("slab calcification"). Modern versions (1.4.25+) have a slab rebalancer and the **segmented LRU** (hot/warm/cold).
- Eviction is per slab class, LRU order. A full cache evicts the least recently used item **of that size class**, not globally.

### Threads
- Multithreaded with libevent: one listener thread accepts connections and hands them to N worker threads (`-t`, default 4); each worker runs its own event loop over its connections ([[socket-management]]).
- Items live in one shared hash table; locking is per hash bucket (item locks) plus a lock per LRU, so threads rarely contend.
- Compare: Redis executes commands on a single thread (I/O threads since 6.0), Memcached spreads commands across cores.

### LRU and reference counting
- Each slab class keeps a doubly linked LRU list ([[lru-cache-design]]). A `get` bumps the item (with throttling: at most once per 60 s, to avoid lock traffic).
- Items have a reference count: an item being sent to a client is not freed even if evicted meanwhile.

### Distribution (client side)
- Servers are independent. The client library hashes the key to a server: originally `hash(key) % N`, which remaps almost everything when N changes; now **consistent hashing** (ketama) so adding a node moves only ~1/N of the keys ([[consistent-hashing]]).
- No replication: a dead node = cache misses for its keys until the client removes it from the ring. Facebook's mcrouter adds pools, replication and failover as a proxy.
- Memcached can use UDP for `get` to save connection state at huge scale ([[udp-in-depth]]).

### Memcached vs Redis
| | Memcached | Redis |
|---|---|---|
| Types | strings | strings, hashes, lists, sets, sorted sets, streams… |
| Threads | multithreaded | single command thread |
| Persistence | none | RDB, AOF |
| Replication / cluster | client-side sharding only | replicas, Sentinel, Cluster |
| Memory | slab allocator, per-class LRU | jemalloc, approximated LRU/LFU |
| Use today | simple large caches with predictable values | almost everything else ([[redis-data-structures]]) |

## Example
```bash
docker run -d --name mc -p 11211:11211 memcached:1.6 -m 256 -t 4
printf 'set user:42 0 300 5\r\nalice\r\nget user:42\r\nquit\r\n' | nc localhost 11211
# STORED / VALUE user:42 0 5 / alice / END
printf 'stats slabs\r\nquit\r\n' | nc localhost 11211   # chunk_size, used_chunks per class
```

```java
// Spring: cache-aside with spymemcached-style client
User u = (User) mc.get("user:" + id);
if (u == null) { u = repo.findById(id).orElseThrow(); mc.set("user:" + id, 300, u); }
```

## Pitfalls
- Values much larger or smaller than the chunk sizes you planned: memory waste or calcification; check `stats slabs`.
- Using it as storage: every restart or eviction loses data.
- Modulo hashing in the client: adding one server invalidates nearly the whole cache (thundering herd on the database).
- Hot keys: one key on one node takes all the traffic; replicate it in the client or use a local cache tier ([[caching]]).
- Related: [[caching]], [[distributed-cache-coherence]], [[redis-patterns-and-persistence]].
