---
title: Memory models, reordering and visibility
tags: [concurrency, memory-model, visibility, happens-before, reordering]
learned: 2026-10-27
---
## TL;DR
A **memory model** is the contract between a program and the compiler + hardware about which values a read may observe when several threads touch the same memory. Compilers reorder instructions and CPUs execute out of order and buffer stores, so without synchronization a second thread may see writes late, never, or in a different order than they appear in the source. Memory models define **happens-before**: a partial order built from program order plus synchronization edges (lock release → acquire, volatile write → read, thread start, thread join). A read is guaranteed to see a write only if the write happens-before the read. `volatile`/atomics exist to create these edges without a lock. The Java-specific rules and `synchronized`/`volatile` semantics are in [[java-memory-model]]; this note is the general picture that applies to C++, Go, Rust and the hardware.

## Key points

### Where reordering comes from
| Source | What it does | Example |
|---|---|---|
| Compiler / JIT | hoists loads out of loops, caches fields in registers, reorders independent stores | `while (!stop) {}` becomes `if (!stop) while (true) {}`: the loop never sees `stop = true` |
| CPU out-of-order execution | executes independent instructions early | a later load completes before an earlier store |
| Store buffer | a write sits in a per-core buffer before reaching the cache | core A writes `x = 1` then reads `y`; core B writes `y = 1` then reads `x`; both can read 0 on x86 |
| Cache hierarchy | coherent (MESI) but asynchronous: invalidations are queued | a core keeps using a stale line until the invalidate is processed ([[cpu-caches-and-cache-lines]]) |
- The rule every optimiser follows is **as-if-serial**: a single thread cannot tell the difference. Multithreaded programs can, which is why synchronization also acts as an optimisation barrier.
- Hardware models differ: x86-64 is **TSO** (total store order: only store→load reordering is visible, stores are seen in order), ARM and POWER are **weakly ordered** (almost anything can be reordered without barriers). Code that "works on my laptop" (x86) can fail on an ARM server (Graviton, Apple silicon, Ampere) because the JIT emits fewer barriers there and the hardware takes the freedom.

### Happens-before
- A partial order over actions. Rules common to Java, C++11 and Go:
  - Program order within one thread.
  - Unlock of a mutex happens-before every later lock of the same mutex.
  - A volatile/atomic (release) write happens-before every later (acquire) read of that variable that sees the written value.
  - `Thread.start()` happens-before anything in the thread; everything in a thread happens-before `join()` returns.
  - Transitivity.
- Consequence: synchronizing on **one** variable publishes **everything** written before it. A `volatile boolean ready = true` after filling a plain `HashMap` makes the map's contents visible to a reader that sees `ready == true`. This "piggybacking" is how `ConcurrentHashMap.put` publishes the value object and how `ExecutorService.submit` publishes the task's state.
- Without an edge the program has a **data race**; in Java every read still sees *some* write to that variable (plus possible `long`/`double` tearing on 32-bit JVMs). In C++ a data race is undefined behaviour ([[race-conditions-and-critical-sections]]).

### Why volatile exists
- A lock gives three things: mutual exclusion, visibility, ordering. Often you need only the last two: a stop flag, a configuration snapshot swapped atomically, a published-once reference. `volatile` (Java), `std::atomic` with `memory_order_acquire/release` (C++), `atomic.Load/Store` (Go) give visibility and ordering with no blocking.
- Mechanically: the compiler must not cache or reorder across the access, and the JIT emits the barrier the hardware needs (`lock`-prefixed instruction or `mfence` on x86 for a store; `dmb`/`stlr` + `ldar` on ARM).
- Cost: a volatile read on x86 is a plain load; a volatile write is a full fence (~20–50 cycles). On ARM both cost something. Far cheaper than a contended lock.
- `volatile` does **not** make `i++` atomic: one load and one store with a window between them. Use CAS ([[lock-free-and-cas]]).

### Ordering levels (strongest to weakest)
| Level | Guarantee | Java | C++ |
|---|---|---|---|
| Sequentially consistent | all threads agree on one global order of all atomic ops | `volatile`, `VarHandle.setVolatile/getVolatile` | `memory_order_seq_cst` |
| Acquire / release | a release write publishes prior writes to an acquire read that sees it; no global order among unrelated atomics | `VarHandle.setRelease/getAcquire`, `AtomicX.lazySet` (legacy) | `acquire`/`release` |
| Opaque / relaxed | atomic, visible eventually, no ordering with other variables | `VarHandle.setOpaque/getOpaque` | `relaxed` |
| Plain | no guarantees across threads | plain field | plain variable |
- Most code needs only `volatile`/`synchronized`. Acquire/release matters in lock-free ring buffers (Disruptor, Aeron), where the full fence per write is the bottleneck.
- Same idea at other layers: isolation levels are a memory model for transactions ([[postgres-isolation-levels]]); [[consistency-models]] are the cross-node version, and "happens-before" comes from Lamport's clocks ([[time-and-clocks]]).

## Example
```java
// Config hot-reload: a background thread swaps the whole snapshot; request threads read it.
// Plain field: a reader may see the old reference forever (JIT hoisted the load) or,
// worse, see the new reference pointing at a half-constructed object on a weak CPU.
final class RatePlanCache {
    private volatile Map<String, RatePlan> plans = Map.of();   // release on write, acquire on read

    void reload(List<RatePlan> fresh) {
        Map<String, RatePlan> next = new HashMap<>();           // plain writes to a plain HashMap...
        for (RatePlan p : fresh) next.put(p.code(), p);
        plans = Map.copyOf(next);                              // ...published by ONE volatile write
    }

    RatePlan lookup(String code) {
        return plans.get(code);                                // volatile read sees the full map
    }
}
```
Readers never lock and never see a partially built map, because every write to `next` happens-before the volatile write, which happens-before any read that observes the new reference. Replacing `volatile` with a plain field compiles and passes unit tests; it fails on an ARM node under load, months later ([[immutability-and-side-effects]]).

## Pitfalls
- "Visibility problems need a lock": a lock is one way to get a happens-before edge, not the only one; `volatile`, atomics, `Executor.submit`, `BlockingQueue` hand-off and `Thread.join` all work.
- Assuming x86 behaviour is the model: your Graviton or Mac M-series CI will expose reorderings that never showed on Intel developer laptops.
- Treating `volatile` as a general "thread-safe" modifier: fine for a flag or a reference swap, wrong for counters, check-then-act or multi-field invariants.
- Double-checked locking with a non-volatile field: the reference can be published before the constructor's writes are visible. Use `volatile`, a holder class, or just `synchronized`.
- Testing for visibility bugs with unit tests: they are timing- and hardware-dependent; use `jcstress` or reason from happens-before.
- Related: [[java-memory-model]] for the exact Java rules and what `synchronized` guarantees, [[cpu-caches-and-cache-lines]] for the hardware that makes all of this necessary.
