---
title: Core data structures and algorithms
tags: [data-structures, algorithms, big-o, trees, complexity]
learned: 2026-10-22
---
## TL;DR
A data structure is a way of organizing data so that the operations you need are cheap. The handful that matter daily: arrays (O(1) index), linked lists (O(1) insert at a known node), hash tables (O(1) average lookup), balanced trees (O(log n) ordered operations), stacks (LIFO), queues and deques (FIFO), heaps (priority), graphs. Knowing the complexity table is what lets you pick the right `java.util` class ([[collections-framework]]).

## Key points
### Linear structures
| Structure | Access by index | Search | Insert/delete | Java |
|---|---|---|---|---|
| Array | O(1) | O(n), O(log n) if sorted | O(n) (shift), fixed size | `int[]`, `ArrayList` (resizable, ×1.5 growth) |
| Singly/doubly linked list | O(n) | O(n) | O(1) at a known node, O(n) to find it | `LinkedList` (doubly linked) |
| Stack (LIFO) | top only | – | push/pop O(1) | `ArrayDeque`; `Stack` is legacy |
| Queue (FIFO) | head only | – | offer/poll O(1) | `ArrayDeque`, `LinkedList`, `ArrayBlockingQueue` |
| Deque | both ends | – | O(1) both ends | `ArrayDeque` |

- Arrays are contiguous in memory, which makes index access O(1) and iteration cache-friendly; their size is fixed, so growing means allocating a new array and copying (what `ArrayList.add` does on resize, amortized O(1)).
- Linked lists spend an extra object and two references per element and are cache-hostile; in practice `ArrayList` beats `LinkedList` even for many insert-in-the-middle workloads because the shift is a `System.arraycopy`. Use a linked list when you hold iterators/nodes and splice (LRU caches, `LinkedHashMap`).
- A stack models recursion, undo, expression evaluation and DFS; a queue models buffering, BFS and producer–consumer ([[executors-and-thread-pools]]).

### Hash table
- Array of buckets indexed by `hash % capacity`; collisions by chaining (Java) or open addressing. Average O(1) for get/put/remove, O(n) worst case; Java 8 bounds the worst case with red-black-tree buckets. Needs a good `hashCode` ([[equals-and-hashcode]]). Details in [[hashmap-internals]].

### Trees
- **Tree**: hierarchical nodes with one parent and zero or more children; a root at the top, leaves at the bottom. Used for ordered data, hierarchies (file systems, DOM), indexes.
- **Binary tree**: at most two children per node.
- **Binary search tree (BST)**: left subtree < node < right subtree; search/insert/delete O(height), which degrades to O(n) when inserts arrive sorted.
- **Self-balancing BSTs** keep height O(log n):
  - **Red-black tree**: each node red or black; the root is black; red nodes have black children; every root-to-leaf path has the same number of black nodes (the null leaves count as black). At most 2·log₂(n+1) high; cheaper rebalancing than AVL on insert. Used by `TreeMap`/`TreeSet`, HashMap buckets, Linux's CFS scheduler.
  - **AVL tree**: stricter balance (heights differ by ≤ 1), faster reads, more rotations on writes.
- **B-tree / B+-tree**: wide nodes sized to a disk page; the structure behind database indexes ([[postgres-indexes]], [[database-indexes]]).
- **Heap**: complete binary tree in an array where parent ≤ children (min-heap); peek O(1), insert/extract O(log n). `PriorityQueue`; used for schedulers, top-k, Dijkstra.
- **Trie**: prefix tree over characters; autocomplete, routing tables.

### Graphs
- Nodes and edges, directed or not, weighted or not; stored as adjacency lists (sparse) or an adjacency matrix (dense). Dependency graphs (Maven, Spring beans), network topologies, state machines.

### Algorithms to know
| Algorithm | Complexity | Notes |
|---|---|---|
| Linear search | O(n) | unsorted data |
| Binary search | O(log n) | sorted array; `Arrays.binarySearch`, `Collections.binarySearch` |
| Bubble sort | O(n²) | teaching only |
| Merge sort | O(n log n), stable | `Arrays.sort(Object[])` uses TimSort, a merge/insertion hybrid |
| Quick sort | O(n log n) average, in place | `Arrays.sort(int[])` uses dual-pivot quicksort |
| Heap sort | O(n log n), in place, not stable | |
| BFS | O(V+E) | shortest path in unweighted graphs, level order; needs a queue |
| DFS | O(V+E) | reachability, cycle detection, topological sort; recursion or a stack |
| Tree traversals | O(n) | pre-order (node, left, right), in-order (sorted output for a BST), post-order (delete children first), level-order (BFS) |
| Dijkstra | O((V+E) log V) | shortest path with non-negative weights, heap-based |
| Greedy | varies | take the locally best choice; works when the problem has the greedy-choice property (interval scheduling, Huffman, fractional knapsack). The 0/1 knapsack is the standard counterexample: greedy fails and dynamic programming (O(n·W)) is needed |
| Dynamic programming | varies | overlapping subproblems + optimal substructure; memoization or bottom-up table |

## Example
```java
// BFS over a dependency graph (bean names -> dependencies) to find everything a bean transitively needs.
static Set<String> transitiveDeps(Map<String, List<String>> graph, String root) {
    Set<String> seen = new LinkedHashSet<>();          // insertion order = discovery order
    Deque<String> queue = new ArrayDeque<>();          // FIFO queue; ArrayDeque, not LinkedList
    queue.add(root);
    while (!queue.isEmpty()) {
        String bean = queue.poll();
        for (String dep : graph.getOrDefault(bean, List.of())) {
            if (seen.add(dep)) queue.add(dep);         // add returns false if already seen
        }
    }
    return seen;
}

// Top-k slowest requests with a bounded min-heap: O(n log k) instead of sorting everything.
static List<Long> slowest(LongStream latenciesMs, int k) {
    PriorityQueue<Long> heap = new PriorityQueue<>();   // min-heap: smallest of the top-k on top
    latenciesMs.forEach(v -> { heap.offer(v); if (heap.size() > k) heap.poll(); });
    return heap.stream().sorted(Comparator.reverseOrder()).toList();
}
```

## Pitfalls
- Big-O hides constants: for n under a few hundred, an array scan beats a hash lookup, and `ArrayList` beats `LinkedList` nearly always.
- A BST without balancing (hand-written or sorted input into a naive tree) silently degrades to a linked list.
- Recursive DFS on deep structures overflows the stack (default ~512 KB–1 MB per thread); use an explicit stack for anything beyond a few thousand levels.
- `PriorityQueue` iteration order is not sorted; only `poll()` returns elements in order.
- Related: [[collections-framework]], [[hashmap-internals]], [[iterators-and-fail-fast]], [[postgres-indexes]].
