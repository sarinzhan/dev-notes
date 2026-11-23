---
title: ConcurrentHashMap internals
tags: [concurrenthashmap, cas, resize, longadder, red-black-tree]
learned: 2026-11-23
---
## TL;DR
`ConcurrentHashMap` (Java 8+) is the same array of buckets as `HashMap` ([[hashmap-internals]]), made safe without a global lock. `get` never locks: it relies on volatile reads. `put` fills an empty bucket with a single CAS and locks only the head node of an occupied one, so threads writing different buckets never wait for each other. The size is counted with striped counters like `LongAdder`, and a resize is done by all writing threads together, bucket by bucket, while readers keep working.

## Key points
### Layout
- `volatile Node[] table`, created lazily on the first insert. A `Node` holds the hash, key, `volatile` value and `volatile next`.
- The hash is `(h ^ h >>> 16) & 0x7fffffff`: high bits mixed in, sign bit cleared. Negative hashes are reserved for special nodes: `MOVED = -1` (a `ForwardingNode` during a resize) and `TREEBIN = -2` (the head of a tree bucket).
- `sizeCtl` is one `volatile int` with several jobs: `-1` while the table is being created, another negative value while a resize is running (it encodes how many threads are helping), otherwise the next resize threshold, `0.75 × capacity`.
- The load factor is always 0.75. The `loadFactor` and `concurrencyLevel` constructor arguments only change the initial size.

### get: no lock
- Read the bucket with a volatile read. If the first node matches, return its value.
- A negative hash means a special node, which knows how to search: a `ForwardingNode` looks in the new table, a `TreeBin` searches its tree.
- Otherwise walk the `next` chain. Because `value` and `next` are volatile, a reader sees fully published nodes.

### put step by step
1. Compute the hash and the index `hash & (n − 1)`.
2. **Empty bucket:** CAS `null → new Node`. No lock at all. If the CAS fails, another thread got there first; loop again.
3. **`ForwardingNode`** (a resize is moving this bucket): call `helpTransfer` and join the resize, then retry.
4. **Occupied bucket:** `synchronized` on the **head node**, re-check that it is still the head, then replace the value or append to the chain (or insert into the tree).
5. If the chain reached 8 nodes, call `treeifyBin`: the bucket becomes a red-black tree, but only when the table has at least 64 buckets; a smaller table is resized instead.
6. `addCount(+1)`, which may start a resize.

### Counting without a hot spot
- `addCount` first tries a CAS on `baseCount`. When threads collide, it switches to `CounterCell[]`: each thread updates its own cell (picked by a per-thread probe value), exactly like `LongAdder`.
- `size()` = `baseCount` + the sum of all cells. Under concurrent writes it is an estimate. `mappingCount()` returns a `long` for maps with more than `Integer.MAX_VALUE` entries.

### Resize: everyone helps
- When the count reaches `sizeCtl`, one thread creates `nextTable` with twice the capacity.
- Threads claim ranges of buckets (at least 16 at a time) starting from the end of the old table. For each bucket they lock the head and split the chain in two: nodes with `(hash & n) == 0` stay at index `i`, the others go to `i + n`. No hash is recomputed.
- A moved bucket is replaced with a `ForwardingNode`. A writer that hits it helps with the resize; a reader follows it to the new table, so `get` keeps working during the whole resize.
- When the last range is done, `table = nextTable` and `sizeCtl = 0.75 × 2n`.

### Tree buckets
- A tree bucket's head is a `TreeBin` that owns the red-black tree. Writers lock the `TreeBin`. Readers never wait: if a writer is restructuring the tree, they walk the same nodes as a linked list (tree nodes also keep `next` links). When a resize splits a tree bucket and one half has 6 nodes or fewer, that half turns back into a list.

### Bulk operations
- `forEach`, `search`, `reduce` (and `…Keys`, `…Values`, `…Entries` variants) take a `parallelismThreshold`: below that estimated size they run sequentially, above it in the common `ForkJoinPool` ([[fork-join-framework]]). `Long.MAX_VALUE` means always sequential, `1` means as parallel as possible.
- They see a weakly consistent view, so do not use them where you need an exact snapshot.

```anim
chm-put-resize
```

## Example
```java
ConcurrentHashMap<String, LongAdder> hits = new ConcurrentHashMap<>();

void record(String path) {
    hits.computeIfAbsent(path, k -> new LongAdder()).increment();   // lock only on first creation
}

// top page, searched in parallel once the map has more than 10 000 entries
Map.Entry<String, Long> top = hits.reduceEntries(10_000,
        e -> Map.entry(e.getKey(), e.getValue().sum()),
        (a, b) -> a.getValue() >= b.getValue() ? a : b);
```

## Pitfalls
- `computeIfAbsent` holds the bucket lock while the function runs. A slow function blocks every writer of that bucket, and updating the same map inside it throws `IllegalStateException: Recursive update` (Java 9+) or can hang forever on Java 8.
- `hits.get(k) + 1` followed by `put` loses updates; use `merge(k, 1L, Long::sum)` or a `LongAdder` value.
- Keys with a bad `hashCode` still pile into one bucket and one lock; the tree only limits the damage to O(log n).
- `size()` in a hot loop sums all counter cells every time; keep it out of tight loops.
- Related: [[concurrent-collections]], [[hashmap-internals]], CAS and `LongAdder` in [[locks-and-lock-free]].
