---
title: CPU caches, cache lines and false sharing
tags: [cpu, cache, performance, concurrency, memory]
learned: 2026-10-27
---
## TL;DR
Main memory is ~100× slower than the core, so each core keeps small, fast copies of recently used memory in **L1/L2 caches**, with a larger **L3** shared by all cores. Caches move data in fixed **cache lines** (64 bytes on x86 and most ARM), not single bytes. Code is fast when it has **locality**: it reuses the same lines (temporal) or walks memory sequentially (spatial). Two threads writing to different variables that happen to share one line cause **false sharing**, where the line ping-pongs between cores and a "lock-free" counter becomes slower than a synchronized one. This is the hardware reason behind "arrays beat linked lists" ([[arrays-and-dynamic-arrays]], [[linked-lists]]) and behind `@Contended` and striped counters in the JDK.

## Key points
### The hierarchy
| Level | Typical size | Latency | Shared by |
|---|---|---|---|
| Registers | ~1 KB | 0–1 cycle | one core |
| L1 (split: data + instructions) | 32–64 KB each | ~4 cycles | one core |
| L2 | 512 KB – 2 MB | ~12–14 cycles | one core |
| L3 (LLC) | 16–96 MB | ~40–60 cycles | all cores on the socket |
| DRAM | GBs | ~70–100 ns (200–300 cycles) | everything |

- A load first checks L1, then L2, L3, then memory; each miss costs the latency of the next level. The CPU also *prefetches* lines ahead of a sequential access pattern, which is why linear scans run near memory bandwidth while random access runs at DRAM latency ([[latency-numbers]]).
- The **TLB** caches virtual-to-physical address translations; a TLB miss adds a page-table walk. Huge pages (`-XX:+UseTransparentHugePages`) reduce TLB misses for large heaps ([[memory-management]]).

### Cache lines and locality
- Memory is fetched in 64-byte lines. Reading one `int` pulls in its 15 neighbours for free; iterating `int[]` touches a new line every 16 elements, iterating a `LinkedList<Integer>` touches at least one new line per element (node object) plus another for the boxed `Integer`.
- **Spatial locality**: data used together should be stored together. Arrays of primitives, `record` fields laid out in one object, columnar layouts for analytics ([[column-and-wide-column-stores]]).
- **Temporal locality**: data used recently will be used again soon. Process a chunk fully before moving on (loop blocking), keep hot structures small enough to stay in L2.
- Object layout in the JVM: a 12–16 byte header, fields reordered by the JVM to minimise padding, references of 4 bytes with compressed oops. A `HashMap` lookup is pointer chasing: table → node → key object → key's fields, each potentially a separate line ([[hashmap-internals]], [[hash-tables]]).

### Coherence and false sharing
- Cores keep caches consistent with the **MESI** protocol (Modified, Exclusive, Shared, Invalid): to write a line a core must own it exclusively, which invalidates the copies in other cores. The next read on another core misses and fetches the line again.
- Coherence is per *line*, not per variable. If thread A writes `counterA` and thread B writes `counterB` and both sit in the same 64 bytes, every write invalidates the other core's copy: **false sharing**. Throughput can drop 10× with no visible data race.
- Fixes: pad hot fields to their own line (`@jdk.internal.vm.annotation.Contended`, needs `-XX:-RestrictContended`), or manually add seven unused `long` fields; use one counter per thread and sum on read (`LongAdder` does exactly this with its striped cells); keep per-thread state in separate objects, not adjacent array slots.
- True sharing of a hot line (one `AtomicLong` incremented by 16 threads) suffers the same transfer cost; `CAS` contention is partly cache-line contention ([[lock-free-and-cas]]).

### Interview-style questions folded in
- *Why is `ArrayList` faster than `LinkedList` even for inserts in the middle?* Moving a contiguous block with `System.arraycopy` is a sequential, prefetched memory operation; walking a linked list to the position is one cache miss per node.
- *What is memory-bound vs CPU-bound?* Memory-bound code waits on cache misses; adding cores barely helps and the fix is layout, not algorithmic tricks. Profilers show low IPC for such code.
- *Does `volatile` flush the cache?* No. Caches are always coherent; `volatile` restricts reordering and store-buffer visibility ([[memory-models-and-visibility]], [[java-memory-model]]).

## Example
Two threads incrementing adjacent fields versus padded fields. Run with JMH; the padded version is typically 5–10× faster on a multi-core machine.
```java
public class FalseSharingDemo {

    // Both counters share one 64-byte line: each write invalidates the other core's copy.
    static final class Packed {
        volatile long a;
        volatile long b;
    }

    // Padding pushes b onto a different line. (@Contended does this without the noise.)
    static final class Padded {
        volatile long a;
        long p1, p2, p3, p4, p5, p6, p7;
        volatile long b;
    }

    static void run(Runnable incA, Runnable incB) throws InterruptedException {
        Thread t1 = new Thread(() -> { for (int i = 0; i < 100_000_000; i++) incA.run(); });
        Thread t2 = new Thread(() -> { for (int i = 0; i < 100_000_000; i++) incB.run(); });
        t1.start(); t2.start(); t1.join(); t2.join();
    }

    public static void main(String[] args) throws InterruptedException {
        Packed p = new Packed();
        long t0 = System.nanoTime();
        run(() -> p.a++, () -> p.b++);
        System.out.printf("packed: %d ms%n", (System.nanoTime() - t0) / 1_000_000);

        Padded q = new Padded();
        t0 = System.nanoTime();
        run(() -> q.a++, () -> q.b++);
        System.out.printf("padded: %d ms%n", (System.nanoTime() - t0) / 1_000_000);
    }
}
```
`LongAdder`, `ConcurrentHashMap`'s size counters and Disruptor-style ring buffers all rely on this trick.

## Pitfalls
- Adding padding fields the JIT can remove: unused non-volatile fields may be dropped or reordered. Use `@Contended` or make the padding fields part of a superclass hierarchy (the classic `long p1..p7` trick in a parent class) so layout is stable.
- Reasoning about Java object layout by field declaration order: HotSpot reorders fields by size and type. Check with JOL (`jol-core`) rather than guessing.
- Benchmarks on a laptop with 2 cores or with threads pinned to the same core show no false sharing; the effect appears only across physical cores and sockets.
- Over-sized "optimised" structures: padding everything evicts real data from L1. Pad only fields written concurrently by different threads.
- Related: [[how-cpu-executes-code]] for the pipeline side, [[lru-cache-design]] and [[caching]] for caches at the software level, which follow the same locality logic.
