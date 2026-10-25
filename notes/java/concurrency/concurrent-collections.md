---
title: Thread-safe collections
tags: [collections, concurrenthashmap, copyonwrite, blockingqueue]
learned: 2026-10-22
---
## TL;DR
`Collections.synchronizedX()` wrappers (JDK 1.2) lock the whole collection on every call and still leave iteration unsafe. `java.util.concurrent` (Java 5) replaces them with collections designed for concurrency: `ConcurrentHashMap` locks only the bucket it writes, `CopyOnWriteArrayList` copies on write and never blocks readers, and the queues are either lock-free or deliberately blocking.

## Key points
### Synchronized wrappers
- `synchronizedList / Set / Map / Collection` wrap the original; every method takes the same lock.
- Drawbacks: one global lock serializes everything, and iterating is not safe unless you also lock manually around the loop; otherwise `ConcurrentModificationException`.

### ConcurrentHashMap
- Reads never block; writes lock only a small part.
- Java 7: the map was split into segments, each a small independent hash map with its own lock.
- Java 8+: no segments. `put` computes the hash and the bucket index; an empty bucket is filled with a CAS; an occupied bucket is locked at the **node** level (`synchronized` on the head) and the entry is appended to the chain or inserted into the tree.
- Atomic compound operations: `putIfAbsent`, `computeIfAbsent`, `merge`. `size()` is an estimate under concurrent writes.
- Null keys and values are not allowed (unlike `HashMap`).

### Copy-on-write
- `CopyOnWriteArrayList` / `CopyOnWriteArraySet`: every write (add, set, remove) locks, copies the backing array, modifies the copy and atomically swaps it in. Readers and iterators use the old array, so they never block and never throw `ConcurrentModificationException`.
- Only for read-mostly data (listeners, config lists). Each write is O(n).

### Queues
- `ConcurrentLinkedQueue` / `ConcurrentLinkedDeque`: non-blocking, CAS-based linked structures.
- `BlockingQueue` / `BlockingDeque` (`ArrayBlockingQueue`, `LinkedBlockingQueue`, `PriorityBlockingQueue`, `SynchronousQueue`): `put` blocks when full, `take` blocks when empty. The producer/consumer building block, and what thread pools use internally ([[executors-and-thread-pools]]).

### Sorted
- `ConcurrentSkipListMap` / `ConcurrentSkipListSet`: the concurrent counterparts of `TreeMap` / `TreeSet`, implemented as skip lists.

## Example
```java
ConcurrentMap<String, LongAdder> hits = new ConcurrentHashMap<>();
void record(String path) {
    hits.computeIfAbsent(path, k -> new LongAdder()).increment();   // atomic get-or-create
}
```

## Pitfalls
- `if (!map.containsKey(k)) map.put(k, v)` is a race even on `ConcurrentHashMap`; use `putIfAbsent` / `computeIfAbsent`.
- Long-running work inside `computeIfAbsent` blocks the bucket and can deadlock if it touches the same map.
- `CopyOnWriteArrayList` in a hot write path will crush the GC.
- How the underlying CAS works: [[locks-and-lock-free]].
