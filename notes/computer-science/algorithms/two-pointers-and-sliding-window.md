---
title: Two pointers and sliding window
tags: [algorithms, two-pointers, sliding-window, arrays]
learned: 2026-10-27
---
## TL;DR
Both patterns turn an O(n²) "look at every pair / every subarray" scan into O(n) by moving indices **monotonically**: they only ever advance, so the total work is bounded by the array length. **Two pointers** walk from both ends (or at two speeds) over sorted or linked data; **sliding window** keeps a contiguous range `[left, right]` plus a running summary (sum, counts map, distinct count) and grows or shrinks it as a condition breaks. They are the two most frequent interview patterns for arrays and strings.

## Key points

### Two pointers
| Variant | Signal in the problem | Example |
|---|---|---|
| opposite ends | sorted array, pair with target sum/difference, palindrome check, container with most water | `lo` and `hi` move inward |
| same direction, read/write | remove duplicates or zeros in place, partition | `write` lags `read` |
| fast/slow | linked list middle, cycle detection (Floyd), k-th from the end | `fast` moves two steps |
| two arrays | merge sorted arrays, intersection of sorted lists | one index per array |

- Why it works on sorted input: if `a[lo] + a[hi] < target`, no pair with `a[lo]` and a smaller `hi` can work either, so `lo++` is safe. This is an **exchange argument** ([[greedy-algorithms]]).
- Unsorted data: sort first (O(n log n), [[sorting-algorithms]]) or use a hash set for O(n) with O(n) memory ([[hash-tables]]). Three-sum is sort + two pointers inside a loop, O(n²).
- Fast/slow on linked lists is the only O(1)-space way to find a cycle or the middle ([[linked-lists]]).

### Sliding window
| Window type | Signal | Shape of the code |
|---|---|---|
| fixed size k | "every k consecutive", moving average, max sum of k elements | add `a[right]`, subtract `a[right - k]` |
| variable, shrink while invalid | longest substring without repeats, longest subarray with sum ≤ S | grow right; while invalid move left |
| variable, shrink while valid | minimum window containing all chars, smallest subarray with sum ≥ S | grow right; while valid record and move left |
| counts window | anagrams in a string, "at most k distinct" | `int[26]` or `HashMap<Character,Integer>` with a `distinct` counter |

- Precondition: the validity must be **monotonic** in window size (growing can only make a "sum ≤ S" window less valid, never more). Negative numbers break that for sum-based windows; use [[prefix-sums-and-intervals]] with a hash map instead.
- "Exactly k distinct" = "at most k" − "at most k−1": two sliding-window passes.
- Max/min over a sliding window in O(n) needs a monotonic deque ([[stack-and-monotonic-stack]]).

### Where it appears in backend work
- Rate limiting with a sliding log or sliding window counter ([[rate-limiting]]).
- Streaming aggregation over the last N events (p99 over the last minute) in a metrics pipeline ([[otel-metric-types]]).
- Merging two sorted Kafka partitions by timestamp is the two-array pointer walk.

### Complexity
Both patterns: O(n) time because each pointer moves at most n times in total; O(1) extra space, or O(k) for a counts map with k distinct keys. The inner `while` does not make it O(n²): `left` can only advance n times over the whole run ([[big-o-and-complexity]]).

## Example
```java
// Variable sliding window: longest run of consecutive requests from one client
// whose total payload bytes stay under `limitBytes`. Returns [start, endExclusive).
static int[] longestUnderLimit(int[] bytes, long limitBytes) {
    int left = 0, bestLeft = 0, bestLen = 0;
    long sum = 0;
    for (int right = 0; right < bytes.length; right++) {
        sum += bytes[right];                               // grow
        while (sum > limitBytes && left <= right) {        // shrink until valid again
            sum -= bytes[left++];
        }
        if (right - left + 1 > bestLen) {                  // record
            bestLen = right - left + 1;
            bestLeft = left;
        }
    }
    return new int[]{bestLeft, bestLeft + bestLen};
}

// Two pointers on sorted data: does any pair of plan prices add up to exactly `target`?
static boolean hasPairSum(int[] sortedPrices, int target) {
    int lo = 0, hi = sortedPrices.length - 1;
    while (lo < hi) {
        int s = sortedPrices[lo] + sortedPrices[hi];
        if (s == target) return true;
        if (s < target) lo++; else hi--;
    }
    return false;
}
```

## Pitfalls
- Applying a sum-based sliding window to arrays with negative numbers: shrinking no longer monotonically reduces the sum, so the answer is wrong. Prefix sums plus a map is the fix.
- Updating the window summary after moving the pointer instead of before (off-by-one on `sum -= a[left++]`).
- Using `HashMap<Character,Integer>` where `int[128]` would do; it is ten times slower and clutters the code with boxing ([[casting-and-autoboxing]]).
- Recording the best answer outside the loop or only when the window is valid for "shrink while valid" problems: the minimum window is recorded *inside* the shrink loop.
- Two pointers on unsorted data without sorting first; the exchange argument no longer holds.
- Related: [[prefix-sums-and-intervals]] for the non-monotonic cases, [[string-algorithms]] for frequency-array tricks, [[interview-problem-patterns]].
