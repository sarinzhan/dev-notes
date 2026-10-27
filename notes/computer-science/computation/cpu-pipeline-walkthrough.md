---
title: Inside the CPU, a walkthrough of one loop
tags: [cpu, pipeline, jit, assembly, demo]
learned: 2026-10-27
---
## TL;DR
This note follows one Java loop all the way down: source → bytecode → the machine code the JIT emits → what the core does with it, cycle by cycle, including a cache miss and a mispredicted branch. The point is to see with real numbers why the same five instructions take 6 cycles in one iteration and 300 in the next, and why the JIT's choices about inlining and layout matter more than the Java you typed.

## Key points
### The program
Sum the prices of items whose quantity is positive:

```java
long total = 0;
for (int i = 0; i < items.length; i++) {
    Item it = items[i];
    if (it.qty > 0) total += it.price;
}
```

Assume `items` has 10,000 elements, `Item` objects were allocated in order (so they sit next to each other on the heap), and about 95% have `qty > 0`.

### Step 1, bytecode
`javac` produces stack-machine bytecode: load `i`, load `items.length`, compare, jump; load `items`, load `i`, `aaload`, `getfield qty`, `ifle`, `getfield price`, `ladd`, increment, jump back. Twelve or so bytecodes per iteration, each a tiny abstract operation. The interpreter runs these one by one, ~10–30 ns each; after a few thousand iterations the JIT compiles the loop ([[compilation-interpretation-jit]]).

### Step 2, what the JIT emits (simplified x86-64)
```text
loop:
  mov   rcx, [rbx + rdi*8 + 16]   ; i1  it = items[i]          load, address from i
  mov   edx, [rcx + 12]           ; i2  it.qty                  load, address from i1
  test  edx, edx                  ; i3  qty > 0 ?               ALU, needs i2
  jle   skip                      ; i4  branch on flags         needs i3
  add   rax, [rcx + 16]           ; i5  total += it.price       load + add, needs i1
skip:
  inc   rdi                       ; i6  i++                     ALU, independent
  cmp   rdi, r8                   ; i7  i < length              needs i6
  jl    loop                      ; i8  back edge               needs i7
```

Two things the JIT already did: the array bounds check was hoisted out of the loop (it proved `i < items.length` once), and `total` lives in `rax` for the whole loop, never in memory. A third thing it did not do: `i1 → i2` is a pointer chase (the array holds references, the field lives in the object), so each iteration has a two-load dependency chain.

### Step 3, one good iteration, cycle by cycle
Everything is in L1 cache and the predictor has learned "`jle` is usually not taken, `jl` is usually taken":

```text
cycle  1   2   3   4   5   6   7   8   9  10
i1     F   D   X   M   M   M   W                 ← load, 4-cycle L1 latency
i2         F   D   -   -   -   X   M   M   M   W  ← waits for rcx from i1, then its own 4
i3             F   D   -   -   -   -   -   -   X  ← waits for edx from i2
i4                 F   D   ·   ·   ·   ·   ·   ·  ← predicted not-taken; fetch continues at i5
i5                     F   D   -   -   X   M   M  ← needs rcx (ready cycle 7); add in parallel
i6                         F   D   X   W          ← independent: runs early, out of order
i7                             F   D   X   W
i8                                 F   D   X      ← predicted taken; next iteration's i1 already fetched
```

The dependency chain `i1 → i2 → i3 → i4` is the critical path: two loads back to back are 8 cycles of latency before the branch is even verified. Everything else (`i5`–`i8`) runs in the shadow of that chain. Because the branch predictor was right, the next iteration's `i1` was fetched at cycle 9 and the loop sustains roughly one iteration per 8–9 cycles, not per 40 (the sum of all latencies). At 3 GHz that is ~3 ns per item.

### Step 4, a bad iteration: cache miss
At item 2,048 the `Item` object is on a page the core has not touched yet. `i2` misses L1, L2 and L3 and goes to RAM:

```text
cycle   1 ... 6  7  8 ...................... 300  301  302
i2      F  D  X  M  M  M  M  M  M  (waiting on RAM)  W
i3      F  D  -  -  -  -  -  -  -  (nothing to do)   X
i5..i8  issued early, done by cycle 12, then sit in the reorder buffer
next iterations: i1, i2 of items 2,049..2,060 are fetched and issued (out of order),
                 their loads also go out — the hardware prefetcher notices the stride
                 and starts pulling the following lines early
```

One iteration costs ~300 cycles, 100 ns. The reorder buffer (~250 entries) fills with instructions that finished but cannot retire behind `i2`. The saving grace: those speculative loads warm the cache for the next dozen items, so the miss is paid once per cache line, not per item. If `items` referenced objects scattered across the heap (allocated at different times, or after a compacting GC moved them), there would be no stride to prefetch and nearly every iteration would look like this. Same code, 30× slower ([[cpu-caches-and-cache-lines]]).

### Step 5, a bad iteration: misprediction
Item 5,000 has `qty == 0`. The predictor said "not taken" as usual; `i4` reaches execute at cycle ~9 and the flags say "taken":

```text
cycle   9: i4 executes, mismatch detected
           everything fetched after i4 — i5, i6, i7, i8 and the next iteration's i1, i2 — is discarded
cycle 10: fetch restarts at `skip`
cycle 25: pipeline refilled, back to full speed
```

Cost: ~15 cycles plus the work thrown away, about two normal iterations. With 5% of items mispredicted the loop loses ~10% of its speed. If the data were random 50/50, the predictor would be wrong half the time and the loop would spend more time flushing than computing; the JIT would then do better by compiling the `if` into a conditional move (`cmov`) with no branch at all — which it does, when its profile says the branch is unpredictable.

### What this shows
| Observation | Consequence for Java code |
|---|---|
| Latency is hidden only when instructions are independent | A loop body with one long dependency chain gains nothing from a wider core |
| Loads through references are the critical path | Arrays of primitives or packed fields beat arrays of objects; this is why `int[]` loops are 5–10× faster than `List<Integer>` ([[arrays-and-dynamic-arrays]]) |
| The predictor learns patterns, not probabilities | Sorted or grouped data makes the same `if` cheap; "sort first, then filter" can be faster than filtering alone |
| Memory layout decides the miss rate | Allocation order and GC compaction change performance without changing code ([[garbage-collection]]) |
| The JIT sees the profile you do not | Warm-up matters: the first thousand iterations run the interpreter, so micro-benchmarks need JMH, not `System.nanoTime` |

## Example
The three variants of the same loop and roughly what each costs per element on a server core, measured with JMH on a warm JVM (orders of magnitude, not a benchmark to quote):

```text
int[] qty, long[] price, sequential            ~0.5 ns   vectorised, no pointer chase, no miss
Item[] allocated in order, 95% predictable      ~3 ns     the walkthrough above
Item[] scattered after GC, random qty          ~60 ns     one miss + one flush per element
```

Same algorithm, same O(n), 100× apart. `perf stat -e cycles,instructions,branch-misses,cache-misses` on the running JVM shows which case you are in: IPC near 3 for the first, near 1 for the second, well under 0.5 for the third.

## Pitfalls
- Reading the JIT's assembly (`-XX:+PrintAssembly` with hsdis) shows the contract, not the timing; the pipeline diagram is only knowable from counters or a simulator.
- The walkthrough is one core model. Real cores have more stages, more ports and smarter predictors; the shape of the conclusions holds, the exact cycle counts do not.
- Do not restructure business code around this. It matters in the 1% of code that is a hot loop over large data (serialisation, aggregation, parsing); everywhere else, clarity wins and the OS and database dominate anyway.
- Related: [[instruction-lifecycle-and-pipelining]] for the stages and hazards named here, [[cpu-components-and-architecture]] for the units, [[how-cpu-executes-code]] for the short version.
