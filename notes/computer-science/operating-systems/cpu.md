---
title: CPU
tags: [cpu, hardware, registers, pipeline, parallelism, isa, operating-systems]
learned: 2026-10-28
order: 5
aliases: [cpu-components-and-architecture, how-cpu-executes-code, instruction-lifecycle-and-pipelining, cpu-pipeline-walkthrough]
---
## TL;DR
A CPU core is a few parts wired around a clock: a **control unit** that fetches and decodes instructions, an **ALU** that computes, **registers** that hold the handful of values being worked on, an **MMU** that turns virtual addresses into physical ones, and private **L1/L2 caches**, with a shared **L3**, a memory controller and a **bus** to RAM around the cores. Each instruction goes through **fetch → decode → execute → (memory) → write**. A single pass leaves most units idle, so cores **pipeline** instructions (overlapping the stages), issue several per cycle, run them **out of order** and **guess** branches. That is why the speed of code depends far more on dependencies, branch predictability and memory layout than on the number of instructions. On top of that comes **parallelism**: several cores, hardware threads, and the limits set by the serial part of the work. The [[operating-system-architecture]] overview introduces the CPU; this note goes inside it.

## Key points
### The parts of a core
| Part | Job | What it means for code |
|---|---|---|
| Clock | Ticks a few billion times a second; every stage advances on a tick | 3 GHz = 0.33 ns per cycle; a miss to RAM is ~300 cycles of waiting |
| Control unit (front end) | Fetches instruction bytes at the program counter, decodes them into micro-operations (µops), predicts branches, sends µops to the units, writes results back | Decoding x86 is hard; a hot loop that fits the decoded-µop cache runs faster |
| Registers | A few dozen 64-bit slots plus vector registers and flags; the only storage the ALU reads directly | A method whose values fit in registers is fast; "spilling" to the stack costs loads and stores |
| ALU(s) | Integer add, subtract, compare, shift, AND, OR, XOR; usually several per core | Simple integer work is almost free; the cost is in waiting for operands |
| FPU / vector units | Floating point and SIMD (AVX, NEON): one instruction over 4–16 values | The JIT auto-vectorises simple loops over arrays; `BigDecimal` never benefits |
| MMU + TLB | Translates every virtual address to a physical one using the current page table; the TLB caches recent translations | A TLB miss adds a page-table walk; huge pages help large heaps ([[memory-management]]) |
| Load/store units + L1i/L1d | Read and write memory through the caches; track outstanding misses | Memory access, not arithmetic, dominates most backend code ([[cpu-caches-and-cache-lines]]) |
| Branch predictor | Guesses the target of jumps before the condition is known | Predictable branches cost nothing; a 50/50 branch costs a pipeline flush |
| Reorder buffer / scheduler | Holds in-flight instructions, issues them when operands are ready, retires them in program order | Lets the core hide latency by running later independent instructions early |
| Bus / interconnect | Connects cores, L3, the memory controller and I/O (PCIe) | All RAM and device traffic goes through it; its bandwidth is shared |

```anim
cpu-components
```

### Why separate units, and how the ALU adds
- The core is split into units (fetch, decode, ALU, load/store) so that they can all work at once on different instructions: that is what makes pipelining possible. One unit doing everything would sit mostly idle.
- The **control unit** does not compute; it moves values around. For `sub sp, sp, #12` it reads the stack pointer from the register file (say 100), hands the ALU two plain numbers, 100 and 12, and writes the result, 88, back into `sp`. The ALU never sees register names; it only gets inputs and produces an output.
- The **ALU** is logic gates. Addition is built from adders: a *half adder* gives `sum = a XOR b`, `carry = a AND b`; a *full adder* also takes the carry from the previous bit: `sum = a XOR b XOR cin`, `cout = (a AND b) OR (cin AND (a XOR b))`. Chaining them gives a multi-bit adder; real ALUs compute all the carries in parallel (carry-lookahead) so a 64-bit add finishes in one cycle.
- The same "add" is a different thing at every level: `a + b` in Java, an `add` in assembly, a few bytes of machine code, control signals into the ALU, and finally gates. Each level translates for the one below.

```anim
alu-adder
```

### Registers
- **Small, ultrafast storage inside the core**: 32 or 64 bits each, read in under a cycle. Everything the ALU computes on must be in a register first.
- **PC** (program counter, `RIP` on x86-64): address of the next instruction. Changed by jumps, calls, returns and interrupts.
- **IR** (instruction register): the instruction being decoded and executed right now.
- **SP** and **BP** (stack and frame pointer): the top of the current thread's stack and the current frame ([[anatomy-of-a-process]]).
- **General-purpose**: 16 on x86-64, 31 on ARM64. Arguments, return values, loop counters, addresses. The calling convention fixes which registers carry the first arguments, so a call with six `int` parameters touches no memory.
- **Flags**: the result of the last compare (zero, negative, carry, overflow); conditional jumps read them.
- **Control and system registers**: page-table base (CR3 / TTBR), privilege mode, interrupt masks; writable only in kernel mode.
- Inside, a core has far more **physical** registers (~200) than the ISA names; *register renaming* maps the names onto them so independent instructions that reuse `rax` do not wait for each other.

- **Using the full register width pays.** `memchr()` (find a byte in a block of memory) used to compare one byte at a time. A 2022 patch to the Linux kernel's version loads a whole 8-byte word into a 64-bit register and checks all 8 bytes at once with a few bit tricks, which was reported about 4× faster. The function had sat unchanged for years: code compiled for a 64-bit CPU runs, but it is only faster if it actually uses the 64 bits.
- The same gap shows up everywhere: hardware ships a feature, and nothing gets faster until the software above uses it (a new GPU on an old chipset, NVMe zoned namespaces the file system does not understand, SIMD a library never calls).
### Caches: L1, L2, L3
| Level | Where | Size (typical) | Latency |
|---|---|---|---|
| L1i / L1d | per core, split into instructions and data | 32–128 KB each | ~1 ns (4 cycles) |
| L2 | per core, instructions and data | 256 KB – 2 MB | ~4–5 ns |
| L3 | shared by all cores on the chip | 16–100+ MB | ~12–40 ns |
| RAM | through the memory controller | GBs | 50–100 ns |
- Data moves between levels in 64-byte **cache lines**. A hardware **coherence protocol** keeps all copies of a line consistent across cores; that traffic is what makes contended locks and false sharing slow. The full story of lines, locality and false sharing is in [[cpu-caches-and-cache-lines]], and how the line comes from RAM is in [[memory-management]].

- **What a write costs other cores.** Core 1 and core 2 both have variable `a` cached. Core 1 writes it: its copy becomes *modified*, and the coherence protocol (MESI: modified, exclusive, shared, invalid) sends an invalidation so core 2's copy becomes *invalid*. Core 2's next read misses and fetches the line again, from L3 or from core 1, ~15–40 ns instead of 1. Whole 64-byte lines are invalidated, not single variables, so unrelated data next to a hot counter suffers too. CPython's reference counts on `None`, `True` and `False` caused exactly this traffic ([[operating-system-introduction]]).
- **Why L1 is split into L1i and L1d.** Instructions do not change while a program runs, data does. Separate caches let instruction fetch and data access happen in the same cycle, and a stream of data writes cannot evict or invalidate the hot code.
- **See your caches**: `lscpu | grep -i cache` (Linux), `sysctl -a | grep cache` (macOS), `nproc` for logical CPUs, `uname -m` for the architecture. Vendor tables often list totals: "L1d 4 × 32 KB" means 32 KB per core on 4 cores. Code whose working set fits in L1 or L2 runs at a different speed from code that does not.

### Cores, sockets and NUMA
- **Multi-core**: each core has its own front end, ALUs, registers, L1 and L2; cores share L3 and the memory controller. Threads on different cores run truly in parallel; the OS scheduler decides which thread runs where ([[process-management]]).
- **Multi-socket servers**: two or more chips, each with its own RAM (DIMM1 next to socket 1, DIMM2 next to socket 2), joined by an interconnect (Intel UPI, AMD Infinity Fabric). The hardware presents all RAM as one shared address space, so any core can read any address, but RAM attached to the other socket is slower (often 1.5–2×). This is **NUMA**, non-uniform memory access; the course slide calls the link a distributed shared memory (DSM).
- Consequence: a big JVM heap that spans both sockets sees uneven latency. `numactl --hardware` shows the layout; `-XX:+UseNUMA` makes the parallel and G1 collectors allocate a thread's young objects in its local node; databases are often pinned to one socket.

- The lecture calls the link between sockets "distributed shared memory"; in practice it is the CPU vendor's interconnect (Intel UPI, AMD Infinity Fabric). Apple's M-series puts the memory in the same package as all cores, so every core is about equally close to all of it and the NUMA question mostly disappears.

### ISA: RISC vs CISC
| | RISC (ARM64, RISC-V) | CISC (x86-64, Intel / AMD) |
|---|---|---|
| Instructions | Simple, fixed length (4 bytes); each does one thing | Variable length (1–15 bytes); one instruction can load, compute and store |
| Memory access | Only `ldr`/`str` touch memory; arithmetic works on registers (load/store architecture) | Arithmetic can take a memory operand (`add rax, [rbx]`) |
| Decoding | Easy, many instructions decoded in parallel | Hard; decoded into RISC-like micro-ops internally |
| Timing | Most instructions take one pipeline slot, predictable | Instructions take a varying number of cycles |
| Typical use | Phones, Apple Macs, AWS Graviton, other cloud ARM servers | Most servers and PCs |
- The difference is smaller than it looks: since the 1990s x86 cores translate instructions into micro-ops and run a RISC-like engine inside. ARM's advantage today is mostly power efficiency.
- The **ISA** (instruction set architecture) is the contract a program can rely on: instructions, registers, memory model. The **microarchitecture** is one implementation of it: how many decoders, how deep the pipeline, how big the caches. Two chips with the same ISA run the same binary at different speeds.
- Memory ordering is part of the ISA: x86 is strongly ordered (loads and stores mostly appear in program order), ARM is weakly ordered. The same Java code with a missing `volatile` can work on x86 and break on ARM ([[memory-models-and-visibility]], [[java-memory-model]]).
- For a backend developer: a `linux/amd64` Docker image does not run natively on an ARM machine; build multi-arch images. The JVM has a JIT back end for each ISA, so Java bytecode runs on both.

- The lecture's example, `a = a + b` with both in memory: on x86 it is presented as one instruction adding two memory locations, but x86 does not allow two memory operands either; it is `mov eax, [b]` then `add [a], eax`, two instructions, the second of which loads, adds and stores. On ARM it is four: `ldr`, `ldr`, `add`, `str`. CISC makes the code shorter, not the work smaller: the memory accesses are the same, just hidden inside an instruction. The same trade-off as an ORM hiding SQL: simpler to write, the cost is still there.

### Clock speed and instructions per cycle
- Clock speed is cycles per second: 3 GHz = 3 billion cycles, 0.33 ns each.
- Performance = **frequency × IPC** (instructions per cycle). One instruction per cycle would give 3 billion per second at 3 GHz; a modern core retires 4–6 per cycle at best, while a stall on RAM drops it far below 1.
- Since around 2005 frequency has barely grown (heat and power), so gains come from IPC, more cores and bigger caches. That is why parallelism and cache behaviour matter more than a "fast CPU".
- `perf stat` reports IPC: under 1 on a 4-wide core means the core is mostly waiting, usually on memory.

### The life of one instruction
The basic cycle has five steps:
1. **Fetch**: read the instruction at the PC from memory. The MMU translates the address, and the bytes come from L1i, or from L2, L3 or RAM on a miss.
2. **Decode**: the control unit works out the operation and its operands and reads the source registers.
3. **Execute**: the ALU (or FPU, or the address calculation for a load) computes the result.
4. **Memory** (optional): a load or store reads or writes data through L1d.
5. **Write**: the control unit writes the result into the destination register (the ALU itself does not write registers) and the PC moves on.

The course example walks `sub sp, sp, #12` at address 640 through these steps. Note that the first fetch brings a whole 64-byte line, so the next fifteen instructions are already in L1i:

```anim
cpu-instruction-cycle
```

On a real out-of-order core the same instruction passes through more detailed stages:

| Stage | What happens | Where it waits |
|---|---|---|
| Fetch | Read 16–32 bytes at the PC from the instruction cache | Instruction-cache miss; wrong branch target |
| Decode | Split instructions into fixed-size µops; ARM is simpler with fixed 4-byte instructions | Complex instructions; decoder width (4–6 per cycle) |
| Rename | Map architectural registers onto physical ones | Physical registers exhausted |
| Issue / dispatch | Put the µop in the scheduler; it leaves when all its inputs exist | Inputs not ready (data hazard) |
| Execute | ALU, multiplier, address calculation; 1 cycle for add, 3 for multiply, 20+ for divide | Execution unit busy (structural hazard) |
| Memory | Load from or store to the cache hierarchy | Cache miss: 4 cycles (L1) to 300 (RAM) |
| Write back | Result lands in a physical register; dependent µops can now issue | — |
| Retire | Instructions leave the reorder buffer in program order; this is the moment they "happened" | A long-latency older instruction holds up younger finished ones |

One instruction takes 15–20 cycles from fetch to retire, yet the core retires up to 4–6 per cycle, because a few hundred are in flight at once.

### Pipelining and its hazards
- In the walkthrough above, each part of the CPU is idle most of the time: while the ALU computes, the fetch and decode logic wait. **Pipelining** overlaps the stages: while one instruction executes, the next is decoded and the one after is fetched. With four stages and one instruction entering per cycle, four are in flight and one finishes every cycle. No instruction gets faster; throughput does.
- **Data hazard**: `b = a + 1; c = b * 2`: the multiply cannot execute until the add has produced `b`. **Forwarding** passes the result straight from execute to the next instruction; a *load*, though, cannot be forwarded until the cache answers, so a dependent instruction waits for the full load latency.
- **Control hazard**: on a conditional jump, fetch does not know where to continue until the compare executes, several stages later. Instead of waiting, the **branch predictor** guesses and the core fetches speculatively; a wrong guess throws away everything fetched since (a 15–20 cycle flush).
- **Structural hazard**: two µops need the same unit in the same cycle, so one waits. Cores have several ALUs but typically one divider and a few load ports, which is why division and scattered loads are common bottlenecks.
- **Superscalar** cores have several of each unit and start 4–8 independent instructions per cycle.

```anim
cpu-pipeline
```

- **The same idea in HTTP.** HTTP/1.1 pipelining let a client send several requests on one connection without waiting, so the server could work on the next while sending the previous response. It failed in practice: responses must come back in order (one slow request blocks the rest, head-of-line blocking) and many proxies handled it badly. HTTP/2 replaced it with independent multiplexed streams. A CPU pipeline works because the core controls every stage and can always retire in order.

### Out-of-order and speculative execution
- The scheduler issues whichever µops have their inputs ready, not the oldest. A load that misses the cache does not stop later independent arithmetic; the core keeps going until the reorder buffer fills with instructions waiting behind the miss.
- Everything executes on a guess (the predicted branch path) and becomes real only at retire. If the guess was wrong, the results are discarded and nothing is visible: the architectural state only moves at retirement.
- Discarded work still leaves traces in the cache, which is how Spectre-class attacks read data across boundaries. Their kernel mitigations (retpolines, flushes on context switch) made system calls and context switches measurably slower.
- This is also why memory ordering is subtle: loads and stores can execute out of order internally; the ISA's memory model says which reorderings a program may observe, and `volatile`, fences and locks constrain them ([[memory-models-and-visibility]]).

### Branch prediction in practice
- Predictors learn patterns from history: a loop condition that is true 999 times then false is almost free; `if (x % 2 == 0)` on random data mispredicts 50% of the time.
- Sorting an array before a data-dependent branch can make a loop several times faster purely from prediction. Branchless code (`Math.max`, conditional moves, bit tricks from [[bit-manipulation]]) avoids the problem entirely, and the JIT emits a conditional move when its profile says a branch is unpredictable.
- Virtual calls are indirect branches: the JIT's *inline cache* turns a monomorphic call site into a direct call plus a type check, which predicts perfectly; megamorphic sites (many implementations at one call site) fall back to a slower vtable jump.

### Parallelism
- An application can start several processes or threads, and the OS runs each on its own core: real parallelism. One core can only interleave them (concurrency).

| Level | Who provides it | Example |
|---|---|---|
| Instruction-level (ILP) | The core, automatically: pipelining, superscalar issue, out-of-order execution | Four independent adds in a loop body retire in one cycle |
| Data-level (SIMD) | Vector instructions over 4–16 lanes; the compiler or JIT emits them | Summing an `int[]` with AVX2 processes 8 ints per instruction |
| Thread-level (TLP) | Multiple cores and SMT; the OS scheduler and your thread pool | A Tomcat thread per request on 8 cores |
| Process / machine level | Multiple processes or machines | Horizontal scaling behind a load balancer ([[horizontal-vs-vertical-scaling]]) |

- **Amdahl's law**: the part of a task that must run serially limits the speedup. With 20% serial work, 2 cores give 1.7× and infinite cores at most 5× ([[amdahls-law-and-parallel-speedup]]).
- **More threads than cores** does not speed up CPU-bound work: the scheduler time-slices the threads and each switch costs cold caches. Size CPU-bound pools to the core count; I/O-bound work can use many more threads, or virtual threads ([[executors-and-thread-pools]], [[virtual-threads]]).
- **Simultaneous multithreading** (hyper-threading): one core presents two hardware threads that share its execution units. It helps when one thread stalls on memory and gives almost nothing when both are compute-bound: often 10–30%. A "16 vCPU" cloud instance is usually 8 cores × 2 threads.
- ILP is limited by dependencies in the code: a linked-list walk cannot overlap (each load's address comes from the previous load), an array walk can. Same instruction count, often a 10× gap ([[linked-lists]], [[arrays-and-dynamic-arrays]]).

```anim
cpu-parallelism
```

- **Some teams turn hyper-threading off.** The two hardware threads share L1, L2 and often the TLB, so a noisy neighbour on the sibling thread makes latency unpredictable: the lecturer's colleagues disabled it for steadier production timings. It is also a security boundary problem: attacks such as L1TF and MDS (2018–2019) leak data between sibling threads, so OpenBSD disabled SMT by default and cloud providers never put two customers on the same core. When the two threads run the same program they share code in L1i and it tends to help; when they run unrelated programs they fight over the caches.
- **SIMD** gives the ALU vectors instead of single values: one instruction adds 4, 8 or 16 numbers. Image and audio processing, compression, JSON parsing (simdjson), and even B+ tree searches use it to compare many keys at once. ARM's version is NEON, x86's SSE/AVX. In Java the JIT vectorises simple loops over arrays automatically, and the Vector API (`jdk.incubator.vector`) lets you write SIMD code explicitly.

### A Java loop, cycle by cycle
Sum the prices of items whose quantity is positive: 10,000 `Item` objects allocated in order (so they sit next to each other on the heap), about 95% with `qty > 0`:

```java
long total = 0;
for (int i = 0; i < items.length; i++) {
    Item it = items[i];
    if (it.qty > 0) total += it.price;
}
```

`javac` produces stack-machine bytecode; the interpreter runs it at ~10–30 ns per bytecode until, after a few thousand iterations, the JIT compiles the loop ([[compilation-interpretation-jit]]). Simplified x86-64 it emits:

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

The JIT hoisted the bounds check out of the loop and keeps `total` in `rax` the whole time. What it cannot remove: `i1 → i2` is a pointer chase (the array holds references, the field lives in the object), a two-load dependency chain per iteration.

**A good iteration**: everything in L1, the predictor has learned "`jle` usually not taken, `jl` usually taken":

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

The chain `i1 → i2 → i3 → i4` is the critical path; everything else runs in its shadow. The loop sustains roughly one iteration per 8–9 cycles, ~3 ns per item at 3 GHz, not the 40 cycles that the latencies would add up to.

**A cache miss** (item 2,048 is on a page not touched yet): `i2` waits ~300 cycles on RAM. The reorder buffer (~250 entries) fills with later instructions that cannot retire behind it, but their loads go out early, and the hardware prefetcher notices the stride and pulls the following lines. The miss is paid once per cache line, not per item. If the objects were scattered across the heap (allocated at different times, or moved by GC), there would be no stride to prefetch and nearly every iteration would cost this: same code, 30× slower.

**A misprediction** (item 5,000 has `qty == 0`): `i4` executes around cycle 9, the guess was wrong, everything fetched after it is discarded and fetch restarts at `skip`: ~15 cycles, about two normal iterations. With 5% mispredictions the loop loses ~10%; with random 50/50 data the JIT would rather emit a branchless `cmov`.

| Observation | Consequence for Java code |
|---|---|
| Latency is hidden only when instructions are independent | A loop body with one long dependency chain gains nothing from a wider core |
| Loads through references are the critical path | Arrays of primitives or packed fields beat arrays of objects: `int[]` loops are 5–10× faster than `List<Integer>` |
| The predictor learns patterns, not probabilities | Sorted or grouped data makes the same `if` cheap |
| Memory layout decides the miss rate | Allocation order and GC compaction change performance without changing code ([[garbage-collection]]) |
| The JIT sees a profile you do not | Warm-up matters: micro-benchmarks need JMH, not `System.nanoTime` |

### Where the OS and the JVM touch this
- A **context switch** saves and restores the registers above; the cost is less the copy than the cold caches, TLB and predictor afterwards ([[process-management]]).
- The **JIT** allocates Java locals to registers, inlines small methods so their values never leave registers, unrolls and vectorises simple loops. A method too large to inline, or an exception handler in the loop body, blocks those optimisations.
- **Container CPU limits** are enforced by the scheduler in time slices, not by taking cores away: a limit of `1` on a 16-core node still lets a thread run on any core, but only for 100 ms in every 100 ms window ([[docker-containers-and-resources]]).
- *Why is `i++` not atomic?* It is load → add → store, three steps; another core can interleave between them ([[race-conditions-and-critical-sections]]). `lock xadd` or `CAS` does it in one atomic instruction ([[lock-free-and-cas]]).

### CPU-bound or IO-bound? Reading `top`
A workload is **CPU-bound** when it needs more instructions than the cores can run, and **IO-bound** when it mostly waits for disk or network. The first line of `top` tells them apart:

```text
%Cpu(s):  8.0 us,  4.0 sy,  0.0 ni, 38.0 id, 50.0 wa,  0.0 hi,  0.0 si,  0.0 st
```

| Field | Meaning | High value means |
|---|---|---|
| `us` | user: your processes' own code | CPU-bound application work |
| `sy` | system: kernel code on behalf of processes (system calls, page faults) | too many small system calls, lock contention, page faults ([[kernel-vs-user-mode]]) |
| `ni` | user time of processes with a positive *nice* value (lower priority) | background jobs you deprioritised |
| `id` | idle | spare capacity, or a service waiting on the network |
| `wa` | iowait: idle while **disk** IO is outstanding | the disk is the bottleneck |
| `hi` / `si` | hardware / software interrupts | heavy network traffic (softirq) |
| `st` | steal: time the hypervisor gave your vCPU to another VM | a noisy neighbour or an oversubscribed host |

- The percentages are of **all cores**: on 4 cores, one busy core is 25%. A single process's `%CPU` column counts per core and can exceed 100% (`htop` and some tools show 400% for 4 full cores).
- **iowait is idle time, not busy time**: the core is free and the kernel can run another thread on it. It counts only block-device IO (including network file systems), not sockets: a service waiting for a slow HTTP downstream shows plain `id`.
- The lecture's demo on a 4-core Raspberry Pi: one CPU loop → 25% `us`; four copies → ~100% `us`; a Postgres `INSERT … SELECT … FROM generate_series(…)` on slow eMMC → 40–80% `wa` with little `us`.
- **100% CPU does not prove starvation.** It only says the cores were busy. Whether threads waited for a core shows in the run queue (`vmstat 1`, column `r` above the core count) and in **pressure stall information**: `/proc/pressure/cpu` gives the share of time some task was runnable but not running (the lecture calls it "performance stall information"). In containers, also check `nr_throttled` in the cgroup's `cpu.stat` ([[docker-containers-and-resources]]).
- This decides thread pool sizes: CPU-bound pools near the number of cores, IO-bound pools larger, or virtual threads ([[executors-and-thread-pools]], [[virtual-threads]]).

```anim
cpu-vs-io-bound
```

## Example
What the CPU sees when Java adds two fields (`this.total += item.price`), after the JIT, on x86-64:

```text
mov   rax, [rdi + 0x10]     ; load this.total      → load unit, L1 hit ~4 cycles
mov   rcx, [rsi + 0x18]     ; load item.price      → load unit, independent, runs in parallel
add   rax, rcx              ; ALU, 1 cycle, waits for both loads
mov   [rdi + 0x10], rax     ; store unit, buffered, does not block the next instruction
```

If `item` is hot in L1 the four instructions take ~6 cycles; if `item` came from a large array walked out of order, the second load misses to RAM and the same four take ~300 cycles while the reorder buffer looks for other work.

Branch prediction in one benchmark: the second loop is faster on random data because the branch becomes arithmetic and the loop vectorises.

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

With JMH on 1M random bytes the branchless version is typically 3–5× faster; sort the array first and the branchy version catches up, because the predictor learns the single transition point.

The same algorithm in three data layouts, roughly per element on a server core (orders of magnitude, not a benchmark to quote):

```text
int[] qty, long[] price, sequential            ~0.5 ns   vectorised, no pointer chase, no miss
Item[] allocated in order, 95% predictable      ~3 ns     the loop walkthrough above
Item[] scattered after GC, random qty          ~60 ns     one miss + one flush per element
```

`perf stat -e cycles,instructions,branch-misses,cache-misses -p <pid>` shows which case you are in: IPC near 3 for the first, near 1 for the second, well under 0.5 for the third.

## Pitfalls
- Reading high `wa` as "the CPU is busy": it is idle time waiting for the disk. Adding cores does nothing; faster storage, fewer `fsync`s or batching does.
- Reading 100% CPU as "we need more cores" without checking the run queue or `/proc/pressure/cpu`: busy is not the same as starved.
- Clock speed is not performance. A 2.5 GHz server core with a wide back end and large caches beats a 4 GHz laptop core on database workloads.
- Fewer instructions is not faster. A loop with 20 independent instructions can beat one with 10 that form a dependency chain; one cache miss or mispredicted branch outweighs dozens of ALU operations.
- "vCPU" means hardware thread, not core. Two threads of one core share its ALUs; size CPU-bound pools around physical cores ([[executors-and-thread-pools]]).
- Assuming the CPU executes your program in order. It does not, which is why unsynchronised sharing between threads breaks in ways that look impossible ([[java-memory-model]]).
- Micro-benchmarks that let the JIT see constant inputs: it folds the branch away and you measure nothing. Use JMH with `Blackhole` and random data.
- Reading the JIT's assembly (`-XX:+PrintAssembly` with hsdis) shows the contract, not the timing; the pipeline schedule is only knowable from counters.
- Running an `amd64` image on ARM hosts under emulation: it works, many times slower ("exec format error" without emulation).
- Do not restructure business code around this. It matters in the 1% that is a hot loop over large data (serialisation, aggregation, parsing); elsewhere clarity wins, and the OS and database dominate anyway.
- Related: [[memory-management]] for how memory reaches the core, [[cpu-caches-and-cache-lines]] for caches and false sharing, [[numbers-bits-and-floating-point]] for what the ALU and FPU actually compute, [[latency-numbers]] for the numbers to remember.
