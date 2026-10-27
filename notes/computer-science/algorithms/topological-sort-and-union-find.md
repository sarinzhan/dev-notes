---
title: Topological sort and union-find
tags: [algorithms, graphs, topological-sort, union-find]
learned: 2026-10-27
---
## TL;DR
**Topological sort** orders the nodes of a directed acyclic graph so that every edge goes from earlier to later: dependency resolution, build order, migration order, workflow step scheduling. Kahn's algorithm (BFS over in-degrees) also detects cycles for free: if fewer than V nodes come out, there is a cycle. **Union-find** (disjoint set union, DSU) tracks which elements belong to the same group with near-O(1) `find` and `union`; it answers connectivity questions on graphs whose edges arrive one by one and is the core of Kruskal's minimum spanning tree.

## Key points

### Topological sort
- Only defined on a DAG. Multiple valid orders usually exist; use a `PriorityQueue` instead of a queue when the "smallest first" order is required (lexicographically smallest schedule).
- **Kahn's algorithm**: compute in-degree of every node, enqueue all with in-degree 0, pop, append to the order, decrement each neighbour's in-degree, enqueue when it hits 0. O(V + E).
- **DFS version**: run DFS, push each node to a stack on finish; the stack popped is a topological order. Cycle detection needs the grey/black colouring ([[graph-traversal-bfs-dfs]]).
- Longest path in a DAG (critical path, "minimum semesters") is a DP over the topological order ([[dynamic-programming]]).
- Interview statements: course schedule, alien dictionary (order letters from sorted words), build systems, "can all tasks complete".
- At work: Flyway orders migrations by version number, but the dependencies between schema changes are a DAG you keep in your head ([[schema-migrations-flyway-liquibase]]); Spring resolves bean creation order through dependency edges and fails on cycles ([[spring-ioc-and-di]]); Temporal child workflows and activities with `await` form an execution DAG ([[temporal-concepts]]).

### Union-find
- Each element points to a parent; the root represents the set. `find(x)` follows parents to the root; `union(a, b)` links one root under the other.
- Two optimisations make it practically constant time:
  - **Path compression**: on `find`, point every visited node directly at the root.
  - **Union by rank/size**: attach the smaller tree under the larger.
  - Together: amortized O(α(n)) per operation, where α is the inverse Ackermann function, ≤ 4 for any realistic n ([[big-o-and-complexity]]).
- Uses: connected components when edges stream in, "number of provinces", redundant connection (the edge that closes a cycle in an undirected graph), accounts merge (same email → same person), Kruskal's MST (sort edges, union if endpoints are in different sets), percolation, equivalence classes of equations.
- Keep `size[]` per root to answer "how big is the component" and `count` of sets to answer "how many components" without a scan.
- It cannot delete edges and does not work for directed reachability; those need a traversal.

### Choosing
| Question | Tool |
|---|---|
| valid order given dependencies, or is there a cycle (directed) | topological sort |
| are a and b connected, how many components (undirected, edges added over time) | union-find |
| are a and b connected, static graph, one query | BFS/DFS |
| cycle in an undirected graph while adding edges | union-find (`find(a) == find(b)` before union) |
| minimum spanning tree | Kruskal with union-find, or Prim with a heap ([[heaps-and-priority-queues]]) |

### Complexity
| Operation | Time |
|---|---|
| Kahn's / DFS topo sort | O(V + E) |
| DSU `find` / `union` with both optimisations | amortized O(α(n)) ≈ O(1) |
| Kruskal | O(E log E) for the sort |

## Example
```java
// Kahn's topological sort over provisioning steps: step -> steps it depends on.
// Throws when the dependency graph has a cycle.
static List<String> executionOrder(Map<String, List<String>> dependsOn) {
    Map<String, Integer> inDegree = new HashMap<>();
    Map<String, List<String>> dependents = new HashMap<>();
    for (var e : dependsOn.entrySet()) {
        inDegree.putIfAbsent(e.getKey(), 0);
        for (String dep : e.getValue()) {
            inDegree.merge(e.getKey(), 1, Integer::sum);
            inDegree.putIfAbsent(dep, 0);
            dependents.computeIfAbsent(dep, k -> new ArrayList<>()).add(e.getKey());
        }
    }
    Deque<String> ready = new ArrayDeque<>();
    inDegree.forEach((step, d) -> { if (d == 0) ready.add(step); });
    List<String> order = new ArrayList<>();
    while (!ready.isEmpty()) {
        String step = ready.poll();
        order.add(step);
        for (String next : dependents.getOrDefault(step, List.of()))
            if (inDegree.merge(next, -1, Integer::sum) == 0) ready.add(next);
    }
    if (order.size() != inDegree.size()) throw new IllegalStateException("cycle in step dependencies");
    return order;
}

// Union-find with path compression and union by size.
final class DSU {
    private final int[] parent, size;
    DSU(int n) { parent = new int[n]; size = new int[n];
                 for (int i = 0; i < n; i++) { parent[i] = i; size[i] = 1; } }
    int find(int x) { return parent[x] == x ? x : (parent[x] = find(parent[x])); }
    boolean union(int a, int b) {
        a = find(a); b = find(b);
        if (a == b) return false;                       // already connected: this edge closes a cycle
        if (size[a] < size[b]) { int t = a; a = b; b = t; }
        parent[b] = a; size[a] += size[b];
        return true;
    }
}
```

## Pitfalls
- Building the graph in the wrong direction (`a depends on b` must become edge `b → a` for Kahn's order).
- Nodes that appear only as dependencies and never as keys get lost unless you register them with in-degree 0 (the `putIfAbsent` calls above).
- Union-find without path compression degrades to O(n) chains on adversarial input; with only one of the two optimisations it is still O(log n), which is fine, but say which you implemented.
- Recursive `find` on a 10⁷-element DSU before compression can overflow the stack; use the iterative two-pass version in production.
- Treating the topological order as unique; tests that assert a specific order break when `HashMap` iteration changes.
- Related: [[graph-traversal-bfs-dfs]] for the DFS-based variant, [[graphs-representation]], [[greedy-algorithms]] for Kruskal, [[interview-problem-patterns]].
