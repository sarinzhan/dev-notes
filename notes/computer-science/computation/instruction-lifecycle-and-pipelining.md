---
title: Instruction lifecycle, pipelining and parallelism
tags: [cpu, pipeline, hazards, out-of-order, parallelism]
learned: 2026-10-27
---
## TL;DR
Every instruction passes through the same stages: **fetch** the bytes, **decode** them into micro-operations, **rename** registers, **issue** when operands are ready, **execute**, access **memory** if needed, **write back** the result, and **retire** in program order. A **pipeline** overlaps these stages so that while one instruction executes the next is decoding and the one after that is being fetched: throughput goes up to one instruction per cycle without any single instruction getting faster. The things that break the overlap are **hazards**: an instruction that needs a result not yet computed, a branch whose direction is unknown, or two instructions wanting the same unit. Modern cores go further and run several instructions per cycle, out of order, on a guess — and this, more than clock speed, is why a straight, predictable loop is fast.

## Key points
### The stages of one instruction
| Stage | What happens | Where it waits |
|---|---|---|
| Fetch | Read 16–32 bytes at the program counter from the instruction cache | Instruction-cache miss; wrong branch target |
| Decode | Split x86 bytes into fixed-size micro-operations (µops); ARM is simpler, fixed 4-byte instructions | Complex instructions; decoder width (4–6 per cycle) |
| Rename | Map the 16 architectural registers to ~200 physical ones so independent instructions that reuse a register name do not block each other | Physical registers exhausted |
| Issue / dispatch | Put the µop in the scheduler; it leaves when all its inputs exist | Inputs not ready (data hazard) |
| Execute | ALU, multiplier, address calculation; 1 cycle for add, 3 for multiply, 20+ for divide | Execution unit busy (structural hazard) |
| Memory | Load from or store to the cache hierarchy | Cache miss: 4 cycles (L1) to 300 (RAM) |
| Write back | Result lands in a physical register; dependents can now issue | — |
| Retire | Instructions leave the reorder buffer in original program order; this is the moment they "happened" | A long-latency older instruction holds up younger finished ones |

A single instruction takes 15–20 cycles from fetch to retire on a modern core, yet the core retires up to 4–6 per cycle, because up to a few hundred are in flight at once.

### Pipelining and its hazards
- **Pipelining** = overlap. With five stages and one instruction entering per cycle, five are in progress at once; the first finishes after 5 cycles, then one finishes every cycle.
- **Data hazard**: `b = a + 1; c = b * 2` — the multiply cannot issue until the add writes back. **Forwarding** passes the result directly from the execute stage to the next instruction, saving the write-back wait; a load's result cannot be forwarded until the cache answers, so a dependent instruction stalls for the full load latency.
- **Control hazard**: on a conditional jump the fetch stage does not know where to fetch from until the compare executes, many stages later. Instead of waiting, the **branch predictor** guesses and the core fetches speculatively; a wrong guess throws away everything fetched since (a 15–20 cycle flush) ([[how-cpu-executes-code]]).
- **Structural hazard**: two µops need the same unit in the same cycle; one waits. Cores have several ALUs but typically one divider and limited load ports, which is why division and scattered loads are the common bottlenecks.

### Out-of-order and speculative execution
- The scheduler issues whichever µops have their inputs, not the oldest. A load that misses the cache does not stop later independent arithmetic; the core keeps going until the reorder buffer fills with instructions waiting behind the miss.
- Everything executes on a guess (the predicted branch path) and becomes real only at retire. If the guess was wrong, the results are discarded and nothing is visible: the architectural state only moves at retirement.
- This is also why memory ordering is subtle: loads and stores may execute out of order internally; the ISA's memory model says which reorderings a program may observe, and `volatile`, fences and locks are how a language constrains them ([[memory-models-and-visibility]]).

### Levels of parallelism
| Level | Who provides it | Example |
|---|---|---|
| Instruction-level (ILP) | The core, automatically: pipelining, superscalar issue, out-of-order | Four independent adds in a loop body retire in one cycle |
| Data-level (SIMD) | Vector instructions over 4–16 lanes; the compiler or JIT emits them | Summing an `int[]` with AVX2 processes 8 ints per instruction |
| Thread-level (TLP) | Multiple cores and SMT; the OS scheduler and your thread pool | A Tomcat thread per request on 8 cores |
| Request/process-level | Multiple processes or machines | Horizontal scaling behind a load balancer ([[horizontal-vs-vertical-scaling]]) |

ILP is limited by dependencies in the code: a linked-list walk cannot overlap (each load's address comes from the previous load), an array walk can. That difference is a 10× gap for the same number of instructions ([[cpu-caches-and-cache-lines]], [[linked-lists]]).

### What a backend developer can do with this
- Keep hot loops branch-predictable and dependency-light: no virtual calls on megamorphic sites inside the loop, no `if` on random data, sequential rather than pointer-chasing data ([[arrays-and-dynamic-arrays]]).
- Let the JIT do the work: it inlines, unrolls and vectorises when the loop is simple. A method that is too large to inline or has an exception handler in the loop body blocks those optimisations ([[compilation-interpretation-jit]]).
- Measure instead of guessing: `perf stat` reports instructions per cycle (IPC), branch-miss rate and cache-miss rate. IPC under 1 on a 4-wide core means the core is waiting, usually on memory.

## Example
Five instructions through a classic five-stage pipeline (F = fetch, D = decode, X = execute, M = memory, W = write back). `i3` needs the result of the `load` in `i2`; `i5` is a branch.

```text
cycle:   1   2   3   4   5   6   7   8   9  10
i1 add   F   D   X   M   W
i2 load      F   D   X   M   W
i3 mul           F   D   -   -   X   M   W        ← stalls 2 cycles: load result ready only after M
i4 sub               F   -   -   D   X   M   W    ← waits behind i3 in order
i5 jnz                       F   D   X   M   W    ← target guessed at F; verified at X (cycle 9)
```

Without the stall the five would finish at cycle 9; the load-use dependency pushes it to 10, and a mispredicted `jnz` at cycle 9 would discard whatever was fetched in cycles 8–9 and restart. An out-of-order core would move `i4` ahead of `i3` if they were independent and fill those bubbles. The full trace of a real loop, with a cache miss and a misprediction, is in [[cpu-pipeline-walkthrough]].

## Pitfalls
- Fewer instructions is not faster. A loop with 20 independent instructions can beat one with 10 that form a dependency chain.
- A mispredicted branch is as expensive as a cache hit chain of a dozen loads; "branchless" tricks exist for a reason, but the JIT already applies them where it can — profile before hand-optimising.
- Speculation is why Spectre-class vulnerabilities exist: results of a wrong guess are discarded architecturally but leave traces in the cache. The kernel mitigations are a real, measurable cost on syscall-heavy workloads ([[operating-system-architecture]]).
- Related: [[cpu-components-and-architecture]] for the units these stages run on, [[amdahls-law-and-parallel-speedup]] for the limits of the thread-level kind.
