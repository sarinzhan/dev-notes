---
title: Java Collections Framework map
tags: [collections, list, set, map, queue, big-o]
learned: 2026-10-22
---
## TL;DR
The framework splits into two roots: `Collection` (sequences of elements: `List`, `Set`, `Queue`/`Deque`) and `Map` (key–value pairs). `Iterable` sits above `Collection` and is what makes `for-each` work. Pick the implementation by the operations you need and their cost, not by habit: `ArrayList` + `HashMap` cover 90% of cases.

## Key points
### Interfaces
- `Iterable` → provides an `Iterator` (and `forEach`, `spliterator`). See [[iterators-and-fail-fast]].
- `Collection` → `List` (ordered, duplicates allowed, index access), `Set` (no duplicates), `Queue` (FIFO: `offer/poll/peek` return special values, `add/remove/element` throw), `Deque` (both ends: `offerFirst/pollLast…`, also used as a stack).
- `Map` (not a `Collection`): `SortedMap`/`NavigableMap` add ordering by key (`firstKey`, `headMap`, `ceilingKey`).

### Implementations
| Interface | Implementation | Backed by | Notes |
|---|---|---|---|
| List | `ArrayList` | growable array | index access O(1); capacity grows ×1.5 and never shrinks automatically (`trimToSize`) |
| List / Deque | `LinkedList` | doubly linked nodes | O(1) insert at ends, O(n) by index; rarely better than `ArrayDeque` |
| List | `Vector`, `Stack` | synchronized array | legacy; use `ArrayList` / `ArrayDeque` |
| Set | `HashSet` | a `HashMap` with dummy values | no order |
| Set | `LinkedHashSet` | hash map + linked list | insertion order |
| Set | `TreeSet` | red-black tree | sorted by `Comparable` or a `Comparator`; neither → `ClassCastException` |
| Queue | `PriorityQueue` | binary heap | head is the smallest by comparator; not sorted when iterated |
| Deque | `ArrayDeque` | circular array | the stack and queue of choice; no nulls |
| Map | `HashMap` | array of buckets | one null key, any null values; see [[hashmap-internals]] |
| Map | `LinkedHashMap` | + linked list | insertion or access order (LRU caches via `removeEldestEntry`) |
| Map | `TreeMap` | red-black tree | sorted keys, range queries |
| Map | `Hashtable` | synchronized | legacy, no nulls |
| Map | `WeakHashMap`, `IdentityHashMap` | special keys | see [[hashmap-internals]] |

### Complexity (Big O)
Big O describes how time or memory grows with input size n. O(1) constant, O(log n) logarithmic (trees, binary search), O(n) linear, O(n log n) sorting, O(n²) nested loops.

| | get | add | contains | remove |
|---|---|---|---|---|
| `ArrayList` | O(1) | O(1) amortized, O(n) in the middle | O(n) | O(n) |
| `LinkedList` | O(n) | O(1) at ends | O(n) | O(1) with iterator |
| `HashMap` / `HashSet` | O(1) avg | O(1) avg | O(1) avg | O(1) avg |
| `TreeMap` / `TreeSet` | O(log n) | O(log n) | O(log n) | O(log n) |
| `PriorityQueue` | peek O(1) | O(log n) | O(n) | poll O(log n) |

### Utilities and wrappers
- `Collections` and `Arrays` are static helper classes (`sort`, `binarySearch`, `shuffle`, `asList`), not to be confused with the `Collection` and array types.
- Read-only views: `Collections.unmodifiableList/Set/Map(x)` (a view: the original can still change) and the immutable `List.of`, `Set.of`, `Map.of`, `List.copyOf` (Java 9+, reject nulls).
- Synchronized wrappers `Collections.synchronizedList/Map(x)`: a global lock, iteration still needs manual locking. Prefer [[concurrent-collections]].
- Heterogeneous collections (elements of different types) exist only with `Object` or raw types; see [[generics]].

### Comparable vs Comparator
- `Comparable<T>`: the type's **natural** order, one `compareTo(T)` implemented by the class itself (`String`, numbers, `LocalDate`).
- `Comparator<T>`: an **external** order, `compare(a, b)`, as many as you want. Build them with `Comparator.comparing(User::lastName).thenComparing(User::age).reversed()`.

## Example
```java
record Order(String id, Instant created, BigDecimal total) {}

List<Order> orders = new ArrayList<>(repo.findAll());
orders.sort(Comparator.comparing(Order::created).reversed());

Map<String, List<Order>> byDay = new TreeMap<>();              // sorted keys
for (Order o : orders)
    byDay.computeIfAbsent(o.created().toString().substring(0, 10), k -> new ArrayList<>()).add(o);

Deque<Order> stack = new ArrayDeque<>();                        // not java.util.Stack
stack.push(orders.get(0));
```

## Pitfalls
- `Arrays.asList(arr)` is a fixed-size view over the array; `add` throws. `List.of` is fully immutable.
- Mutating an element's fields after putting it in a `HashSet`/`HashMap` key loses it (hash changes).
- `TreeSet` treats elements with `compareTo == 0` as duplicates even if `equals` says otherwise.
- `LinkedList` is almost never the right list; its only advantage is O(1) removal through an iterator.
