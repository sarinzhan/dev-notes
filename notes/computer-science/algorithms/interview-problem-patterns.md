---
title: Interview problem patterns cheat sheet
tags: [algorithms, interview, patterns, cheat-sheet]
learned: 2026-10-27
---
## TL;DR
Almost every coding-interview problem is one of about twenty patterns wearing a costume. The skill being tested is reading the **signal** in the statement (sorted input, "contiguous", "k-th largest", "minimum number of steps", "all combinations") and mapping it to the pattern and its target complexity before writing code. This note is the lookup table; each row links to the note with the template and the pitfalls. Use it in the first two minutes of a problem, then say the pattern and the complexity out loud before coding.

## Key points

### Signal → pattern
| Signal in the problem statement | Pattern | Target complexity | Note |
|---|---|---|---|
| sorted array or list; pair/triple with a target sum; palindrome; "in place" | two pointers | O(n) after sort | [[two-pointers-and-sliding-window]] |
| "contiguous subarray / substring", "longest / shortest window with property", "at most k distinct" | sliding window | O(n) | [[two-pointers-and-sliding-window]] |
| subarray sum equals k with negatives; "sum of range [l, r]" many times; range updates then one query | prefix sum / difference array | O(n) | [[prefix-sums-and-intervals]] |
| intervals: merge, insert, overlap, "meeting rooms", peak concurrency | sort by start or end + scan / sweep line | O(n log n) | [[prefix-sums-and-intervals]] |
| sorted input and "find the first / last / minimum X such that..."; monotonic feasibility; rotated sorted array | binary search / search on the answer | O(log n), O(n log range) | [[binary-search-and-variants]] |
| linked list: middle, cycle, k-th from end, merge sorted | fast/slow pointers, dummy head | O(n), O(1) space | [[linked-lists]] |
| nested structure, "most recent unmatched", brackets, expression evaluation, undo | stack | O(n) | [[stack-and-monotonic-stack]] |
| "next greater / smaller element", "days until", histogram areas, trapping water | monotonic stack | O(n) | [[stack-and-monotonic-stack]] |
| sliding window max/min | monotonic deque | O(n) | [[stack-and-monotonic-stack]] |
| frequency, duplicates, "seen before", anagram groups, two-sum unsorted | hash map / set | O(n), O(n) space | [[hash-tables]] |
| "k-th largest", "top k", "k closest", merge k sorted lists, running median, task scheduling by priority | heap / priority queue | O(n log k) | [[heaps-and-priority-queues]] |
| "k most frequent" | hash map + heap or bucket sort | O(n log k) / O(n) | [[heaps-and-priority-queues]] |
| prefix search, autocomplete, word dictionary with wildcards, word search II | trie | O(L) per op | [[tries]] |
| tree: depth, path sum, diameter, LCA, invert, serialise | recursive DFS on trees | O(n) | [[trees-and-bst]] |
| tree: level order, zigzag, right side view, min depth | BFS by levels | O(n) | [[trees-and-bst]] |
| BST: validate, k-th smallest, range sum | in-order traversal (sorted) | O(n) or O(h + k) | [[trees-and-bst]] |
| grid or graph: shortest path in steps, "minimum moves", nearest X, rotting oranges | BFS (multi-source if several starts) | O(V + E) | [[graph-traversal-bfs-dfs]] |
| islands, connected regions, flood fill, reachability, all paths | DFS | O(V + E) | [[graph-traversal-bfs-dfs]] |
| "can all tasks finish", course schedule, build/dependency order, "alien dictionary" | topological sort (Kahn's) | O(V + E) | [[topological-sort-and-union-find]] |
| dynamic connectivity, "number of provinces", redundant edge, accounts merge | union-find | ~O(α(n)) per op | [[topological-sort-and-union-find]] |
| weighted shortest path, "cheapest", "minimum effort", "network delay" | Dijkstra (Bellman-Ford if negative or ≤ k edges) | O((V+E) log V) | [[shortest-paths-dijkstra]] |
| "all subsets / permutations / combinations", N-queens, Sudoku, "generate all valid..." | backtracking | O(2ⁿ) / O(n!) | [[recursion-and-backtracking]] |
| count ways, min/max cost over choices, "is it possible", two strings, knapsack, coins, LIS, LCS, grid paths | dynamic programming | O(n·k), O(n·m) | [[dynamic-programming]] |
| "maximum number of non-overlapping", jump game, gas station, assign/match after sort | greedy (prove with exchange argument) | O(n log n) | [[greedy-algorithms]] |
| anagrams, character counts, pattern matching, repeated substring | frequency array, KMP, rolling hash | O(n) | [[string-algorithms]] |
| "appears once, others twice", power of two, subsets of n ≤ 20, flags | XOR / bitmask | O(n), O(2ⁿ·n) | [[bit-manipulation]] |
| "O(1) get and put with eviction", "most recently used" | hash map + doubly linked list / `LinkedHashMap` | O(1) | [[lru-cache-design]] |
| "design a data structure that supports..." | combine two structures (map + heap, map + list, two heaps) | per op O(1) or O(log n) | [[data-structures]] |
| sort needed, custom order, stable multi-key | `Comparator`, counting sort for small ranges | O(n log n) / O(n + k) | [[sorting-algorithms]] |
| numbers 1..n, find missing/duplicate in O(1) space; matrix rotate/spiral | index-as-hash (cyclic sort), index arithmetic | O(n) | [[arrays-and-dynamic-arrays]] |
| queue with two stacks, circular buffer, "design hit counter" | deque / ring buffer | O(1) amortized | [[stacks-and-queues]] |
| "is it probably in the set", approximate counts at scale | Bloom filter, count-min, HyperLogLog | O(1), tiny memory | [[bloom-filters-and-probabilistic]] |

### Constraint → acceptable complexity
| n | Fastest acceptable | Typical patterns |
|---|---|---|
| ≤ 12 | O(n!) | permutations, backtracking |
| ≤ 20–25 | O(2ⁿ · n) | bitmask enumeration / DP |
| ≤ 500 | O(n³) | interval DP, Floyd-Warshall |
| ≤ 5 000 | O(n²) | 2D DP, nested loops |
| ≤ 10⁶ | O(n log n) | sort, heap, binary search, DP 1D |
| ≤ 10⁸ | O(n) | one pass, two pointers, prefix sums |
| larger / streaming | O(log n) or O(1) per element | hashing, probabilistic structures |
Details in [[big-o-and-complexity]].

### The five-minute routine
1. Restate the problem and the input/output types; ask about sizes, duplicates, sorted-ness, negative numbers, empty input.
2. Find the signal above and name the pattern and target complexity.
3. Give the brute force in one sentence and its complexity, then the improved idea.
4. Write the code using the pattern's template; keep helper functions small.
5. Trace one normal and one edge case (empty, single element, all equal, maximum size) and state time and space.

Interviewers probe *why* the pattern applies (monotonicity, non-negative weights, exchange argument, overlapping subproblems) and the Java details that leak into algorithm code: `ArrayDeque` over `Stack`, `Integer` identity comparisons, `int` overflow, `PriorityQueue` without `decreaseKey`, unspecified `HashMap` order ([[collections-framework]], [[hashmap-internals]]).

## Example
```java
// Routine applied to "k most frequent subscriber errors in the last hour":
// signal: "k most frequent" -> hash map counts + min-heap of size k -> O(n log k).
static List<String> topKErrors(List<String> errorCodes, int k) {
    Map<String, Integer> count = new HashMap<>();
    for (String code : errorCodes) count.merge(code, 1, Integer::sum);          // O(n)

    PriorityQueue<Map.Entry<String, Integer>> heap =
            new PriorityQueue<>(Map.Entry.comparingByValue());                  // min-heap on count
    for (var e : count.entrySet()) {
        heap.offer(e);
        if (heap.size() > k) heap.poll();                                       // evict the smallest, O(log k)
    }
    List<String> result = new ArrayList<>();
    while (!heap.isEmpty()) result.add(heap.poll().getKey());
    Collections.reverse(result);                                                // most frequent first
    return result;
}
```

## Pitfalls
- Jumping to code before naming the pattern; a wrong pattern costs more time than a minute of thinking.
- Choosing the pattern by surface words only: "subarray" with negative numbers is prefix sums, not sliding window; "shortest path" with weights is Dijkstra, not BFS.
- Forgetting to verify the pattern's precondition (sorted, monotonic, non-negative, DAG).
- Over-engineering: a `TreeMap` where a sort plus scan would do, a segment tree where a prefix sum suffices.
- Not stating complexity at the end; interviewers mark it even when the code is right.
- Related: [[data-structures]] for the structure-level complexity table, [[big-o-and-complexity]] for the constraints table, [[system-design-basics]] for the equivalent routine in design interviews.
