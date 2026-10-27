---
title: Recursion and backtracking
tags: [algorithms, recursion, backtracking, call-stack]
learned: 2026-10-27
---
## TL;DR
Recursion solves a problem by solving a smaller instance of the same problem and trusting the call to return the right answer. Every recursive function needs a **base case**, a step that **shrinks the input**, and a bound on depth, because each call takes a stack frame. **Backtracking** is recursion over choices: pick an option, recurse, undo the pick. It enumerates subsets, permutations, combinations and constraint-satisfaction solutions (N-queens, Sudoku), and **pruning** (rejecting a partial choice early) is what makes it practical.

## Key points

### How a call works
- Each call pushes a frame (parameters, locals, return address) on the thread stack. The default Java thread stack is about 512 KB to 1 MB, roughly 10–20 thousand simple frames; deeper recursion throws `StackOverflowError` ([[stack-vs-heap-memory]], [[jvm-memory-areas]]).
- The JVM does **not** do tail-call optimization, so "tail recursive" Java still uses O(depth) stack. Convert deep recursion to an explicit `ArrayDeque` loop ([[stacks-and-queues]]); that is how production DFS over large graphs is written ([[graph-traversal-bfs-dfs]]).
- Recursion depth on linked structures: a recursive `toString` on a 100k-element list is a bug, not a style choice ([[linked-lists]]).

### Designing a recursive solution
1. Define exactly what `f(args)` returns.
2. Base case(s): the smallest input with a direct answer. Missing or wrong base case is the number one bug.
3. Recursive case: express `f(n)` through `f(smaller)`; assume the recursive call is correct (the "leap of faith").
4. Check that every path reduces towards the base case.
5. If subproblems repeat, memoize or switch to tabulation ([[dynamic-programming]]).

### Complexity of recursive code
| Shape | Recurrence | Result | Example |
|---|---|---|---|
| one call, input shrinks by 1 | T(n) = T(n−1) + O(1) | O(n) | list length, factorial |
| one call, input halves | T(n) = T(n/2) + O(1) | O(log n) | binary search ([[binary-search-and-variants]]) |
| two calls, halves, linear merge | T(n) = 2T(n/2) + O(n) | O(n log n) | merge sort ([[sorting-algorithms]]) |
| two calls, shrinks by 1 | T(n) = 2T(n−1) + O(1) | O(2ⁿ) | naive Fibonacci, all subsets |
| n calls per level, depth n | | O(n!) | permutations |

The master theorem is the general tool; the four rows above cover interview needs ([[big-o-and-complexity]]).

### Backtracking template
- State: the partial solution (a path list), the remaining choices, and often a `used[]` array.
- At each node: if the partial solution is complete, record a **copy**; otherwise for each candidate: check the constraint, add it, recurse, remove it.
- Pruning options: sort candidates and `break` once they exceed a target (combination sum), skip duplicates by `if (i > start && a[i] == a[i-1]) continue` on sorted input, keep the count of remaining items and stop when they cannot reach the goal.
- Output size bounds the running time: 2ⁿ subsets, n! permutations, C(n, k) combinations. Interviewers ask for this number, not for a tighter bound.

### Where it shows up at work
- Walking a tree of JSON/YAML config, directory trees, dependency graphs in Maven ([[maven]]).
- Generating test combinations (all plan/region/channel pairs) for parametrized tests.
- Retry trees in workflow engines: a parent activity reruns child activities; recursion depth equals nesting depth ([[temporal-concepts]]).

## Example
```java
// Backtracking: all combinations of tariff add-ons whose monthly price sums to exactly `budget`.
// Each add-on may be used at most once; input is sorted so we can prune.
record AddOn(String code, int price) {}

static List<List<String>> bundlesFor(List<AddOn> sorted, int budget) {
    List<List<String>> out = new ArrayList<>();
    backtrack(sorted, 0, budget, new ArrayDeque<>(), out);
    return out;
}

private static void backtrack(List<AddOn> items, int start, int remaining,
                              Deque<String> path, List<List<String>> out) {
    if (remaining == 0) {                          // base case: exact match
        out.add(new ArrayList<>(path));             // copy! path is mutated afterwards
        return;
    }
    for (int i = start; i < items.size(); i++) {
        AddOn a = items.get(i);
        if (a.price() > remaining) break;           // prune: sorted, nothing later fits
        if (i > start && a.price() == items.get(i - 1).price()
                      && a.code().equals(items.get(i - 1).code())) continue; // skip duplicates
        path.addLast(a.code());                     // choose
        backtrack(items, i + 1, remaining - a.price(), path, out);
        path.removeLast();                          // un-choose
    }
}
```
Time is bounded by the number of subsets explored after pruning, at most O(2ⁿ); space is O(n) for the path plus the output.

## Pitfalls
- Adding `path` itself to the result instead of a copy: every stored answer ends up empty after backtracking.
- Forgetting to undo a choice (`used[i] = false`, `removeLast`) so later branches see stale state.
- Recursing on a `String` with `substring` in a loop: O(n²) copying hides inside an "O(n)" algorithm.
- Exponential time where memoization would give polynomial: if two branches recompute the same `(index, remaining)`, this is a [[dynamic-programming]] problem in disguise.
- Deep recursion in request threads: a `StackOverflowError` is an `Error`, not an `Exception`, so generic `catch (Exception e)` does not handle it ([[exceptions]]).
- Related: [[graph-traversal-bfs-dfs]] (DFS is recursion over neighbours), [[trees-and-bst]] (recursive traversals), [[interview-problem-patterns]].
