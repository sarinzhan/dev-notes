---
title: Big-O and complexity analysis
tags: [algorithms, complexity, big-o, performance]
learned: 2026-10-27
---
## TL;DR
Big-O describes how running time or memory **grows with input size**, ignoring constants and lower-order terms. It is an upper bound on the growth rate, not a stopwatch reading: an O(n) algorithm with a huge constant can lose to O(n log n) on real inputs. In interviews you are expected to state time and space complexity for every solution, distinguish best/average/worst case, and know what "amortized O(1)" means for `ArrayList.add` and `HashMap.put`.

## Key points

### The notation family
| Notation | Meaning | Interview use |
|---|---|---|
| O(f) | grows **no faster** than f (upper bound) | the one everybody means |
| Ω(f) | grows **at least** as fast as f (lower bound) | "any comparison sort is Ω(n log n)" |
| Θ(f) | both: tight bound | when you want to be precise |
| o(f) | strictly slower growth | rare |

- Drop constants and lower-order terms: `3n² + 50n + 7` is O(n²). Two nested loops over the same array are O(n²); two sequential loops are O(n) + O(n) = O(n).
- Different inputs get different variables: merging lists of sizes n and m is O(n + m), not O(n).
- Logarithms: base does not matter (constant factor), so O(log₂ n) = O(log n). Halving the search space each step gives log n ([[binary-search-and-variants]]); balanced-tree height is log n ([[trees-and-bst]]).

### Common classes, with a real example each
| Class | Name | Example | n = 1 000 000 |
|---|---|---|---|
| O(1) | constant | `HashMap.get`, array index, `ArrayDeque.push` | 1 |
| O(log n) | logarithmic | binary search, `TreeMap.get`, heap push/pop | ~20 |
| O(n) | linear | scan a list, `ArrayList.contains`, BFS over n nodes | 10⁶ |
| O(n log n) | linearithmic | `Arrays.sort`, merge sort, heap sort, building a `TreeMap` | 2 × 10⁷ |
| O(n²) | quadratic | nested loops, bubble/insertion sort, naive duplicate check | 10¹² |
| O(2ⁿ) | exponential | all subsets, naive recursive Fibonacci | hopeless |
| O(n!) | factorial | all permutations | hopeless above n ≈ 11 |

Rule of thumb for the "will it pass" question: about 10⁸ simple operations per second. n = 10⁵ rules out O(n²); n = 10³ makes O(n²) fine; n ≤ 20 allows O(2ⁿ) bitmask enumeration ([[bit-manipulation]]).

### Best, average, worst
- Quicksort: average O(n log n), worst O(n²) on already-sorted input with a naive pivot. Hash lookup: average O(1), worst O(n) when every key collides ([[hash-tables]]; Java 8 treeifies buckets to make it O(log n)).
- Unless asked otherwise, state the worst case and mention the average when they differ.

### Amortized complexity
- A single operation may be expensive, but the **average over a sequence** is cheap. `ArrayList.add` copies the whole array on resize (O(n)), but doubling capacity means n appends cost O(n) in total, so each append is amortized O(1) ([[arrays-and-dynamic-arrays]]).
- Same reasoning: `HashMap` resize, `StringBuilder.append`, union-find with path compression ([[topological-sort-and-union-find]]).
- Amortized is not average-case: amortized is a guarantee over any sequence, average-case assumes a probability distribution over inputs.

### Space complexity
- Count **extra** memory beyond the input. Merge sort is O(n) extra, heap sort O(1), quicksort O(log n) for the recursion stack.
- Recursion depth is memory: naive DFS on a 10⁶-node path blows the Java stack long before the heap ([[recursion-and-backtracking]], [[stack-vs-heap-memory]]).
- "In-place" means O(1) extra space, not "no allocation at all".

### Reading complexity off Java code
- `for (i..n) for (j = i..n)`: still O(n²) (n²/2 operations).
- `for (i = 1; i < n; i *= 2)`: O(log n).
- Loop of n calling a `List.contains` on an `ArrayList`: O(n²); swap the list for a `HashSet` and it becomes O(n). This is the most common "make it faster" interview fix.
- `String s += x` inside a loop: O(n²) because each concatenation copies ([[strings-and-string-pool]]); use `StringBuilder`.
- Streams do not change complexity: `list.stream().filter(...).toList()` is O(n), `sorted()` adds O(n log n) ([[stream-api]]).

## Example
```java
// Three ways to find whether two users share an ICCID; same answer, different growth.
record Sim(String iccid) {}

static boolean quadratic(List<Sim> a, List<Sim> b) {          // O(n*m) time, O(1) space
    for (Sim x : a)
        for (Sim y : b)
            if (x.iccid().equals(y.iccid())) return true;
    return false;
}

static boolean sortBased(List<Sim> a, List<Sim> b) {          // O((n+m) log(n+m)) time
    String[] all = Stream.concat(a.stream(), b.stream())
            .map(Sim::iccid).sorted().toArray(String[]::new);
    for (int i = 1; i < all.length; i++)
        if (all[i].equals(all[i - 1])) return true;
    return false;
}

static boolean hashBased(List<Sim> a, List<Sim> b) {          // O(n+m) time, O(n) space
    Set<String> seen = new HashSet<>();
    for (Sim x : a) seen.add(x.iccid());                       // amortized O(1) per add
    for (Sim y : b) if (seen.contains(y.iccid())) return true;
    return false;
}
```
Typical interview flow: write `quadratic`, state O(n·m), then improve to `hashBased` and explain the time/space trade.

## Pitfalls
- Saying "O(1) because it is a HashMap" without mentioning the hash function cost: hashing a long `String` is O(length), and a bad `hashCode` turns lookups into O(n) ([[equals-and-hashcode]]).
- Confusing O(n log n) sorting with "fast enough": sorting 10⁸ rows in memory still takes seconds and gigabytes; the DB may do it better with an index ([[database-indexes]]).
- Forgetting the recursion stack when claiming O(1) space.
- Reporting the complexity of the wrong variable: a graph algorithm is O(V + E), not O(n).
- Big-O ignores constants, but hardware does not: a linear scan of a small array beats a hash lookup because of cache lines ([[cpu-caches-and-cache-lines]]).
- Related: [[data-structures]] for the per-structure complexity table, [[sorting-algorithms]] for where the n log n bound comes from, [[interview-problem-patterns]] for picking the pattern that hits the target complexity.
