---
title: Binary search and its variants
tags: [algorithms, binary-search, complexity]
learned: 2026-10-27
---
## TL;DR
Binary search halves a **monotonic** search space on every step: O(log n) instead of O(n). The classic form finds a value in a sorted array, but the useful interview form is the **predicate version**: find the first index where a boolean condition flips from false to true. That one template gives lower bound, upper bound, "search on the answer" (minimum capacity, smallest feasible time) and rotated-array search without rewriting the loop each time.

## Key points

### The invariant, not the code
- Keep a half-open range `[lo, hi)` where the answer is known to be inside, and a predicate `p(i)` that is false for a prefix and true for the rest (monotonic). Each step tests `mid` and moves either `lo` or `hi`; the loop ends when `lo == hi`, which is the first `true` index (or `n` if none).
- Everything else is choosing the predicate:

| Problem | Sorted array `a`, predicate `p(i)` | Result |
|---|---|---|
| lower bound (first `>= x`) | `a[i] >= x` | insertion point; equals `x` if present |
| upper bound (first `> x`) | `a[i] > x` | `upper - lower` = count of `x` |
| exact search | lower bound, then check `a[lo] == x` | index or "not found" |
| last `<= x` | upper bound − 1 | floor |
| rotated sorted array (no duplicates) | `a[i] <= a[n-1]` | index of the minimum; then normal search in the right half |
| peak element | `a[i] > a[i+1]` | some local maximum |

### Search on the answer
- When the question is "the minimum X such that something is feasible" and feasibility is monotonic in X (if capacity 10 works, 11 works too), binary search over X and write a linear `feasible(X)` check. Total O(n log range).
- Typical statements: ship packages within D days (capacity), Koko eating bananas (speed), split array into k parts minimizing the largest sum, minimum time for workers to finish, smallest batch size that keeps a Kafka consumer under its `max.poll.interval.ms` budget.

### Real-world appearances
- `Arrays.binarySearch` and `Collections.binarySearch` return `-(insertionPoint) - 1` when the key is absent; `TreeMap.ceilingKey/floorKey` are tree walks with the same semantics ([[trees-and-bst]]).
- Database B-tree indexes do a binary search inside each page and a tree descent between pages ([[database-indexes]]).
- Kafka consumers use binary search over the index file to find the file position for an offset ([[kafka-architecture]]).
- Git bisect: binary search over commits for the first bad one; the predicate "is this commit broken" must be monotonic, which is why flaky tests break bisect.
- Floating-point search (e.g. square root to 1e-9): loop a fixed number of iterations (100) instead of `while (lo < hi)`.

### Complexity
| Variant | Time | Space |
|---|---|---|
| Array search | O(log n) | O(1) |
| Search on the answer | O(n log range) | O(1) |
| Rotated array | O(log n) with distinct values, O(n) worst with duplicates | O(1) |

## Example
```java
// One template for every variant: first index in [lo, hi) where the predicate is true.
static int firstTrue(int lo, int hi, IntPredicate p) {
    while (lo < hi) {
        int mid = lo + (hi - lo) / 2;      // never (lo + hi) / 2: overflow near Integer.MAX_VALUE
        if (p.test(mid)) hi = mid;         // answer is mid or to the left
        else lo = mid + 1;                 // answer is strictly to the right
    }
    return lo;                             // == hi when nothing is true
}

static int lowerBound(int[] a, int x) { return firstTrue(0, a.length, i -> a[i] >= x); }
static int upperBound(int[] a, int x) { return firstTrue(0, a.length, i -> a[i] > x); }

// Search on the answer: smallest batch size so that n records fit into `maxBatches` batches
// where each batch may hold at most `limit` bytes. sizes[] are record sizes in order.
static int minBatchBytes(int[] sizes, int maxBatches) {
    int lo = Arrays.stream(sizes).max().orElse(0);           // must fit the largest record
    int hi = Arrays.stream(sizes).sum() + 1;                  // one batch always works
    return firstTrue(lo, hi, limit -> batchesNeeded(sizes, limit) <= maxBatches);
}

private static int batchesNeeded(int[] sizes, int limit) {   // greedy, O(n)
    int batches = 1, current = 0;
    for (int s : sizes) {
        if (current + s > limit) { batches++; current = 0; }
        current += s;
    }
    return batches;
}
```

## Pitfalls
- `mid = (lo + hi) / 2` overflows for arrays over 2³⁰ elements; the bug lived in the JDK for years. Use `lo + (hi - lo) / 2` or `(lo + hi) >>> 1`.
- Off-by-one from mixing closed `[lo, hi]` and half-open `[lo, hi)` conventions; pick one template and reuse it.
- Searching a non-monotonic predicate "works" on the sample and fails in production: prove monotonicity first.
- `Arrays.binarySearch` on an unsorted array returns garbage, not an error; on an array with duplicates it returns an arbitrary matching index, not the first.
- Infinite loop when `lo = mid` instead of `lo = mid + 1` in the else branch (with `hi - lo == 1` mid equals lo forever).
- Related: [[sorting-algorithms]] (sort once, then binary search many times beats repeated linear scans), [[two-pointers-and-sliding-window]] for the O(n) alternative on sorted data, [[interview-problem-patterns]].
