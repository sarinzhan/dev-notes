---
title: Shortest paths with Dijkstra and friends
tags: [algorithms, graphs, dijkstra, shortest-path]
learned: 2026-10-27
---
## TL;DR
On an **unweighted** graph BFS already gives shortest paths. With **non-negative weights** use Dijkstra: a priority queue of `(distance, node)`, always settle the closest unsettled node, relax its edges; O((V + E) log V) with a binary heap. **Bellman-Ford** handles negative weights and detects negative cycles at O(V · E); **Floyd-Warshall** gives all-pairs in O(V³) for small dense graphs. Interview questions dress Dijkstra up as network delay, cheapest flight, minimum effort path or "path with maximum probability".

## Key points

### Picking the algorithm
| Situation | Algorithm | Time |
|---|---|---|
| all edges weight 1 | BFS ([[graph-traversal-bfs-dfs]]) | O(V + E) |
| weights 0 or 1 | 0-1 BFS with a deque | O(V + E) |
| non-negative weights, single source | Dijkstra with binary heap | O((V + E) log V) |
| negative edges allowed, or need negative-cycle detection | Bellman-Ford (or SPFA) | O(V · E) |
| DAG, any weights | DP over topological order ([[topological-sort-and-union-find]]) | O(V + E) |
| all pairs, V ≤ ~400 | Floyd-Warshall | O(V³) |
| at most k edges ("cheapest flight within k stops") | Bellman-Ford limited to k rounds, or BFS by layers | O(k · E) |
| maximise a product of probabilities | Dijkstra with a max-heap and multiplication, or Dijkstra on `-log p` | same as Dijkstra |

### Dijkstra, precisely
- `dist[source] = 0`, all others infinity. Push `(0, source)`.
- Pop the smallest `(d, u)`. If `d > dist[u]`, it is a stale entry: skip. Otherwise for every edge `u → v` with weight `w`: if `dist[u] + w < dist[v]`, set it and push `(dist[v], v)`.
- "Lazy deletion" (push duplicates, skip stale pops) is simpler than `decreaseKey` and what Java's `PriorityQueue` forces anyway, because it has no `decreaseKey` ([[heaps-and-priority-queues]]).
- Correctness relies on non-negative weights: once a node is popped with distance `d`, no later path can be shorter. A negative edge breaks this invariant, and that is why Dijkstra gives wrong answers, not just slow ones.
- Reconstruct the path with a `prev[]` array set during relaxation.
- Early exit: stop when the target is popped (not when it is first pushed).
- Dense graphs (E ≈ V²): the O(V²) array version without a heap is faster than the heap version.

### Bellman-Ford
- Repeat V − 1 times: relax every edge. If a V-th round still relaxes something, a negative cycle is reachable.
- Rounds correspond to "paths with at most i edges", which is exactly the structure needed for "within k stops" questions (copy `dist` before each round so updates in the same round do not chain).

### Where it appears in backend work
- Network routing (OSPF is Dijkstra, RIP is Bellman-Ford) ([[osi-and-tcp-ip-models]]).
- Choosing the cheapest roaming partner chain or the lowest-latency region path in [[multi-region-basics]].
- Service-mesh and load-balancer weighted routing is a one-hop special case ([[load-balancing]]).
- Maps, delivery-time estimation, and any "cost minimisation over a graph" product feature.

## Example
```java
// Dijkstra over a weighted directed graph: lowest total latency from `src` to every node.
// Graph as adjacency lists of (to, weightMs). Lazy deletion with PriorityQueue.
record Edge(int to, int weight) {}

static int[] lowestLatency(List<List<Edge>> graph, int src) {
    int n = graph.size();
    int[] dist = new int[n];
    Arrays.fill(dist, Integer.MAX_VALUE);
    dist[src] = 0;
    // entries: {distance, node}; PriorityQueue has no decreaseKey, so we push duplicates
    PriorityQueue<int[]> pq = new PriorityQueue<>(Comparator.comparingInt(e -> e[0]));
    pq.add(new int[]{0, src});
    while (!pq.isEmpty()) {
        int[] top = pq.poll();
        int d = top[0], u = top[1];
        if (d > dist[u]) continue;                       // stale entry, already settled cheaper
        for (Edge e : graph.get(u)) {
            int nd = d + e.weight();
            if (nd < dist[e.to()]) {
                dist[e.to()] = nd;
                pq.add(new int[]{nd, e.to()});
            }
        }
    }
    return dist;                                         // MAX_VALUE = unreachable
}
```
Complexity: each edge may push one heap entry, so O((V + E) log E), which simplifies to O((V + E) log V) because E ≤ V².

## Pitfalls
- Running Dijkstra with negative weights: the output is silently wrong, and no exception warns you.
- `dist[u] + w` overflowing `int` when `dist[u]` is `Integer.MAX_VALUE`; always check `dist[u] != MAX` first or use `long`.
- Forgetting the stale-entry check (`d > dist[u]`) makes the algorithm still correct but O(E²) on graphs with many relaxations.
- Marking a node as visited when it is pushed instead of when it is popped: the first push is not necessarily the shortest distance.
- Using Dijkstra for "fewest hops" on an unweighted graph: BFS is simpler and faster.
- Related: [[heaps-and-priority-queues]] for the heap behind it, [[graphs-representation]], [[greedy-algorithms]] (Dijkstra is a greedy algorithm whose greedy choice is provably safe), [[interview-problem-patterns]].
