---
title: How HashMap works inside
tags: [hashmap, hashing, equals-hashcode, red-black-tree, weakhashmap]
learned: 2026-10-22
---
## TL;DR
`HashMap` is an array of **buckets** (16 by default). A key's hash picks the bucket; collisions inside a bucket form a linked list, which since Java 8 turns into a red-black tree once it holds 8 or more entries. When the map is 75% full (load factor) the array doubles and every entry is rehashed.

## Key points
### Structure
- `table[]` of `Node` (key, value, cached hash, next). A bucket is either empty, a chain of `Node`s, or a tree of `TreeNode`s (parent, left, right, prev, red flag): HashMap's own red-black tree, not a `TreeMap`.
- Defaults: capacity 16, load factor 0.75, treeify threshold 8, untreeify threshold 6, minimum table size for treeing 64 (below that it resizes instead).

### put(key, value)
1. `hash = key.hashCode()` spread with `h ^ (h >>> 16)` so the high bits take part.
2. `index = hash & (table.length - 1)` (the length is always a power of two, so this is the modulo).
3. Empty bucket → new `Node` goes in.
4. Non-empty bucket = **collision**. Walk the chain or tree: first compare the stored hashes (cheap; different hashes can land in the same bucket), then on equal hashes call `equals()`. Match → overwrite the value. No match → append to the chain (or insert into the tree).
5. Chain length ≥ 8 → treeify (lookup falls from O(n) to O(log n)). A tree shrinks back to a chain at ≤ 6 on removal.
6. `size > capacity * loadFactor` → resize ×2 and redistribute.

### Why treeing works without Comparable keys
A red-black tree needs an order. HashMap compares tree keys in this order: the hash; then `compareTo` if both keys implement `Comparable` of the same class; then `tieBreakOrder()`, which compares class names and finally `System.identityHashCode()`. So any key type works, just slower when hashes collide.

### Relatives
- `Hashtable`: legacy, every method `synchronized`, no null key or values, `Enumeration` instead of a fail-fast iterator. Use `HashMap` or `ConcurrentHashMap`.
- `LinkedHashMap`: keeps insertion (or access) order with an extra linked list through the entries.
- `IdentityHashMap`: compares keys with `==`, ignoring `equals`/`hashCode`. For object-identity caches and serialization frameworks.
- `WeakHashMap`: keys are held by `WeakReference` ([[reference-types]]), so an entry disappears once nothing else references the key. Classic use: attaching extra data to objects you cannot modify; the entry dies with the object.

## Example
```java
// The equals/hashCode contract is what makes all of this work
record Point(int x, int y) {}      // records generate both correctly

Map<Point, String> names = new HashMap<>();
names.put(new Point(1, 2), "A");
names.get(new Point(1, 2));        // "A": same hash -> same bucket -> equals() true
```

## Pitfalls
- Override `equals` without `hashCode` and equal keys land in different buckets: `get` returns null.
- Poor `hashCode` (constant, or few distinct values) collapses everything into one bucket; before Java 8 that was an O(n) DoS vector for string keys.
- `HashMap` is not thread-safe: concurrent `put`s can lose entries; before Java 8 they could even create a cycle and an infinite loop. Use [[concurrent-collections]].
- Iteration order is unspecified and changes after a resize; never depend on it.
- Overview of the other implementations: [[collections-framework]].
