---
title: Heaps and priority queues
tags: [data-structures, heap, priority-queue, top-k, complexity]
learned: 2026-10-27
---
## TL;DR
A priority queue hands back the smallest (or largest) element first, regardless of insertion order. The standard implementation is a **binary heap**: a complete binary tree stored in a plain array, where every parent is ≤ its children (min-heap). Peek is O(1), insert and extract-min are O(log n), and building a heap from n items is O(n). It is the structure behind `PriorityQueue`, timers and schedulers, Dijkstra, top-k queries and k-way merges. When you need to find or remove an arbitrary element, or iterate in order, it is the wrong structure; use a balanced tree ([[trees-and-bst]]).

## Key points

### Array layout
- Node `i` has children `2i + 1` and `2i + 2` and parent `(i - 1) / 2` (0-based). No pointers, no node objects: the whole heap is one contiguous array, which is cache-friendly ([[arrays-and-dynamic-arrays]]).
- The tree is **complete** (filled level by level, left to right), so the array has no holes and height is ⌊log₂ n⌋.
- A heap is **not sorted**: only the root is guaranteed to be the extreme. Iterating the array gives an arbitrary order.

### Operations
| Operation | Complexity | How |
|---|---|---|
| `peek` | O(1) | `a[0]` |
| `offer` (insert) | O(log n) | append at the end, **sift up** while smaller than parent |
| `poll` (extract-min) | O(log n) | move last element to root, **sift down** swapping with the smaller child |
| build from n elements (heapify) | O(n) | sift down from the last internal node to the root; cheaper than n inserts (O(n log n)) |
| `remove(Object)` / `contains` | O(n) | linear scan; `PriorityQueue` has no index |
| decrease-key | O(log n) with an index map | needed by textbook Dijkstra; in Java you re-insert instead ([[shortest-paths-dijkstra]]) |
| heap sort | O(n log n), in place, not stable | heapify, then repeatedly swap root to the end and sift down ([[sorting-algorithms]]) |

### Variants
| Heap | Insert | Extract-min | Merge | Where |
|---|---|---|---|---|
| Binary | O(log n) | O(log n) | O(n) | `PriorityQueue`, almost everything |
| d-ary (d = 4) | O(log_d n) | O(d log_d n) | — | fewer cache misses on sift-down; used in some schedulers |
| Binomial / Fibonacci | O(1) amortized | O(log n) | O(log n) / O(1) | theoretical Dijkstra bound; rarely used in practice |
| Pairing | O(1) | O(log n) amortized | O(1) | practical mergeable heap |
| Hierarchical timing wheel | O(1) | O(1) per tick | — | Kafka purgatory, Netty timers: many timeouts with coarse resolution |

### Classic uses
- **Top-k of a stream**: keep a min-heap of size k; push each item, pop when the size exceeds k. O(n log k) time and O(k) memory instead of sorting everything. Slowest requests, largest files, most frequent words (with a frequency map).
- **K-way merge**: merge k sorted inputs (log segments, sorted partitions, Kafka compaction) with a heap of k heads: O(N log k).
- **Scheduling**: `ScheduledThreadPoolExecutor` keeps tasks in a `DelayedWorkQueue` (a binary heap ordered by trigger time); `DelayQueue` is the same for arbitrary delayed elements ([[executors-and-thread-pools]]). Temporal and cron-like schedulers conceptually do the same ([[background-jobs-and-schedulers]]).
- **Graph algorithms**: Dijkstra and Prim pull the closest unvisited vertex from a heap.
- **Running median**: a max-heap of the lower half and a min-heap of the upper half.
- **Rate limiters and load balancers**: pick the least-loaded backend from a heap keyed by current load ([[load-balancing]]).

### Java API
- `PriorityQueue<E>`: min-heap by natural order or a `Comparator`; `offer`/`poll`/`peek`; not thread-safe; iterator order is unspecified; `null` elements rejected. Max-heap: `new PriorityQueue<>(Comparator.reverseOrder())`.
- `PriorityBlockingQueue` is the thread-safe unbounded version ([[concurrent-collections]]). Everything else about the queue interfaces is in [[collections-framework]].

## Example
A compact binary min-heap of `long` values plus the top-k pattern on it. Interviewers accept `PriorityQueue` for the pattern, but like seeing sift-up/sift-down once.

```java
public final class LongMinHeap {
    private long[] a = new long[16];
    private int size;

    public void offer(long v) {
        if (size == a.length) a = Arrays.copyOf(a, size * 2);
        a[size] = v;
        siftUp(size++);
    }

    public long poll() {
        if (size == 0) throw new NoSuchElementException();
        long min = a[0];
        a[0] = a[--size];
        if (size > 0) siftDown(0);
        return min;
    }

    public long peek() { return a[0]; }
    public int size() { return size; }

    private void siftUp(int i) {
        while (i > 0) {
            int parent = (i - 1) >>> 1;
            if (a[parent] <= a[i]) break;
            swap(i, parent); i = parent;
        }
    }

    private void siftDown(int i) {
        while (true) {
            int l = 2 * i + 1, r = l + 1, smallest = i;
            if (l < size && a[l] < a[smallest]) smallest = l;
            if (r < size && a[r] < a[smallest]) smallest = r;
            if (smallest == i) return;
            swap(i, smallest); i = smallest;
        }
    }

    private void swap(int i, int j) { long t = a[i]; a[i] = a[j]; a[j] = t; }

    /** Top-k largest latencies in O(n log k): the heap holds the k largest seen so far. */
    static long[] topK(long[] latencies, int k) {
        LongMinHeap heap = new LongMinHeap();
        for (long v : latencies) {
            heap.offer(v);
            if (heap.size() > k) heap.poll();     // evict the smallest of the candidates
        }
        long[] out = new long[heap.size()];
        for (int i = out.length - 1; i >= 0; i--) out[i] = heap.poll(); // descending
        return out;
    }
}
```

## Pitfalls
- Treating `PriorityQueue` as sorted: `toString()`, `iterator()` and `stream()` give heap order, not priority order. Only repeated `poll()` is sorted.
- Using a max-heap of size n for top-k: O(n log n) and O(n) memory; the min-heap of size k is the right shape.
- Mutating an element's priority while it sits in the heap (changing a task's `deadline` field) silently breaks the invariant; remove, change, re-insert.
- `PriorityQueue.remove(Object)` is O(n); for "cancel a scheduled task" workloads keep a side map or use a tombstone flag and skip on poll.
- Related: [[data-structures]] for the overview, [[trees-and-bst]] for when you also need ordered iteration or arbitrary deletes, [[shortest-paths-dijkstra]] for the main graph use.
