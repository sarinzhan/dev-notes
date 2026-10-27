---
title: Arrays and dynamic arrays
tags: [data-structures, arrays, memory, amortized, complexity]
learned: 2026-10-27
---
## TL;DR
An array is a contiguous block of memory where element `i` lives at `base + i * elementSize`, so index access is O(1) and iteration is cache-friendly. A dynamic array (`ArrayList`, Go slices, C++ `vector`) wraps a fixed array and, when full, allocates a bigger one and copies: append is O(n) in the worst case but **amortized O(1)** because the copies happen geometrically less often. It is the default collection for almost everything; the overview of where it sits among the others is in [[data-structures]].

## Key points

### Memory layout
- Fixed size, one type, contiguous. The CPU prefetcher and cache lines (64 bytes) make a sequential scan over an `int[]` several times faster than walking the same number of pointers ([[cpu-caches-and-cache-lines]]).
- In Java a primitive array (`int[]`, `byte[]`) stores values inline. An `Object[]` stores **references**; the objects themselves are scattered on the heap, so a `List<Integer>` loses most of the cache benefit (plus boxing, see [[primitives-and-wrappers]]).
- A Java array has a 12–16 byte header plus a length field; the length is fixed at allocation and checked on every access (`ArrayIndexOutOfBoundsException`, no buffer overflows).
- Multidimensional arrays in Java are arrays of arrays (`int[][]`), each row a separate object; row-major iteration is cache-friendly, column-major is not.

### Operations and complexity
| Operation | Static array | Dynamic array (`ArrayList`) |
|---|---|---|
| Read/write by index | O(1) | O(1) |
| Append at end | n/a (fixed) | O(1) amortized, O(n) on resize |
| Insert/delete at index `i` | O(n) shift | O(n) shift (`System.arraycopy`) |
| Search unsorted | O(n) | O(n) |
| Search sorted | O(log n) ([[binary-search-and-variants]]) | O(log n) |
| Memory | exactly n elements | up to 2× n (spare capacity) |

### Amortized growth
- `ArrayList` grows by 1.5× (`oldCapacity + (oldCapacity >> 1)`), Go slices by 2× then 1.25×, C++ `vector` typically 2×.
- With a geometric factor, pushing n elements costs at most n + n/1.5 + n/2.25 + ... ≈ 3n copies in total, so O(1) per append on average. Growing by a constant (+10) would cost O(n²) overall. This is the textbook amortized-analysis example ([[big-o-and-complexity]]).
- Shrinking is not automatic: `ArrayList.remove` never releases capacity; call `trimToSize()` if a list grew once and now stays small.
- Resize is a full copy: a single `add` can pause for milliseconds on a 10M-element list and triggers a large allocation the GC has to find room for ([[garbage-collection]]).

### Why arrays win in practice
- Big-O says `LinkedList` inserts in the middle in O(1), but finding the position is O(n) and each node is a separate heap object. Measured, `ArrayList` beats it even for insert-heavy workloads below tens of thousands of elements ([[linked-lists]]).
- Sorting, binary search, prefix sums, two-pointer and sliding-window techniques all assume an array ([[two-pointers-and-sliding-window]], [[prefix-sums-and-intervals]]).
- A ring buffer over an array gives an O(1) queue with no allocation per element ([[stacks-and-queues]]); a binary heap is just an array with index arithmetic ([[heaps-and-priority-queues]]).

### Java specifics worth knowing
- `Arrays.asList` returns a fixed-size view backed by the array: `set` works, `add` throws. `List.of` is immutable and rejects `null`.
- `ArrayList` is not thread-safe; `CopyOnWriteArrayList` copies the whole array on each write, which is only fine for read-mostly data ([[concurrent-collections]]).
- The rest of the JDK list API, iterators and modCount behaviour are in [[collections-framework]] and [[iterators-and-fail-fast]].

## Example
A minimal dynamic array of `long` values with geometric growth, the kind of thing you write for a metrics buffer or when asked "implement ArrayList" in an interview.

```java
public final class LongList {
    private long[] data;
    private int size;

    public LongList(int initialCapacity) {
        data = new long[Math.max(initialCapacity, 1)];
    }

    public void add(long value) {
        if (size == data.length) {
            data = Arrays.copyOf(data, data.length + (data.length >> 1)); // x1.5, same as ArrayList
        }
        data[size++] = value;
    }

    public long get(int index) {
        Objects.checkIndex(index, size);              // throws IndexOutOfBoundsException
        return data[index];
    }

    public long removeAt(int index) {
        Objects.checkIndex(index, size);
        long removed = data[index];
        System.arraycopy(data, index + 1, data, index, size - index - 1); // O(n) shift
        size--;
        return removed;
    }

    public int size() { return size; }
}
```

## Pitfalls
- Building a list in a loop with `new ArrayList<>()` and a known final size wastes several resizes; pass the expected size to the constructor (`new ArrayList<>(rows.size())`).
- Removing elements while iterating by index (`for (int i = 0; i < list.size(); i++) list.remove(i)`) skips every second element; iterate backwards or use `removeIf`.
- `list.remove(int)` and `list.remove(Object)` on a `List<Integer>` pick different overloads; `list.remove(1)` removes the element at index 1, not the value 1.
- A huge `ArrayList<Integer>` costs roughly 20 bytes per element (reference + boxed object) against 4 for an `int[]`; for numeric hot paths use primitive arrays or a primitive-specialized library.
- Related: [[data-structures]] for the overview, [[collections-framework]] for the JDK implementations, [[stack-vs-heap-memory]] for where the backing array lives.
