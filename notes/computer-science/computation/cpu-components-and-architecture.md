---
title: CPU components and architecture
tags: [cpu, hardware, registers, cores, isa]
learned: 2026-10-27
---
## TL;DR
A CPU core is a small set of parts wired around a clock: a **control unit** that reads instructions and steers everything else, an **ALU** that does arithmetic and logic, **registers** that hold the few values being worked on right now, and **load/store units** that move data between registers and the cache hierarchy. A modern chip repeats that core many times, often lets each core run two hardware threads, and surrounds them with shared caches, a memory controller and an interconnect. The **ISA** (x86-64, ARM64) is the contract the software sees; the **microarchitecture** is how a given chip implements it, and that is what changes between generations.

## Key points
### The parts of one core
| Part | Job | What it means for code |
|---|---|---|
| Clock | Ticks a few billion times a second; every stage advances on a tick | 3 GHz = 0.33 ns per cycle; a cache miss to RAM is ~300 cycles of waiting |
| Control unit / front end | Fetches instruction bytes, decodes them into micro-operations, predicts branches | Decoding x86 is hard; a hot loop that fits the decoded-µop cache runs faster |
| Registers | 16–32 general-purpose 64-bit slots plus vector registers and flags; the only storage the ALU reads directly | A method whose values fit in registers is fast; "spilling" to the stack costs loads and stores |
| ALU(s) | Integer add, subtract, compare, shift, logic; usually several per core | Simple integer work is almost free; the cost is in waiting for operands |
| FPU / vector units | Floating point and SIMD (AVX, NEON): one instruction over 4–16 values | The JIT auto-vectorises simple loops over arrays; `BigDecimal` never benefits |
| Load/store units + L1 cache | Read and write memory through the cache; track outstanding misses | Memory access, not arithmetic, dominates most backend code ([[cpu-caches-and-cache-lines]]) |
| Branch predictor | Guesses the target of jumps before the condition is known | Predictable branches cost nothing; a 50/50 branch costs a pipeline flush ([[how-cpu-executes-code]]) |
| Reorder buffer / scheduler | Holds in-flight instructions, issues them when operands are ready, retires in program order | Lets the core hide latency by running later independent instructions early |

### Registers in a little more detail
- **Program counter** (instruction pointer): address of the next instruction. Changed by jumps, calls, returns and interrupts.
- **Stack pointer** and **frame pointer**: where the current thread's stack top and frame are ([[anatomy-of-a-process]]).
- **General-purpose**: arguments, return values, loop counters, addresses. The calling convention fixes which registers carry the first arguments, so a call with six `int` parameters touches no memory.
- **Flags**: the result of the last compare (zero, negative, carry, overflow); conditional jumps read them.
- **Control and system registers**: page-table base, privilege mode, interrupt masks; writable only in kernel mode ([[operating-system-architecture]]).

### ISA vs microarchitecture
- The **instruction set architecture** defines the instructions, registers and memory model a program can rely on. x86-64 and ARM64 are the two that matter for servers; the JVM has a JIT back end for each.
- The **microarchitecture** is one implementation: how many decoders, how deep the pipeline, how big the caches, how aggressive the predictor. Two chips with the same ISA run the same binary at different speeds.
- Memory ordering is part of the ISA: x86 is strongly ordered (loads and stores mostly appear in program order), ARM is weakly ordered. The same Java code with a missing `volatile` can work on x86 and break on ARM ([[memory-models-and-visibility]], [[java-memory-model]]).

### Beyond one core
- **Multi-core**: each core has its own L1/L2 cache and its own front end; cores share L3 and the memory controller. Threads on different cores run truly in parallel; the OS scheduler decides which thread lands where ([[cpu-scheduling]]).
- **Simultaneous multithreading** (hyper-threading): one core presents two hardware threads that share its execution units. Useful when one thread stalls on memory; near zero gain when both are compute-bound. A "16 vCPU" cloud instance is usually 8 cores × 2 threads.
- **Cache coherence**: hardware keeps every core's copy of a cache line consistent; the protocol traffic is what makes contended locks and false sharing expensive ([[cpu-caches-and-cache-lines]]).
- **NUMA**: on multi-socket servers each socket has its own RAM; touching the other socket's memory is slower. Large JVM heaps spanning sockets can see uneven latencies.

### Where the OS and the JVM touch this
- A context switch saves and restores the registers above; the cost is less the copy than the cold caches and predictor state afterwards ([[processes-and-threads]]).
- The JIT allocates Java locals to registers, inlines small methods so their values never leave registers, and emits vector instructions for simple loops ([[compilation-interpretation-jit]]).
- Container CPU limits are enforced by the scheduler in time slices, not by taking cores away: a limit of `1` on a 16-core node still lets a thread run on any core, but only for 100 ms in every 100 ms window ([[docker-containers-and-resources]]).

## Example
What the CPU sees when Java adds two fields (`this.total += item.price`), after the JIT, on x86-64:

```text
mov   rax, [rdi + 0x10]     ; load this.total      → load unit, L1 hit ~4 cycles
mov   rcx, [rsi + 0x18]     ; load item.price      → load unit, independent, runs in parallel
add   rax, rcx              ; ALU, 1 cycle, waits for both loads
mov   [rdi + 0x10], rax     ; store unit, buffered, does not block the next instruction
```

Four instructions, two of them memory accesses. If `item` was just created it is hot in L1 and the whole thing takes ~6 cycles; if `item` came from a large array walked out of order, the second load misses to RAM and the same four instructions take ~300 cycles while the reorder buffer tries to find other work.

## Pitfalls
- Clock speed is not performance. A 2.5 GHz server core with wide execution and large caches beats a 4 GHz laptop core on database workloads.
- "vCPU" means hardware thread, not core. Two threads of the same core share its ALUs; plan thread pools around physical cores for CPU-bound work ([[executors-and-thread-pools]]).
- The CPU does not execute Java, or even x86 as written: the front end translates instructions to internal micro-operations and the back end reorders them. What you read in `perf` disassembly is the contract, not the schedule ([[instruction-lifecycle-and-pipelining]]).
- Related: [[cpu-pipeline-walkthrough]] for a loop traced cycle by cycle, [[numbers-bits-and-floating-point]] for what the ALU and FPU actually compute.
