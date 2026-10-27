---
title: Graph representations
tags: [data-structures, graphs, adjacency-list, adjacency-matrix]
learned: 2026-10-27
---
## TL;DR
A graph is a set of vertices V and edges E, possibly directed and possibly weighted. The representation you pick decides the cost of the two questions every graph algorithm asks: "who are the neighbours of v?" and "is there an edge u–v?". An **adjacency list** (per vertex, a list of neighbours) answers the first in O(deg v) and uses O(V + E) memory, which is right for the sparse graphs real systems have (dependency graphs, networks, workflows). An **adjacency matrix** answers the second in O(1) but costs O(V²) memory and is only worth it for dense or tiny graphs. The traversals themselves are in [[graph-traversal-bfs-dfs]].

## Key points

### Vocabulary
- **Directed** (edges have a direction: dependencies, HTTP calls, Kafka topic flows) vs **undirected** (friendship, network links).
- **Weighted** (cost, latency, distance) vs unweighted.
- **Degree** (in/out for directed), **path**, **cycle**, **connected component**, **DAG** (directed acyclic graph: build systems, Spring bean wiring, Temporal workflow steps, Airflow DAGs).
- **Sparse** means E is around V (most real graphs); **dense** means E approaches V².
- A tree is a connected acyclic undirected graph with V − 1 edges ([[trees-and-bst]]).

### Representations
| Representation | Memory | Neighbours of v | Edge u–v exists? | Add edge | Best for |
|---|---|---|---|---|---|
| **Adjacency list** `Map<V, List<V>>` or `List<List<Integer>>` | O(V + E) | O(deg v) | O(deg u) | O(1) | sparse graphs, BFS/DFS/Dijkstra, the default |
| Adjacency list with `Set` per vertex | O(V + E) | O(deg v) | O(1) average | O(1) | sparse + frequent edge checks |
| **Adjacency matrix** `boolean[V][V]` / `int[V][V]` | O(V²) | O(V) | O(1) | O(1) | dense graphs, V ≤ a few thousand, Floyd–Warshall, bitset tricks |
| **Edge list** `List<Edge(u, v, w)>` | O(E) | O(E) | O(E) | O(1) | Kruskal (sort edges), Bellman–Ford, input/output format |
| **CSR** (compressed sparse row: one `int[] offsets` and one `int[] neighbours`) | O(V + E), two arrays | O(deg v), contiguous | O(deg u) | rebuild | static large graphs, cache-friendly, analytics |
| Object graph (`Node` with `List<Node> edges`) | O(V + E) + object overhead | O(deg v) | O(deg) | O(1) | domain models, small graphs, readability |

### Choosing
- Interview default: adjacency list as `List<List<Integer>>` with vertices numbered 0..V−1 (or `Map<String, List<String>>` for named nodes). Memory O(V + E), traversal O(V + E).
- Weighted: `List<List<int[]>>` with `{to, weight}` pairs, or a small `record Edge(int to, int weight)`.
- Undirected: store each edge twice (u→v and v→u).
- Matrix when V is small and you need O(1) edge checks, transitive closure, or the "number of paths of length k" trick (matrix powers).
- Memory layout matters: `List<List<Integer>>` is lists of boxed references scattered across the heap; CSR arrays or `int[][]` are contiguous and several times faster for large graphs ([[cpu-caches-and-cache-lines]], [[arrays-and-dynamic-arrays]]).

### Graphs you already work with
- **Dependency graphs**: Maven modules, Spring bean definitions (a cycle is `BeanCurrentlyInCreationException`, [[spring-bean-lifecycle]]), Kafka topic lineage, microservice call graphs drawn from traces ([[distributed-tracing]]). Ordering them is topological sort ([[topological-sort-and-union-find]]).
- **State machines**: eSIM profile lifecycle (`RELEASED → DOWNLOADED → INSTALLED → ENABLED`), order status, workflow steps ([[temporal-concepts]]); the transitions table is an adjacency list.
- **Networks**: routers and links with latency weights, shortest paths ([[shortest-paths-dijkstra]]), service meshes.
- **Databases**: query plans are DAGs of operators ([[query-planning-and-execution]]); graph databases (Neo4j) store adjacency natively, relational ones model edges as a join table `(from_id, to_id)` and walk it with recursive CTEs ([[sql-vs-nosql-choosing]]).

### Memory estimate
Adjacency list of 1M vertices and 10M edges as `int` CSR: ~44 MB. As `List<List<Integer>>`: several hundred MB (boxed `Integer`, `ArrayList` overhead per vertex). This is why graph analytics libraries use primitive arrays.

## Example
A small directed weighted graph type with an adjacency list, used here to model a service call graph with per-hop latency; the shape Dijkstra and BFS consume.

```java
public final class DirectedGraph {
    public record Edge(int to, int weight) {}

    private final List<List<Edge>> adj;           // adj.get(u) = outgoing edges of u
    private final Map<String, Integer> index = new HashMap<>();
    private final List<String> names = new ArrayList<>();

    public DirectedGraph() { adj = new ArrayList<>(); }

    public int vertex(String name) {              // get-or-create a vertex id
        return index.computeIfAbsent(name, n -> {
            names.add(n);
            adj.add(new ArrayList<>());
            return adj.size() - 1;
        });
    }

    public void addEdge(String from, String to, int weight) {
        adj.get(vertex(from)).add(new Edge(vertex(to), weight));
        // undirected graph would also add the reverse edge
    }

    public List<Edge> neighbours(int v) { return adj.get(v); }
    public int size() { return adj.size(); }
    public String name(int v) { return names.get(v); }

    /** Reverse graph: who calls me? Needed for "blast radius" and reverse topological order. */
    public DirectedGraph reversed() {
        DirectedGraph r = new DirectedGraph();
        for (String n : names) r.vertex(n);
        for (int u = 0; u < adj.size(); u++)
            for (Edge e : adj.get(u)) r.adj.get(e.to()).add(new Edge(u, e.weight()));
        return r;
    }
}

// usage
var g = new DirectedGraph();
g.addEdge("api-gateway", "esim-service", 5);
g.addEdge("esim-service", "sm-dp-plus", 120);
g.addEdge("esim-service", "postgres", 2);
```

## Pitfalls
- Using an adjacency matrix for a sparse graph with 100k vertices: 10¹⁰ cells, out of memory before the first traversal.
- Forgetting the reverse edge in an undirected graph, or adding it in a directed one; half the bugs in graph problems are here.
- `List<List<Integer>>` with `list.remove(v)` removes by index, not by value, when `v` is an `int` ([[arrays-and-dynamic-arrays]] has the same trap).
- Mutable vertex objects used as `HashMap` keys for the adjacency map: changing them breaks lookups ([[hash-tables]]).
- Related: [[data-structures]] for the overview, [[graph-traversal-bfs-dfs]] for BFS/DFS over these structures, [[topological-sort-and-union-find]] for DAG ordering and connectivity.
