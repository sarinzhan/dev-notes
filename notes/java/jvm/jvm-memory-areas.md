---
title: JVM memory areas
tags: [jvm, heap, stack, metaspace, memory-layout]
learned: 2026-10-22
---
## TL;DR
The JVM splits memory into the shared **heap** (all objects, divided into young and old generations), one private **stack** per thread (frames with locals and operand stacks), **Metaspace** (class metadata, replaced PermGen in Java 8), the **code cache** (JIT output) and assorted **native memory** (direct buffers, thread stacks, GC structures). A Java process uses far more than `-Xmx`; sizing a container means adding all of these up.

## Key points
### Heap
- Created at JVM start, shared by all threads, home of every object and array; the string pool lives here since Java 7 ([[strings-and-string-pool]]). Bounded by `-Xms` (initial) and `-Xmx` (max); default max is 25 % of the container/host RAM.
- Generational layout under G1/Parallel/Serial: **Eden** (new objects), two **survivor** spaces (S0/S1, one always empty), **old/tenured** (survivors of several minor GCs). G1 uses equally sized regions tagged with one of those roles; ZGC has no fixed generation sizes. Details and the collectors: [[garbage-collection]].
- Allocation is a pointer bump inside a thread-local allocation buffer (**TLAB**), which is why `new` is cheap; the JIT may also scalar-replace or stack-allocate objects that escape analysis proves local.
- Exhaustion: `OutOfMemoryError: Java heap space`; `GC overhead limit exceeded` when > 98 % of time goes to GC recovering < 2 % of the heap.

### Object layout
- Header: **mark word** (identity hash, age bits, lock state; 8 bytes) + **class pointer** (4 bytes with compressed class pointers, default) → 12 bytes, padded to 8-byte alignment. An empty object is 16 bytes; `new Integer(1)` is 16 bytes for a 4-byte payload.
- Compressed oops: with heaps under 32 GB, references are 4 bytes instead of 8. A heap of 33 GB can hold *fewer* objects than one of 31 GB.
- Fields are reordered for alignment; arrays carry a length. Measure with JOL (`jol-core`) when it matters.

### Thread stack
- Each thread gets its own stack at creation (`-Xss`, default 1 MB on 64-bit Linux; `Thread` constructor can override). It holds one **frame** per active method call: local variables (including `this` and parameters), the operand stack for bytecode arithmetic, and a reference to the constant pool. Primitives and references live here; the objects they point to are on the heap.
- Stack data is thread-private, so it needs no synchronization ([[java-memory-model]]); frames are freed on return with no GC involvement.
- Overflow: `StackOverflowError`, almost always unbounded recursion. Deeper recursion → raise `-Xss` or make the algorithm iterative ([[data-structures]]).
- Virtual threads ([[virtual-threads]]) keep their stacks as heap objects (stack chunks) and copy them to a carrier's stack when mounted, which is what makes millions of them affordable.
- Native method stacks: used for JNI code; in HotSpot they are the same OS thread stack.

### Metaspace (and PermGen before it)
- Class metadata: the runtime representation of each loaded class (methods, bytecode, constant pool, vtables, annotations). Java ≤ 7 kept this in **PermGen**, a fixed-size part of the heap that produced `OutOfMemoryError: PermGen space` on redeploys and with many classes. Java 8 moved it to **Metaspace** in native memory, which grows on demand; cap with `-XX:MaxMetaspaceSize`. Interned strings and static fields moved to the heap at the same time.
- Memory is freed only when a class loader and all its classes become unreachable ([[class-initialization-order]]). Leaking loaders (hot redeploy, dynamic proxies/lambdas generated without bound, Groovy scripts) grow Metaspace without limit.
- Compressed class space is a separate 1 GB reservation for the class pointers (`-XX:CompressedClassSpaceSize`).

### Runtime constant pool and method area
- Per class: literals, symbolic references to fields/methods/classes resolved lazily; part of Metaspace. The spec calls the logical region "method area"; Metaspace is HotSpot's implementation.

### Code cache
- JIT-compiled native code, interpreter stubs, adapters. `-XX:ReservedCodeCacheSize` (240 MB default with tiered compilation). When it fills, the JIT stops compiling and the application slows down drastically with a "CodeCache is full" warning.

### Native memory (off-heap)
- Direct `ByteBuffer`s ([[java-nio]]), capped by `-XX:MaxDirectMemorySize` (defaults to `-Xmx`); Netty, Kafka clients and JDBC drivers use them.
- Thread stacks (`threads × Xss`), GC working memory (G1 remembered sets can be 10 % of the heap), JIT compiler arenas, symbol tables, memory-mapped files, native libraries, Foreign Function & Memory API arenas (Java 22).
- Inspect with `-XX:NativeMemoryTracking=summary` + `jcmd <pid> VM.native_memory summary`.

### Sizing a container
`RSS ≈ heap (Xmx) + Metaspace + code cache + threads × Xss + direct memory + GC overhead + JVM itself`. A 1 GB `-Xmx` service typically needs a 1.5–2 GB container. Since Java 10 (`UseContainerSupport`) the JVM reads the cgroup limit and sets the default heap from it; `-XX:MaxRAMPercentage=75` is a more robust setting than a hard `-Xmx` when the container size changes ([[docker-containers-and-resources]]).

## Example
```java
public class OrderService {
    private static final Map<String, Tariff> TARIFFS = new HashMap<>();   // reference in Metaspace (static),
                                                                          // HashMap object and entries on the heap
    private final PricingRules rules;                                      // field: inside the OrderService heap object

    public long price(String tariffCode, int units) {                     // frame on the calling thread's stack:
        Tariff t = TARIFFS.get(tariffCode);                                //   this, tariffCode, units, t (reference)
        long base = t.unitPriceMinor() * units;                            //   base (primitive, lives in the frame)
        return rules.apply(base);                                          // new frame pushed for apply()
    }                                                                      // frame popped; base gone, t's object stays
}                                                                          // until the GC finds it unreachable
```
```bash
# What the process really uses vs the heap you configured
jcmd <pid> GC.heap_info
jcmd <pid> VM.native_memory summary      # requires -XX:NativeMemoryTracking=summary
ps -o rss= -p <pid>                       # resident set size in KB, compare with -Xmx
```

## Pitfalls
- "Objects on the heap, primitives on the stack" is only true for locals; primitive *fields* and array elements live inside their object on the heap.
- `-Xmx` is not the memory limit of the process; the kernel OOM-killer, not the JVM, ends the story when the sum exceeds the cgroup limit.
- Thousands of platform threads × 1 MB stacks is real memory (reserved, committed on touch); thread pools and virtual threads exist for this reason ([[executors-and-thread-pools]]).
- Metaspace growth after each redeploy or with heavy use of dynamic class generation (Mockito, CGLIB proxies in tests, Groovy) is a class-loader leak, not a reason to raise the limit forever.
- Related: [[garbage-collection]], [[jvm-jre-jdk]], [[java-memory-model]], [[reference-types]].
