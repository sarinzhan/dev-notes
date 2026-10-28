---
title: Greedy algorithms
tags: [algorithms, greedy, intervals, optimization]
learned: 2026-10-27
---
## TL;DR
A greedy algorithm makes the **locally best choice at every step and never revisits it**. It is fast (usually one sort plus one pass, O(n log n)) but only correct when the problem has the **greedy-choice property**: some optimal solution contains the greedy choice, which you argue with an **exchange argument** ("swap the optimal first choice for the greedy one and nothing gets worse"). When that argument fails, the problem needs [[dynamic-programming]]. Interview classics: interval scheduling, jump game, gas station, assign cookies, Huffman coding, Dijkstra and Kruskal.

## Key points

### When greedy works
- The problem is an optimisation (max count, min cost) and a simple ordering rule exists under which an early decision never has to be undone.
- Test it: find a small counter-example before trusting the greedy. Coin change works greedily for `{1, 5, 10, 25}` but not for `{1, 3, 4}` (target 6: greedy gives 4+1+1, optimal is 3+3).
- Proof shape (exchange argument): take any optimal solution; if its first choice differs from the greedy choice, replace it with the greedy one; show the result is still feasible and no worse; repeat.

### Canonical greedy problems
| Problem | Greedy rule | Why it is safe |
|---|---|---|
| interval scheduling (max non-overlapping meetings) | sort by **end** time, take each interval that starts after the last taken end | the earliest-ending interval leaves the most room; exchange it into any optimal solution |
| minimum rooms / platforms | sweep line counting overlaps ([[prefix-sums-and-intervals]]) | counting, not choosing |
| minimum intervals to remove | same as scheduling, count the rest | |
| jump game (can reach the end) | track the farthest reachable index | reachability only grows |
| jump game II (min jumps) | BFS-like levels over reachable ranges | each level is one jump |
| gas station | if the total is non-negative, restart from the station after any failure | the prefix that failed cannot be part of the answer |
| assign cookies / two-array matching | sort both, two pointers ([[two-pointers-and-sliding-window]]) | smallest satisfiable first |
| task scheduler with cooldown | schedule the most frequent task first; max-heap | frequent tasks bound the length |
| Huffman coding | repeatedly merge the two least frequent symbols with a min-heap ([[heaps-and-priority-queues]]) | the two rarest symbols can always be siblings at the deepest level |
| fractional knapsack | sort by value/weight ratio | divisible items; 0/1 knapsack is **not** greedy |
| Dijkstra | settle the closest node ([[shortest-paths-dijkstra]]) | non-negative weights |
| Kruskal / Prim MST | add the cheapest edge that joins two components ([[topological-sort-and-union-find]]) | cut property |
| partition labels, merge to largest number, remove k digits | local rule after a sort or with a monotonic stack ([[stack-and-monotonic-stack]]) | |

### Greedy vs DP
| | Greedy | DP |
|---|---|---|
| decisions | made once, never revised | all options evaluated |
| requirement | greedy-choice property + optimal substructure | optimal substructure + overlapping subproblems |
| cost | O(n log n) typical | O(n · k) or worse |
| proof | exchange argument | recurrence correctness |
| when unsure | try greedy, look for a counter-example | fall back to DP |

### Where greedy shows up in systems
- Scheduling: shortest-job-first minimises average wait; earliest-deadline-first for real-time; both are greedy ([[process-management]]).
- Kafka's range and round-robin partition assignors, and sticky assignment minimising moves, are greedy heuristics ([[kafka-consumer]]).
- Bin packing of pods onto nodes in Kubernetes schedulers is greedy with scoring; optimal bin packing is NP-hard ([[kubernetes-basics]]).
- Huffman trees sit inside DEFLATE (gzip), so every compressed HTTP response is a greedy algorithm at work ([[http-fundamentals]]).
- Rate limiters and retries with exponential backoff are greedy in time: act as soon as allowed ([[rate-limiting]]).

## Example
```java
// Interval scheduling: the maximum number of non-overlapping maintenance windows that can
// be approved from a list of requests. Sort by end, keep what fits. O(n log n).
record Window(long start, long end) {}       // half-open [start, end)

static List<Window> approve(List<Window> requests) {
    List<Window> sorted = new ArrayList<>(requests);
    sorted.sort(Comparator.comparingLong(Window::end));      // the exchange argument needs end order
    List<Window> approved = new ArrayList<>();
    long lastEnd = Long.MIN_VALUE;
    for (Window w : sorted) {
        if (w.start() >= lastEnd) {                            // does not overlap the last approved
            approved.add(w);
            lastEnd = w.end();
        }
    }
    return approved;
}

// Counter-example check you should run in your head before trusting a greedy:
// windows [1,10), [2,3), [4,5): sort-by-start greedy approves 1 window, sort-by-end approves 2.
```

## Pitfalls
- Sorting by the wrong key: interval scheduling by start or by length gives wrong answers; the end time is the only safe key.
- Claiming greedy without a counter-example search. Interviewers ask "why is that optimal?" and expect the exchange argument, not "it seems right".
- Mixing up fractional knapsack (greedy) with 0/1 knapsack (DP).
- Greedy heuristics in production (schedulers, assignors) are fine as long as nobody expects them to be optimal; write that down in the design doc.
- A greedy solution that passes the sample tests but fails hidden ones is the most common wrong submission; when in doubt, DP first and optimise later.
- Related: [[dynamic-programming]], [[prefix-sums-and-intervals]], [[sorting-algorithms]] (almost every greedy starts with a sort), [[interview-problem-patterns]].
