---
title: Compilation, interpretation and JIT
tags: [compilers, jit, jvm, performance, fundamentals]
learned: 2026-10-27
---
## TL;DR
Source code reaches the CPU in one of three ways. An **ahead-of-time (AOT) compiler** (C, Go, Rust, GraalVM native-image) produces machine code before the program runs. An **interpreter** (CPython, shell, the JVM's bytecode interpreter) reads an intermediate form and executes it step by step. A **just-in-time (JIT) compiler** starts interpreting, watches which code is hot, and compiles that code to machine code at runtime, using real profiling data to optimise. The JVM combines all three: `javac` is an AOT compiler to bytecode, HotSpot interprets bytecode, then C1/C2 JIT-compile hot methods; the trade-off is startup time and warm-up versus peak throughput. JVM structure itself (JVM/JRE/JDK, class loading) is in [[jvm-jre-jdk]].

## Key points
### The three strategies
| | AOT compilation | Interpretation | JIT compilation |
|---|---|---|---|
| When code becomes machine code | build time | never (interpreter is the machine code) | at runtime, for hot paths |
| Startup | fastest | fast | slow-ish: interpret first, compile later |
| Peak performance | high, but optimised for a generic CPU and no runtime profile | lowest (10–50× slower) | highest: knows the actual CPU, actual types, actual branch frequencies |
| Portability | one binary per OS/CPU | one program, many platforms | one program, JIT per platform inside the runtime |
| Memory | small | small | compiler threads, code cache, profiles |
| Examples | C, Go, Rust, GraalVM native-image | CPython, Ruby MRI (older), bash | HotSpot, V8, .NET CLR, PyPy, LuaJIT |

- Compilers all follow lexing → parsing (AST) → semantic analysis (types) → intermediate representation → optimisation → code generation; [[type-systems]] decide how much the front end can check and how much the back end can assume.
- Bytecode is the middle ground: a compact, stack-based instruction set that is cheap to interpret and easy to verify. `javac` does almost no optimisation; that is deliberately left to the JIT, which has more information.

### How HotSpot does it (tiered compilation, the default since Java 8)
1. **Interpreter** executes bytecode and counts invocations and loop back-edges per method.
2. After ~200 invocations (tier thresholds) a method goes to **C1** (client compiler): fast compile, light optimisations, inserts profiling counters.
3. After ~10–15k invocations with a profile, **C2** (server compiler) recompiles with aggressive optimisations: inlining (the single most important one), escape analysis (allocate on stack / scalar-replace objects that never escape), loop unrolling, auto-vectorisation, devirtualisation based on observed types.
4. Compiled code lives in the **code cache**; `-XX:ReservedCodeCacheSize` bounds it. When the cache fills, compilation stops and everything that is not yet compiled stays interpreted, a classic "service got slow after two weeks" cause.
- **Speculation and deoptimisation**: C2 compiles under assumptions ("this `List` is always an `ArrayList`", "this branch is never taken"). An *uncommon trap* checks the assumption; if it fails, the method is deoptimised back to the interpreter and later recompiled. A monomorphic call site that becomes megamorphic after a config change can regress noticeably.
- **On-stack replacement (OSR)** lets a long-running loop switch to compiled code mid-execution, so a `main` with one giant loop still benefits.
- Flags worth knowing: `-XX:+PrintCompilation` to see what gets compiled, `-XX:TieredStopAtLevel=1` for fast-start short-lived tools, `-Xint` to force interpretation (for debugging only).

### Where this bites a backend developer
- **Warm-up**: the first few hundred requests after a deploy are slow. Kubernetes readiness probes and load balancers should not send full traffic to a cold pod; warm it with a few synthetic calls or use CDS/CRaC (see below).
- **JMH** exists because naive benchmarks measure the interpreter, dead-code elimination or constant folding instead of the code ([[cpu]]).
- **Class Data Sharing (AppCDS)** and **CRaC** (checkpoint/restore) cut startup by snapshotting loaded classes or a whole warmed-up JVM.
- **GraalVM native-image** is AOT for Java: milliseconds startup and small RSS, but a closed world (reflection, proxies and dynamic class loading must be declared), long builds and usually lower peak throughput than C2. Spring Boot 3 supports it; it makes sense for serverless and CLI tools, rarely for a long-lived Kafka consumer.
- Project Leyden (Java 24+) adds ahead-of-time caching of profiles and compiled code to the regular JVM, moving warm-up from runtime to build time without the closed-world restriction.

### Interview-style questions folded in
- *Is Java compiled or interpreted?* Both: compiled to bytecode ahead of time, bytecode interpreted and then JIT-compiled at runtime.
- *Why can a JIT beat an AOT compiler?* It sees the real workload: which branches are taken, which implementations are actually called, which CPU instructions exist. AOT must stay correct for all inputs and a baseline CPU.
- *Why is `final` on a local variable irrelevant for performance?* The JIT infers effective finality itself; `final` on fields and classes helps mostly for readability and the memory model ([[immutable-objects]]).

## Example
Watching the JIT work on a hot method. Run with `java -XX:+PrintCompilation Warmup.java`.
```java
public class Warmup {
    // Simulates per-request work: parse an ICCID-like string and sum its digits.
    static int checksum(String iccid) {
        int sum = 0;
        for (int i = 0; i < iccid.length(); i++) {
            sum += iccid.charAt(i) - '0';
        }
        return sum;
    }

    public static void main(String[] args) {
        String iccid = "8999700000000000001";
        for (int round = 0; round < 5; round++) {
            long t0 = System.nanoTime();
            int acc = 0;
            for (int i = 0; i < 2_000_000; i++) acc += checksum(iccid);
            System.out.printf("round %d: %d ms (acc=%d)%n",
                    round, (System.nanoTime() - t0) / 1_000_000, acc);
        }
    }
}
```
Typical output: round 0 takes tens of milliseconds while `checksum` moves through the interpreter, C1 (`PrintCompilation` shows tier 3) and C2 (tier 4, with `String.charAt` inlined); later rounds take a few milliseconds. In a service the same curve appears as p99 latency during the first minute after deploy.

## Pitfalls
- Measuring without warm-up and concluding "Java is slow" or "approach A is faster": the first run measures compilation, not the code. Use JMH with warm-up iterations.
- A full code cache (`CodeCache is full. Compiler has been disabled`) silently degrades a long-running JVM to interpreted speed; monitor `jvm_memory_used_bytes{area="nonheap"}` and raise `ReservedCodeCacheSize` if needed.
- Megamorphic call sites in generic framework code (one `handle(Event)` dispatching to 20 handlers) cannot be inlined; a `switch` on a sealed type or per-type listeners keeps sites monomorphic.
- native-image surprises: a library using reflection or `ServiceLoader` works on the JVM and fails at runtime in the native binary unless reachability metadata is supplied. Test the native build in CI, not only the JVM one.
- Related: [[jvm-memory-areas]] for where the code cache and metaspace live, [[garbage-collection]] for the other runtime component that competes with the JIT for CPU, [[compilers-and-linkers]] for the OS side: object files, linking, and how a JIT makes memory executable.
