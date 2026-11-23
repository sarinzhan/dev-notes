---
title: Thread-safe collections
tags: [collections, concurrenthashmap, copyonwrite, skip-list, weakly-consistent]
learned: 2026-10-22
---
## TL;DR
`Collections.synchronizedX()` wrappers (JDK 1.2) lock the whole collection on every call and still leave iteration unsafe. `java.util.concurrent` (Java 5) replaces them with collections designed for concurrency: `ConcurrentHashMap` locks only the bucket it writes, `CopyOnWriteArrayList` copies on write and never blocks readers, `ConcurrentSkipListMap` keeps keys sorted without a global lock, and the queues are either lock-free or deliberately blocking. Their iterators never throw `ConcurrentModificationException`, but they may not see changes made during the loop.

## Key points
### Which one to use
| Single-threaded | Thread-safe | How it stays safe | Use when |
|---|---|---|---|
| `HashMap` | `ConcurrentHashMap` | CAS into empty buckets, lock per bucket | the default shared map, caches, counters |
| `HashSet` | `ConcurrentHashMap.newKeySet()` | same | a shared set |
| `TreeMap` / `TreeSet` | `ConcurrentSkipListMap` / `Set` | CAS on skip-list links | sorted keys, range queries |
| `ArrayList` | `CopyOnWriteArrayList` | copy the array on every write | read-mostly lists: listeners, config |
| `ArrayDeque` / `LinkedList` as a queue | `ConcurrentLinkedQueue` / `Deque` | lock-free CAS | non-blocking hand-off |
| (any queue) | `ArrayBlockingQueue`, `LinkedBlockingQueue`… | locks + conditions | producer/consumer, see [[blocking-queues]] |
| `Hashtable`, `Vector` | (legacy) | `synchronized` on every method | never in new code |

### Synchronized wrappers
- `synchronizedList / Set / Map / Collection` wrap the original; every method takes the same lock (the wrapper object).
- One global lock serializes everything, readers included.
- Iteration is not covered: you must hold the lock for the whole loop (`synchronized (list) { for (…) }`), otherwise the fail-fast iterator throws `ConcurrentModificationException` ([[iterators-and-fail-fast]]).
- Compound actions (`if (!contains) add`) are races unless you lock around them too.

### ConcurrentHashMap
- Reads never block; writes lock only one bucket. Java 7 used 16 segments with their own locks; Java 8+ dropped segments.
- Atomic compound operations: `putIfAbsent`, `computeIfAbsent`, `compute`, `merge`, `replace(k, old, new)`.
- No null keys or values: `get` returning null must mean "absent", since you cannot lock the map to check `containsKey` first.
- How `put`, resizing and `size()` work inside: [[concurrenthashmap-internals]].

### Copy-on-write
- `CopyOnWriteArrayList` / `CopyOnWriteArraySet`: every write (add, set, remove) locks, copies the backing array, changes the copy and publishes it through a volatile field. Readers use whatever array they got, so they never block.
- Iterators are **snapshots** of the array at the moment they were created: they never see later writes, and `iterator.remove()` throws `UnsupportedOperationException`.
- Each write is O(n) time and memory; `CopyOnWriteArraySet.contains` is O(n). Only for small, read-mostly data.

### Skip list: sorted without rotations
- `ConcurrentSkipListMap` / `ConcurrentSkipListSet` are the concurrent `TreeMap` / `TreeSet`, with the full `NavigableMap` API (`ceilingKey`, `headMap`, `pollFirstEntry`).
- A skip list is a sorted linked list plus "express lanes": each node gets a random number of index levels, so each higher level holds only a fraction of the nodes. Search starts on the top level and drops down, which gives O(log n) on average.
- Why not a concurrent red-black tree: one insert can recolor and rotate nodes all the way up to the root, which is hard to do without a big lock. A skip-list insert is a CAS on a few `next` links at the bottom level; index levels are added afterwards and never need rebalancing.
- `size()` walks the list, O(n).

### Weakly consistent iterators
- Iterators of `ConcurrentHashMap`, `ConcurrentSkipListMap`, `ConcurrentLinkedQueue` and the blocking queues are **weakly consistent**: they never throw `ConcurrentModificationException`, never return an element twice, and see every element that existed when the iterator was created and was not removed. Changes made during the loop may or may not show up.
- Same for `size()`, `isEmpty()`, `toArray()`: an estimate under concurrent writes, not a lock-protected snapshot.

## Example
```java
// a sorted, thread-safe price book: best bid without locking
ConcurrentNavigableMap<BigDecimal, Long> bids = new ConcurrentSkipListMap<>(Comparator.reverseOrder());
bids.merge(new BigDecimal("101.50"), 300L, Long::sum);   // atomic add to the level
Map.Entry<BigDecimal, Long> best = bids.firstEntry();   // highest price

// read-mostly listeners: iteration never blocks or throws
List<OrderListener> listeners = new CopyOnWriteArrayList<>();
void publish(Order o) { listeners.forEach(l -> l.onOrder(o)); }
```

## Pitfalls
- `if (!map.containsKey(k)) map.put(k, v)` is a race even on `ConcurrentHashMap`; use `putIfAbsent` / `computeIfAbsent`.
- `CopyOnWriteArrayList` in a hot write path copies the array on every write and will crush the GC.
- Making a collection thread-safe does not make the objects inside it thread-safe: a mutable value taken from a `ConcurrentHashMap` still needs its own synchronization.
- `Collections.unmodifiableList` is not thread-safe: it is a read-only view of a list that someone else may still be changing.
- Related: [[concurrenthashmap-internals]], [[blocking-queues]], how the underlying CAS works in [[locks-and-lock-free]].
