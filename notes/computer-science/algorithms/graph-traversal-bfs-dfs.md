---
title: Graph traversal with BFS and DFS
tags: [algorithms, graphs, bfs, dfs]
learned: 2026-10-27
---
## TL;DR
BFS explores a graph level by level with a queue and gives the **shortest path in number of edges** on unweighted graphs. DFS goes as deep as possible with a stack (or recursion) and is the tool for **connectivity, cycle detection, topological order and path enumeration**. Both are O(V + E) with an adjacency list and both need a `visited` set, otherwise cycles loop forever. Representation trade-offs are in [[graphs-representation]]; this note is about the two traversals and the problems built on them.

## Key points

### Representation reminder
- Adjacency list `List<List<Integer>>` or `Map<Node, List<Node>>`: O(V + E) memory, iterate neighbours cheaply; the default.
- Adjacency matrix `boolean[V][V]`: O(V²) memory, O(1) edge test; only for dense or tiny graphs.
- Implicit graphs: a grid where neighbours are the four cells around, a state space (word ladder, puzzle positions), a build dependency tree ([[maven]]). You never materialise them, you compute neighbours on the fly.

### BFS
- Queue of frontier nodes; mark visited **when enqueuing**, not when dequeuing, or nodes get added many times.
- Distance tracking: either store `(node, dist)` pairs or process the queue level by level (`int size = q.size(); for (size) {...}`).
- Multi-source BFS: enqueue all sources with distance 0 first (rotting oranges, nearest hospital). Same cost as a single source.
- 0-1 BFS: with edge weights of only 0 and 1, use a deque and push 0-edges to the front; it replaces Dijkstra ([[shortest-paths-dijkstra]]).
- Bidirectional BFS halves the exponent for huge implicit graphs (word ladder).

### DFS
- Recursive version is shortest to write; iterative with `ArrayDeque` for graphs deeper than a few thousand nodes ([[recursion-and-backtracking]]).
- Three-colour marking for **cycle detection in a directed graph**: white = unvisited, grey = on the current path, black = done. An edge to a grey node is a back edge, i.e. a cycle. For undirected graphs, a visited neighbour that is not the parent means a cycle.
- Post-order (finish) times give a topological order when reversed ([[topological-sort-and-union-find]]).
- Connected components: run DFS/BFS from every unvisited node, count starts. Union-find is the alternative when edges arrive incrementally.
- Grid flood fill ("number of islands") is DFS on an implicit graph; recursion on a 1000×1000 grid overflows the stack, use iterative.

### Choosing
| Need | Use |
|---|---|
| shortest path, unweighted | BFS |
| shortest path, weighted non-negative | Dijkstra ([[shortest-paths-dijkstra]]) |
| any path, reachability, components | DFS or BFS |
| cycle detection, topological order | DFS (or Kahn's BFS for topo) |
| all paths / enumeration with constraints | DFS with backtracking |
| level-aware answers (minimum moves, nearest X) | BFS |
| memory-limited on wide graphs | DFS (O(depth)) rather than BFS (O(width)) |

### Complexity
| Traversal | Time | Space |
|---|---|---|
| BFS, adjacency list | O(V + E) | O(V) queue + visited |
| DFS, adjacency list | O(V + E) | O(V) visited + O(depth) stack |
| either, adjacency matrix | O(V²) | O(V) |

### Where it appears at work
- Dependency resolution and "which services are affected if X goes down" in a service graph ([[api-gateway-and-service-discovery]]).
- Workflow DAG validation before scheduling ([[temporal-concepts]], [[background-jobs-and-schedulers]]).
- Garbage collection marks reachable objects with a traversal from GC roots ([[garbage-collection]]).
- Crawlers, permission inheritance trees, org charts, "friends of friends" in [[design-news-feed]].

## Example
```java
// Shortest number of hops between two services in a call graph (unweighted), BFS.
// Returns -1 when unreachable. Graph: service -> list of services it calls.
static int hops(Map<String, List<String>> calls, String from, String to) {
    if (from.equals(to)) return 0;
    Deque<String> queue = new ArrayDeque<>();
    Map<String, Integer> dist = new HashMap<>();      // doubles as the visited set
    queue.add(from);
    dist.put(from, 0);
    while (!queue.isEmpty()) {
        String cur = queue.poll();
        for (String next : calls.getOrDefault(cur, List.of())) {
            if (dist.containsKey(next)) continue;     // mark on enqueue, not on dequeue
            dist.put(next, dist.get(cur) + 1);
            if (next.equals(to)) return dist.get(next);
            queue.add(next);
        }
    }
    return -1;
}

// Directed cycle detection with three colours, DFS. 0 = white, 1 = grey (in progress), 2 = black.
static boolean hasCycle(Map<String, List<String>> g) {
    Map<String, Integer> color = new HashMap<>();
    for (String v : g.keySet())
        if (color.getOrDefault(v, 0) == 0 && dfs(g, v, color)) return true;
    return false;
}
private static boolean dfs(Map<String, List<String>> g, String v, Map<String, Integer> color) {
    color.put(v, 1);
    for (String w : g.getOrDefault(v, List.of())) {
        int c = color.getOrDefault(w, 0);
        if (c == 1 || (c == 0 && dfs(g, w, color))) return true;   // grey neighbour = back edge
    }
    color.put(v, 2);
    return false;
}
```

## Pitfalls
- Marking visited on dequeue: the same node is enqueued once per incoming edge, and on dense graphs the queue explodes to O(E).
- Using BFS distance on a weighted graph; the first time you reach a node is not the cheapest.
- Recursive DFS on large inputs: `StackOverflowError` around 10–20k depth with default thread stacks ([[jvm-memory-areas]]).
- Cycle detection in a directed graph with only a `visited` boolean: a visited black node is not a cycle; you need the "on current path" state.
- Mutating a `List` of neighbours while iterating it in DFS throws `ConcurrentModificationException` ([[iterators-and-fail-fast]]).
- Related: [[graphs-representation]], [[topological-sort-and-union-find]], [[trees-and-bst]] (tree traversals are DFS/BFS without a visited set), [[interview-problem-patterns]].
