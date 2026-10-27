---
title: LRU cache design
tags: [data-structures, cache, lru, linked-hash-map, eviction]
learned: 2026-10-27
---
## TL;DR
An LRU (least recently used) cache holds at most N entries and evicts the one that was touched longest ago. The O(1) design is a **hash map for lookup plus a doubly linked list for recency**: every hit unlinks the node and moves it to the head, every miss inserts at the head and, when full, drops the tail. Java ships this as `LinkedHashMap` with `accessOrder = true` and an overridden `removeEldestEntry`. Production caches (Caffeine, Redis) use approximations of LRU/LFU because strict LRU is bad under scans and expensive to lock; the policy choice is covered in [[caching]].

## Key points

### Why hash map + doubly linked list
| Need | Structure | Cost |
|---|---|---|
| find an entry by key | hash map `key → node` ([[hash-tables]]) | O(1) |
| move a node to "most recent" | doubly linked list: unlink in O(1) because the node knows `prev` and `next` ([[linked-lists]]) | O(1) |
| find the eviction victim | the list tail | O(1) |
| evict | remove tail node, remove its key from the map | O(1) |

A singly linked list would need O(n) to find the predecessor; an array-backed deque would need O(n) to remove from the middle; a `TreeMap` by timestamp would be O(log n). Only the map + doubly linked list pair is O(1) everywhere.

### Operations
| Operation | Steps | Complexity |
|---|---|---|
| `get(k)` | map lookup; if hit, unlink node, link at head | O(1) |
| `put(k, v)` | if present: update value, move to head; else create node at head, map put; if `size > capacity`: unlink tail, map remove | O(1) |
| `remove(k)` | map remove, unlink node | O(1) |
| memory | per entry: map entry + list node (or one combined node) + key + value | O(capacity) |

Sentinel head and tail nodes remove every null check from the link/unlink code.

### The `LinkedHashMap` trick
- `new LinkedHashMap<>(capacity, 0.75f, true)`: the third argument switches the internal doubly linked list from insertion order to **access order**; `get` and `put` move the entry to the end.
- Override `protected boolean removeEldestEntry(Map.Entry<K,V> eldest)` to return `size() > capacity`; the map calls it after every insert and evicts the first (eldest) entry.
- This is exactly the map + list design, with the list threaded through the map entries themselves ([[hashmap-internals]]). Not thread-safe: wrap with `Collections.synchronizedMap` or a lock, or use Caffeine.

### Variants and policies
| Policy | Idea | Pros / cons |
|---|---|---|
| **LRU** | evict least recently used | simple, good temporal locality; a one-off scan of N+1 keys flushes the whole cache |
| **LFU** | evict least frequently used | resists scans; new entries die young, needs decay; O(1) LFU uses a map of frequency buckets |
| **LRU-K / 2Q / SLRU** | protect entries hit ≥ 2 times | scan-resistant; Postgres buffer manager uses a clock-sweep with usage counts |
| **Clock / second chance** | ring with a reference bit, no list moves | cheap approximation used by OS page caches ([[virtual-memory-and-paging]]) |
| **TinyLFU / W-TinyLFU** | admission filter (count-min sketch) + segmented LRU | Caffeine's default; best hit rates in practice ([[bloom-filters-and-probabilistic]]) |
| **Sampled LRU** | sample k random keys, evict the oldest | Redis `allkeys-lru`: no list, approximate, O(1) memory overhead ([[redis-patterns-and-persistence]]) |
| **TTL** | evict by expiry, not recency | orthogonal; combine with any of the above |

### Concurrency
- A global lock around get/put serializes every cache hit; fine for a per-request local cache, not for a hot shared one.
- `ConcurrentHashMap` + a lock-free structure for recency is hard (moving list nodes under contention); Caffeine buffers access events in ring buffers and applies them to the policy asynchronously, so reads are almost lock-free ([[concurrent-collections]], [[lock-free-and-cas]]).
- Spring's `@Cacheable` plugs into Caffeine or Redis rather than a hand-written LRU ([[spring-cache-abstraction]]); distributed invalidation is a separate problem ([[distributed-cache-coherence]]).

### Interview checklist
- State the two structures and why both are needed; write `get`/`put` with sentinels; mention `LinkedHashMap` as the JDK shortcut; mention thread safety and that strict LRU is scan-vulnerable; know that O(1) LFU exists.

## Example
The explicit map + doubly linked list implementation, plus the `LinkedHashMap` one-liner. Used here as a per-instance cache of decoded eSIM profiles keyed by ICCID.

```java
public final class LruCache<K, V> {
    private static final class Node<K, V> {
        K key; V value; Node<K, V> prev, next;
        Node(K key, V value) { this.key = key; this.value = value; }
    }

    private final int capacity;
    private final Map<K, Node<K, V>> index;
    private final Node<K, V> head = new Node<>(null, null);  // sentinel: most recent after head
    private final Node<K, V> tail = new Node<>(null, null);  // sentinel: least recent before tail

    public LruCache(int capacity) {
        this.capacity = capacity;
        this.index = HashMap.newHashMap(capacity);             // Java 19+: sized for capacity entries
        head.next = tail; tail.prev = head;
    }

    public V get(K key) {
        Node<K, V> n = index.get(key);
        if (n == null) return null;
        unlink(n); linkFirst(n);                               // touched: move to most recent
        return n.value;
    }

    public void put(K key, V value) {
        Node<K, V> n = index.get(key);
        if (n != null) { n.value = value; unlink(n); linkFirst(n); return; }
        n = new Node<>(key, value);
        index.put(key, n); linkFirst(n);
        if (index.size() > capacity) {
            Node<K, V> victim = tail.prev;                     // least recently used
            unlink(victim); index.remove(victim.key);
        }
    }

    private void linkFirst(Node<K, V> n) {
        n.next = head.next; n.prev = head;
        head.next.prev = n; head.next = n;
    }

    private void unlink(Node<K, V> n) {
        n.prev.next = n.next; n.next.prev = n.prev;
    }
}

// JDK shortcut: access-ordered LinkedHashMap with eviction hook
Map<String, Profile> cache = new LinkedHashMap<>(256, 0.75f, true) {
    @Override protected boolean removeEldestEntry(Map.Entry<String, Profile> eldest) {
        return size() > 256;
    }
};
```

## Pitfalls
- Forgetting to move the node on `get`: the cache degrades to FIFO and evicts hot entries.
- Forgetting to remove the evicted key from the map (or the node from the list): the two structures drift apart and memory leaks.
- Using `LinkedHashMap` with the default `accessOrder = false`: it evicts by insertion order (FIFO), not LRU.
- Exposing an unsynchronized LRU cache as a Spring singleton bean: concurrent `get` calls corrupt the list pointers. Use Caffeine (`Caffeine.newBuilder().maximumSize(n)`) in real services.
- Related: [[caching]] for strategies and invalidation, [[redis-patterns-and-persistence]] for distributed eviction policies, [[linked-lists]] and [[hash-tables]] for the two building blocks.
