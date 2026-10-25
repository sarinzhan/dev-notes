---
title: Iterator, Iterable and fail-fast vs fail-safe
tags: [iterator, iterable, fail-fast, concurrentmodificationexception]
learned: 2026-10-22
---
## TL;DR
`Iterable` is "something you can get an `Iterator` from", and `for-each` is syntax sugar over exactly that. Most `java.util` iterators are **fail-fast**: they throw `ConcurrentModificationException` if the collection was structurally changed behind their back. The concurrent collections give **fail-safe** iterators that work on a snapshot and never throw.

## Key points
### The interfaces
- `Iterator<E>`: `hasNext()`, `next()` (returns the current element and advances), `remove()` (removes the last returned element; the only safe way to delete while iterating).
- `ListIterator<E>` extends `Iterator`: walks in both directions (`hasPrevious`, `previous`), knows the index, and can `add()` and `set()` as well as `remove()`.
- `Iterable<E>`: one method, `iterator()`; implementing it lets any class be used in `for (E e : obj)`. The compiler turns the loop into `Iterator it = obj.iterator(); while (it.hasNext()) …`.
- `Enumeration`: the pre-1.2 version (`hasMoreElements`, `nextElement`) still exposed by `Vector`, `Stack`, `Hashtable`. No `remove`, longer names. Legacy.

### Fail-fast
- Fail-fast in general: stop immediately when an error (or a state that leads to one) is detected, instead of continuing with corrupted data.
- Implementation: the collection keeps a `modCount` that changes on every structural modification (add/remove, not `set`). The iterator copies it at creation and compares on every `next()`; a mismatch throws `ConcurrentModificationException`.
- It is a best-effort check, not a guarantee: it catches single-thread bugs reliably, multithreaded ones only sometimes.

### Fail-safe
- Iterators of `CopyOnWriteArrayList` (snapshot of the array) and of `ConcurrentHashMap` views (weakly consistent: reflect some, maybe not all, concurrent changes) never throw. The price: you may iterate stale data. See [[concurrent-collections]].

## Example
```java
List<String> names = new ArrayList<>(List.of("a", "b", "c"));

for (String n : names)                 // ConcurrentModificationException on the next step
    if (n.equals("b")) names.remove(n);

Iterator<String> it = names.iterator(); // correct
while (it.hasNext())
    if (it.next().equals("b")) it.remove();

names.removeIf(n -> n.equals("b"));     // simplest
```

## Pitfalls
- Removing inside `for-each` is the classic CME; use `Iterator.remove`, `removeIf`, or iterate over a copy.
- Modifying a `HashMap` while iterating its `keySet()` throws too; `entrySet().removeIf` or `ConcurrentHashMap` are the fixes.
- `Collections.synchronizedList` does not make iteration safe; lock around the loop or use a concurrent collection.
- Where these fit in the framework: [[collections-framework]].
