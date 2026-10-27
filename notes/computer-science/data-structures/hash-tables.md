---
title: Hash tables
tags: [data-structures, hashing, hash-table, collisions, complexity]
learned: 2026-10-27
---
## TL;DR
A hash table maps a key to an array slot by `index = hash(key) mod capacity`, giving O(1) average `get`/`put`/`remove`. Two keys can land in the same slot (a collision); the table either chains them in a list per bucket (Java `HashMap`) or probes for a free neighbouring slot (open addressing: CPython dicts, Rust's `HashMap`, Go maps). The **load factor** (entries / capacity) controls how often collisions happen; when it exceeds a threshold the table doubles and rehashes everything. The structure is in this note; the JDK-specific details (treeified buckets, `hash ^ (hash >>> 16)`, resize split) are in [[hashmap-internals]].

## Key points

### How a lookup works
1. Compute `h = hash(key)` (the `hashCode()` in Java, see [[equals-and-hashcode]]).
2. Reduce to a slot: `h & (capacity - 1)` when capacity is a power of two (same as `mod`, but one instruction).
3. Compare keys in that slot by `equals` until a match or the end of the chain/probe sequence.

The hash function must be fast, deterministic and spread keys uniformly across bits; a bad one (all keys to one bucket) degrades the table to O(n).

### Collision resolution
| Strategy | Layout | Delete | Cache behaviour | Used by |
|---|---|---|---|---|
| **Separate chaining** | each bucket is a linked list (or tree) of entries | unlink the node | one pointer hop per probe; entries are separate objects | Java `HashMap`, `ConcurrentHashMap` |
| **Open addressing, linear probing** | entries live in the array; on collision try `i+1, i+2, ...` | needs tombstones or backward-shift | excellent (sequential), suffers from primary clustering | Go, Rust (Swiss tables), Python dict |
| **Quadratic / double hashing** | probe step grows or depends on a second hash | tombstones | fewer clusters, more cache misses | some C++ libs |
| **Robin Hood hashing** | linear probing where rich entries yield to poor ones | backward shift | low variance of probe length | Rust pre-2018, many caches |
| **Cuckoo hashing** | two tables/two hashes, evict and relocate | simple | O(1) worst-case lookup, expensive inserts | hardware tables, some KV stores |

Chaining tolerates load factors above 1 and keeps inserts simple; open addressing packs more into cache lines but must stay below ~0.7–0.9 load.

### Load factor and resize
- Java default: capacity 16, load factor 0.75, resize at 13 entries, capacity doubles. Each resize is O(n) but amortized O(1) per insert, like a dynamic array ([[arrays-and-dynamic-arrays]]).
- Expected chain length with chaining is the load factor itself; with linear probing the expected probes grow as `1/(1-α)²`, which explodes near α = 1.
- Size the table up front when you know n: `HashMap.newHashMap(n)` (Java 19) or `new HashMap<>((int)(n / 0.75) + 1)` avoids repeated rehashing.

### Complexity
| Operation | Average | Worst case | Note |
|---|---|---|---|
| `get` / `put` / `remove` | O(1) | O(n) (all keys collide) | Java bounds the worst case to O(log n) by treeifying long chains |
| Iteration | O(capacity + n) | — | iterating a huge, mostly empty table is slow; order is not meaningful |
| Resize | O(n) | O(n) | amortized away over inserts |

### Hash sets, ordered variants and friends
- A hash **set** is a hash table with keys only (`HashSet` wraps a `HashMap`).
- **Insertion/access order** needs an extra doubly linked list threaded through the entries (`LinkedHashMap`), which is also the basis of an LRU cache ([[lru-cache-design]], [[linked-lists]]).
- Sorted keys are a different structure: a balanced tree, O(log n), `TreeMap` ([[trees-and-bst]]).
- Concurrent tables shard locks per bucket or use CAS on bucket heads ([[concurrent-collections]]).
- Distributed hash tables and partitioning by key hash are the same idea across machines ([[consistent-hashing]], [[sharding-strategies]]).

### Security: hash flooding
- An attacker who knows the hash function can craft thousands of colliding keys (HTTP parameter names, JSON keys) and turn O(1) into O(n) per request, a CPU DoS. Java's treeified buckets, randomized seeds in Python/Ruby/Go and SipHash in Rust exist because of this ([[owasp-top-10-for-backend]]).

## Example
A compact chaining hash map with power-of-two capacity and resize at 0.75 load, the structure every "implement a HashMap" interview question expects.

```java
public final class ChainingMap<K, V> {
    private static final class Entry<K, V> {
        final K key; V value; final int hash; Entry<K, V> next;
        Entry(K key, V value, int hash, Entry<K, V> next) {
            this.key = key; this.value = value; this.hash = hash; this.next = next;
        }
    }

    private Entry<K, V>[] buckets = newTable(16);
    private int size;

    @SuppressWarnings("unchecked")
    private static <K, V> Entry<K, V>[] newTable(int n) { return (Entry<K, V>[]) new Entry[n]; }

    private static int spread(Object key) {            // mix high bits into low bits
        int h = key.hashCode();
        return h ^ (h >>> 16);
    }

    public V get(K key) {
        int h = spread(key);
        for (Entry<K, V> e = buckets[h & (buckets.length - 1)]; e != null; e = e.next) {
            if (e.hash == h && e.key.equals(key)) return e.value;
        }
        return null;
    }

    public V put(K key, V value) {
        int h = spread(key), i = h & (buckets.length - 1);
        for (Entry<K, V> e = buckets[i]; e != null; e = e.next) {
            if (e.hash == h && e.key.equals(key)) { V old = e.value; e.value = value; return old; }
        }
        buckets[i] = new Entry<>(key, value, h, buckets[i]);   // prepend to chain
        if (++size > buckets.length * 3 / 4) resize();
        return null;
    }

    private void resize() {                            // O(n): rehash every entry
        Entry<K, V>[] old = buckets;
        buckets = newTable(old.length * 2);
        for (Entry<K, V> head : old) {
            for (Entry<K, V> e = head; e != null; ) {
                Entry<K, V> next = e.next;
                int i = e.hash & (buckets.length - 1);
                e.next = buckets[i]; buckets[i] = e;
                e = next;
            }
        }
    }

    public int size() { return size; }
}
```

## Pitfalls
- A mutable key: changing a field used in `hashCode` after insertion makes the entry unreachable (it sits in the wrong bucket). Use immutable keys ([[immutable-objects]]).
- Overriding `equals` without `hashCode`: equal keys land in different buckets, and the map keeps "duplicates".
- Relying on iteration order of a hash map: it changes on resize and across JDK versions. Use `LinkedHashMap` or sort explicitly.
- Hashing floating-point or `BigDecimal` values with different scales (`1.0` vs `1.00`) gives different hashes for "equal" amounts ([[numbers-bits-and-floating-point]]).
- Related: [[hashmap-internals]] for the JDK implementation, [[data-structures]] for the overview, [[bloom-filters-and-probabilistic]] for the approximate cousin that answers "maybe present" in a fraction of the memory.
