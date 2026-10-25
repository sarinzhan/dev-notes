---
title: Stream API, parallel streams and gatherers
tags: [streams, functional, parallel-streams, gatherers, java24]
learned: 2026-10-22
---
## TL;DR
Streams (Java 8) process a sequence of elements declaratively: build a pipeline of lazy **intermediate** operations and finish with one eager **terminal** operation. Nothing runs until the terminal call. Parallel streams split the work over the common `ForkJoinPool`; **gatherers** (Java 24) let you write your own intermediate operations.

## Key points
### Creating a stream
- From a collection: `list.stream()`, `list.parallelStream()`.
- From values or arrays: `Stream.of(a, b, c)`, `Arrays.stream(arr)`, `IntStream.range(0, n)`.
- From a string: `"hello".chars()` (an `IntStream`).
- Infinite: `Stream.iterate(seed, f)`, `Stream.generate(supplier)`; must be cut with `limit` or `takeWhile`.
- Others: `Files.lines(path)`, `random.ints()`, `Stream.ofNullable`.

### Intermediate (lazy, return a stream)
`filter`, `map`, `mapToInt/Long/Double`, `flatMap` (one element → many), `flatMapToInt…`, `distinct`, `sorted`, `peek` (debugging), `limit`, `skip`, `takeWhile`, `dropWhile` (Java 9), `mapMulti` (Java 16), `gather` (Java 24).

### Terminal (eager, return a result)
`forEach`, `forEachOrdered`, `collect`, `toList` (Java 16), `reduce`, `count`, `min`, `max`, `findFirst`, `findAny`, `anyMatch` / `allMatch` / `noneMatch`, `toArray`, `iterator`.
- `forEach` on a parallel stream has no order; `forEachOrdered` keeps encounter order.
- `Collectors`: `toList`, `toSet`, `toMap`, `groupingBy`, `partitioningBy`, `joining`, `counting`, `summingInt`, `teeing`.

### Primitive streams
`IntStream`, `LongStream`, `DoubleStream` avoid boxing. They use specialized functional interfaces (`IntPredicate`, `IntFunction`) and add `sum`, `average`, `summaryStatistics`, `boxed`, `mapToObj`.

### Parallel streams
- `parallelStream()` or `.parallel()`; `.sequential()` switches back; `isParallel()` tells you which. The whole pipeline runs in the last mode set, not per operation.
- The data is split by a `Spliterator`, chunks are processed on the common `ForkJoinPool` ([[fork-join-framework]]), results are combined.
- Worth it when: large data, cheap splitting (`ArrayList`, arrays, `IntStream.range`; not `LinkedList` or `iterate`), CPU-bound and stateless operations, many cores. `unordered()` removes the cost of keeping order.
- Not worth it when: small data, blocking I/O (starves the shared pool), operations with side effects, or `limit`/`findFirst` that depend on order.

### Gatherers (Java 24, JEP 485)
`Stream.gather(Gatherer)` is the extension point for custom intermediate operations, the way `collect(Collector)` is for terminal ones. Built-ins in `Gatherers`: `windowFixed(n)`, `windowSliding(n)`, `fold`, `scan`, `mapConcurrent(n, f)`. This is the feature previewed in Java 22–23.

## Example
```java
record Txn(String account, BigDecimal amount, LocalDate date) {}

Map<String, BigDecimal> totalsPerAccount = txns.stream()
    .filter(t -> t.date().isAfter(since))
    .collect(Collectors.groupingBy(Txn::account,
             Collectors.reducing(BigDecimal.ZERO, Txn::amount, BigDecimal::add)));

List<List<Txn>> batches = txns.stream()
    .gather(Gatherers.windowFixed(100))      // Java 24
    .toList();
```

## Pitfalls
- A stream can be consumed once; reusing it throws `IllegalStateException`.
- `peek` is for debugging; relying on it for side effects breaks under `count()` (which may skip the pipeline) and in parallel.
- `Collectors.toMap` throws on duplicate keys unless you pass a merge function.
- `sorted` and `distinct` are stateful and buffer the whole stream; on infinite streams they never finish.
- The functional interfaces the API is built on: [[functional-interfaces]].
