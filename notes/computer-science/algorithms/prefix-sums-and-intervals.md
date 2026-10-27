---
title: Prefix sums, difference arrays and interval problems
tags: [algorithms, prefix-sum, intervals, sweep-line]
learned: 2026-10-27
---
## TL;DR
A **prefix sum** array answers any range-sum query in O(1) after O(n) preprocessing: `sum(l, r) = P[r+1] - P[l]`. Its inverse, the **difference array**, applies a range update in O(1) and reconstructs the final array with one prefix pass. Interval problems (merge, insert, overlap count, meeting rooms) reduce to **sort by start** plus a linear scan, or to a **sweep line** over start/end events with a running counter. All of these are O(n) after an O(n log n) sort and show up constantly both in interviews and in billing, scheduling and monitoring code.

## Key points

### Prefix sums
- `P[0] = 0`, `P[i+1] = P[i] + a[i]`. Range sum over `[l, r]` inclusive is `P[r+1] - P[l]`. Use `long` for the prefix even when elements are `int`.
- **Subarray with target sum** (negatives allowed): walk `i`, keep `HashMap<prefix, firstIndex or count>`; for each `P[i]` look up `P[i] - target`. O(n). This is what a sliding window cannot do with negatives ([[two-pointers-and-sliding-window]]).
- Same trick with modulo: "subarray sum divisible by k" keys the map by `P[i] mod k`.
- 2D prefix sums: `S[i][j]` = sum of the rectangle from (0,0); a sub-rectangle is four lookups (inclusion–exclusion).
- Prefix XOR, prefix product (watch zeros), prefix counts of a character: the same idea with a different monoid.
- Monitoring: a Prometheus counter is a prefix sum over time; `rate()` is the range difference divided by the interval ([[otel-metric-types]]).

### Difference arrays
- To add `v` to every element in `[l, r]`: `D[l] += v; D[r+1] -= v`. After all updates, prefix-sum `D` to get the result. m updates on n elements cost O(n + m) instead of O(n·m).
- Use for "car pooling" (capacity along a route), bookings per day, concurrent-connection peaks from a list of `(connectAt, disconnectAt)` sessions.
- Range updates **and** range queries interleaved need a Fenwick tree or segment tree (O(log n) each); mention them, do not implement under interview time unless asked.

### Interval patterns
| Problem | Approach | Complexity |
|---|---|---|
| merge overlapping intervals | sort by start; extend current if `next.start <= cur.end`, else emit | O(n log n) |
| insert an interval into a sorted non-overlapping list | copy left part, merge the overlapping middle, copy right | O(n) |
| max number of overlapping intervals (meeting rooms II, peak concurrent sessions) | sweep line: events `(+1 at start, −1 at end)`, sort, running max | O(n log n) |
| min intervals to remove so none overlap | sort by **end**, greedy keep ([[greedy-algorithms]]) | O(n log n) |
| does a point/interval overlap any stored one | `TreeMap.floorEntry(start)` and `ceilingEntry` | O(log n) per query |
| free time between busy slots | merge, then gaps between consecutive intervals | O(n log n) |

- Half-open convention `[start, end)` avoids the "do touching intervals overlap" ambiguity; it is also what Postgres `tstzrange` and `EXCLUDE USING gist` use to forbid overlapping bookings ([[postgres-indexes]]).
- Sweep line tie-break: when start and end times are equal, process ends first for half-open intervals (a session ending at 10:00 does not overlap one starting at 10:00).

### Where it appears in backend work
- Billing: usage charged per overlapping tariff period is a merge of `[activatedAt, deactivatedAt)` with `[periodStart, periodEnd)`.
- Capacity planning from session logs: peak concurrency via sweep line, p-th percentile via prefix counts over a histogram.
- Scheduling and SLA windows ([[slo-sli-and-alerting]]): time in violation is the merged length of alert intervals.

## Example
```java
// Peak number of simultaneously active eSIM sessions from (start, end) pairs, half-open.
// Sweep line: O(n log n) for the sort, O(n) scan.
record Session(long start, long end) {}

static int peakConcurrency(List<Session> sessions) {
    long[][] events = new long[sessions.size() * 2][];
    int k = 0;
    for (Session s : sessions) {
        events[k++] = new long[]{s.start(), +1};
        events[k++] = new long[]{s.end(),   -1};
    }
    // sort by time; at equal time, process -1 (end) before +1 (start)
    Arrays.sort(events, Comparator.<long[]>comparingLong(e -> e[0]).thenComparingLong(e -> e[1]));
    int active = 0, peak = 0;
    for (long[] e : events) {
        active += (int) e[1];
        peak = Math.max(peak, active);
    }
    return peak;
}

// Prefix sums: bytes transferred in any [from, to) window of per-minute counters, O(1) per query.
static long[] prefix(int[] perMinute) {
    long[] p = new long[perMinute.length + 1];
    for (int i = 0; i < perMinute.length; i++) p[i + 1] = p[i] + perMinute[i];
    return p;
}
static long bytesBetween(long[] p, int from, int to) { return p[to] - p[from]; }
```

## Pitfalls
- `int` prefix sums overflow silently; 2 billion bytes is reached after 2 GB of traffic.
- Off-by-one between `P[r+1] - P[l]` (inclusive r) and `P[r] - P[l]` (exclusive r); pick half-open everywhere.
- Merging intervals without sorting first, or sorting by end when the algorithm assumes start order.
- Treating `[1, 5]` and `[5, 8]` as overlapping or not depending on the file; make the convention explicit in the type (`Range` record with a comment, or `tstzrange` in SQL).
- Using a difference array for queries interleaved with updates: each query would need a full O(n) rebuild.
- Related: [[two-pointers-and-sliding-window]] for the monotonic-case alternative, [[greedy-algorithms]] for interval scheduling, [[interview-problem-patterns]].
