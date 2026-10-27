---
title: How a CPU executes code
tags: [cpu, computer-architecture, performance, fundamentals]
learned: 2026-10-27
---
## TL;DR
A CPU core runs an endless **fetch → decode → execute → write-back** loop over machine instructions, using a handful of fast **registers** as working storage. Modern cores **pipeline** many instructions at once, run several per cycle (superscalar), reorder them (out-of-order execution) and **guess** which way a branch goes before they know. The result: raw instruction count matters much less than how predictable the code is and how far away its data lives ([[cpu-caches-and-cache-lines]]). For a Java developer this explains why the JIT ([[compilation-interpretation-jit]]) cares about inlining and why "branchy" or pointer-chasing code is slow even with a low [[big-o-and-complexity|Big-O]].

## Key points
### The basic cycle
- **Fetch**: read the instruction at the address in the *program counter* (PC / `RIP` on x86-64) from memory (really from the instruction cache).
- **Decode**: figure out the opcode and operands; x86 decodes variable-length instructions into fixed-size micro-ops (uops).
- **Execute**: an ALU adds, compares, shifts; a load/store unit reads or writes memory; an FPU/SIMD unit handles floating point and vectors.
- **Write-back**: store the result into a register or memory, update flags (zero, carry, overflow), move the PC.
- Everything the program "sees" is registers (16 general-purpose on x86-64, 31 on ARM64), memory addresses, and flags. Local variables of a JIT-compiled Java method live in registers when the compiler can keep them there; otherwise on the stack ([[stack-vs-heap-memory]]).

### Why one instruction per cycle is not the ceiling
| Technique | Idea | What it costs when it fails |
|---|---|---|
| Pipelining | split the cycle into stages, each stage works on a different instruction; 14–20 stages on modern x86 | a stall drains the pipeline |
| Superscalar | several execution units, 4–8 instructions dispatched per cycle | dependent instructions cannot run in parallel |
| Out-of-order execution | run later independent instructions while an earlier one waits for memory | hardware complexity; still bounded by the reorder buffer |
| Branch prediction | guess the direction of `if`/loops and speculatively execute | a mispredict throws away 15–20 cycles of work |
| Speculative execution | execute past loads and branches before they resolve | side-channel attacks (Spectre/Meltdown) and their mitigations |
| SIMD (AVX, NEON) | one instruction on 4–16 lanes of data | only helps straight-line numeric code; the JIT auto-vectorises simple loops, Java's Vector API is still incubating |

- **Clock speed vs IPC**: performance = frequency × instructions per cycle. Since ~2005 frequency barely moved; gains came from IPC, more cores and bigger caches. This is why [[amdahls-law-and-parallel-speedup|parallelism]] and cache behaviour matter more than "fast CPU".
- **Latency hierarchy**: register ~0 cycles, L1 ~4, L2 ~12, L3 ~40, DRAM ~200–300 cycles. A single cache miss costs more than a hundred simple instructions ([[latency-numbers]]).

### Branch prediction in practice
- Predictors learn patterns from history: a loop condition that is true 999 times then false is almost free; `if (x % 2 == 0)` on random data mispredicts 50% of the time.
- Sorting an array before a data-dependent branch can make the loop several times faster, purely from prediction. Branchless code (`Math.max`, conditional moves, bit tricks from [[bit-manipulation]]) avoids the problem entirely.
- Virtual calls are indirect branches: the JIT's *inline cache* turns a monomorphic call site into a direct call plus a type check, which predicts perfectly; megamorphic sites (many implementations at one call site) fall back to a slow vtable jump.

### Interview-style questions folded in
- *What is a register?* The fastest storage in the machine, a few dozen named slots the ALU operates on directly; everything else must be loaded into one first.
- *What is a context switch and why is it expensive?* Saving all registers and the PC of one thread, loading another's, and losing cache and TLB contents; see [[processes-and-threads]].
- *Why is `i++` not atomic?* It is load → add → store, three instructions; another core can interleave between them ([[race-conditions-and-critical-sections]], [[lock-free-and-cas]] for the single-instruction `CAS` fix).
- *What does "memory reordering" have to do with the CPU?* Out-of-order execution and store buffers let loads and stores become visible in a different order than the program wrote them; fences and `volatile` restore order ([[memory-models-and-visibility]]).

## Example
Same work, two loops; the second is faster on random data because the branch is replaced with arithmetic and the loop auto-vectorises.
```java
// Count values >= 128 in a byte array of random data.
static int branchy(byte[] data) {
    int count = 0;
    for (byte b : data) {
        if ((b & 0xFF) >= 128) {   // unpredictable branch: ~50% mispredict on random input
            count++;
        }
    }
    return count;
}

static int branchless(byte[] data) {
    int count = 0;
    for (byte b : data) {
        count += ((b & 0xFF) >> 7); // bit 7 is 1 exactly when value >= 128; no branch
    }
    return count;
}
```
With a JMH benchmark on 1M random bytes the branchless version is typically 3–5× faster; sort the array first and the branchy version catches up, because the predictor learns the single transition point.

## Pitfalls
- Judging performance by counting source lines or even bytecodes: one cache miss or one mispredicted branch outweighs dozens of ALU operations. Measure with JMH and look at cache-miss and branch-miss counters (`perf stat`) before micro-optimising.
- Assuming the CPU executes your program in order. It does not, which is why unsynchronised sharing between threads breaks in ways that look impossible ([[java-memory-model]]).
- Micro-benchmarks that let the JIT see constant inputs: it folds the branch away and you measure nothing. Use JMH `Blackhole` and random data.
- Spectre-class mitigations (retpolines, flush on context switch) made syscalls and context switches noticeably slower; a thread-per-request design pays this on every blocking call ([[io-models-blocking-nonblocking-epoll]], [[virtual-threads]]).
- Related: [[cpu-caches-and-cache-lines]] for the memory side of the story, [[jvm-memory-areas]] for how the JVM maps onto this hardware.
