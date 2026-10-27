---
title: Sorting algorithms and what Java actually uses
tags: [algorithms, sorting, complexity, java]
learned: 2026-10-27
---
## TL;DR
Comparison-based sorting cannot beat O(n log n) in the worst case; merge sort, heap sort and well-pivoted quicksort reach it. Java uses **dual-pivot quicksort** for primitive arrays and **TimSort** (a stable merge/insertion hybrid) for objects, `List.sort` and streams. Counting and radix sort break the n log n barrier for integers with a bounded range. In interviews, know **stability**, in-place vs extra memory, and when you do not need a full sort at all (top-k via a heap, partial order via quickselect).

## Key points

### Comparison sorts
| Algorithm | Best | Average | Worst | Extra space | Stable | Notes |
|---|---|---|---|---|---|---|
| Insertion | O(n) | O(n²) | O(n²) | O(1) | yes | fastest for n < ~32 or nearly sorted; TimSort uses it for runs |
| Selection | O(n²) | O(n²) | O(n²) | O(1) | no | minimal swaps, otherwise useless |
| Bubble | O(n) | O(n²) | O(n²) | O(1) | yes | interview trivia only |
| Merge | O(n log n) | O(n log n) | O(n log n) | O(n) | yes | predictable, parallelizes, external sort for data on disk |
| Quick | O(n log n) | O(n log n) | O(n²) | O(log n) stack | no | cache-friendly, best constant factors; random/median pivot avoids the worst case |
| Heap | O(n log n) | O(n log n) | O(n log n) | O(1) | no | guaranteed bound, poor locality ([[heaps-and-priority-queues]]) |
| TimSort | O(n) | O(n log n) | O(n log n) | O(n) | yes | finds existing runs, merges them; Java/Python default for objects |

- **Stability**: equal elements keep their input order. Needed when sorting by a secondary key after a primary one (sort orders by date, then stable-sort by customer, and each customer's orders stay by date). `Comparator.thenComparing` avoids the two-pass trick, but a stable sort is still what makes `sort by status` keep the existing order inside each status.
- **Why n log n is the floor**: a comparison sort must distinguish n! orderings; each comparison halves the candidates, so log₂(n!) ≈ n log n comparisons are needed ([[big-o-and-complexity]]).

### Non-comparison sorts
- **Counting sort**: O(n + k) for integers in range k. Count occurrences, prefix-sum the counts, place elements. Stable. Great for sorting statuses, bytes, small ids.
- **Radix sort**: counting sort per digit, least significant first, O(d · (n + k)). Used for fixed-width keys (32-bit ints, ICCIDs as digit strings).
- **Bucket sort**: distribute into buckets, sort each; O(n) average for uniformly distributed data.

### What Java does
- `Arrays.sort(int[])`: dual-pivot quicksort (Java 7+), with insertion sort for tiny ranges and a heap-sort fallback (Java 14+) so the worst case is O(n log n). Not stable, but primitives have no identity, so it does not matter.
- `Arrays.sort(Object[])`, `List.sort`, `Collections.sort`, `Stream.sorted`: TimSort, stable, O(n) extra. Throws `IllegalArgumentException: Comparison method violates its general contract!` if the comparator is inconsistent (for example subtracting ints that overflow, or a comparator that is not transitive).
- `Arrays.parallelSort`: parallel merge sort via [[fork-join-framework]]; pays off from roughly 10⁵ elements.
- `TreeMap`/`TreeSet` keep data sorted on insert at O(log n) each: building one is O(n log n), same as sorting once, but each later insert stays cheap ([[trees-and-bst]]).

### When not to sort everything
- Top-k of n: a min-heap of size k is O(n log k) ([[heaps-and-priority-queues]]).
- k-th smallest: quickselect, O(n) average.
- Already sorted inputs to merge: k-way merge with a heap, O(n log k).
- Sorting in the database: `ORDER BY` on an indexed column is a free index walk; without an index it is an in-memory or on-disk sort visible in `EXPLAIN` as `Sort Method: external merge` ([[explain-analyze]]).

## Example
```java
// Merge sort on an int[]: the shape every "implement a sort" question expects.
static void mergeSort(int[] a) { if (a.length > 1) sort(a, new int[a.length], 0, a.length); }

private static void sort(int[] a, int[] tmp, int lo, int hi) {   // sorts [lo, hi)
    if (hi - lo < 2) return;
    int mid = (lo + hi) >>> 1;                                     // no overflow
    sort(a, tmp, lo, mid);
    sort(a, tmp, mid, hi);
    if (a[mid - 1] <= a[mid]) return;                              // already ordered: TimSort's trick
    merge(a, tmp, lo, mid, hi);
}

private static void merge(int[] a, int[] tmp, int lo, int mid, int hi) {
    int i = lo, j = mid, k = lo;
    while (i < mid && j < hi) tmp[k++] = a[i] <= a[j] ? a[i++] : a[j++];  // <= keeps stability
    while (i < mid) tmp[k++] = a[i++];
    while (j < hi)  tmp[k++] = a[j++];
    System.arraycopy(tmp, lo, a, lo, hi - lo);
}

// Production code: a stable multi-key sort, no hand-written algorithm.
orders.sort(Comparator.comparing(Order::status)
                      .thenComparing(Order::createdAt, Comparator.reverseOrder()));
```

## Pitfalls
- `(a, b) -> a.amount - b.amount` as a comparator overflows for large ints and breaks the contract; use `Integer.compare` or `Comparator.comparingInt`.
- Comparators that disagree with `equals` in a `TreeSet` silently drop elements the set considers "equal" ([[equals-and-hashcode]]).
- Sorting `BigDecimal` with `compareTo` treats `1.0` and `1.00` as equal; `equals` does not ([[time-money-and-ids]]).
- Quicksort with first-element pivot on sorted input is O(n²) and can overflow the stack; always say "randomized pivot" in an interview.
- Sorting a million-row result in the application instead of letting Postgres use an index is a classic performance bug ([[postgres-indexes]]).
- Related: [[data-structures]] for the containers you sort, [[greedy-algorithms]] because most greedy solutions start with a sort, [[interview-problem-patterns]] for "sort first, then two pointers".
