---
title: Garbage collection in HotSpot
tags: [gc, g1, zgc, generational, stop-the-world]
learned: 2026-10-22
---
## TL;DR
The garbage collector finds objects no longer reachable from the **GC roots** and reclaims their memory; HotSpot uses tracing, not reference counting, so cycles are no problem. Every collector does mark → sweep/copy → (compact), and they differ in how much of that work happens while the application is stopped (**stop-the-world**, STW) versus concurrently. Defaults: Serial on tiny machines, G1 since Java 9 everywhere else; ZGC and Shenandoah give sub-millisecond pauses on large heaps.

## Key points
### Finding garbage
- **Reference counting**: each object carries a count of references to it; zero means garbage. Cheap and incremental, but two objects referencing each other never reach zero, and every pointer write costs an update. Not used by the JVM (used by CPython, Swift).
- **Tracing**: an object is live if it is reachable from a root, directly or through other live objects; everything else is garbage. The four kinds of roots: local variables and parameters on every thread's stack, live threads themselves, static fields of loaded classes, and JNI references (native code holding Java objects). Also: monitors held, JIT-compiled code constants, and the system class loader's classes.
- `System.gc()` is a *request* for a full collection; it is honoured by most collectors (as a full STW GC) and ignored with `-XX:+DisableExplicitGC`. It is not deprecated but should not be in application code. The weaker reference kinds that interact with the collector are in [[reference-types]].

### Phases
- **Mark**: traverse from the roots, flag live objects (a mark bit in the header or a bitmap).
- **Sweep**: free the unmarked ones, leaving holes (free lists; fragmentation).
- **Compact**: move live objects together to remove fragmentation; requires updating every reference to a moved object, which is why moving collectors need the application stopped or clever barriers.
- **Copy** (used in the young generation): copy live objects from one space to an empty one; the old space is then free as a whole. Cost is proportional to live data, not to garbage, which makes it ideal when most objects die young.

### The generational heap
- The **weak generational hypothesis**: most objects die young. So the heap ([[jvm-memory-areas]]) is split into a **young generation** (Eden plus two survivor spaces S0/S1) and an **old (tenured) generation**. Class metadata is in Metaspace (PermGen before Java 8), not in the heap.
- **Minor GC**: Eden is full → STW → live objects in Eden and the active survivor space are copied into the other survivor space; everything else is dropped in bulk. Each survivor copy increments the object's age; at a threshold (default 15, adaptive) it is **promoted** to the old generation. Large objects go straight to old.
- **Major / full GC**: the old generation fills → mark-sweep-compact over the whole heap, long STW. The goal of every modern collector is to avoid this.
- Cost model: a minor GC is cheap because it touches only live young objects; old-to-young references are tracked with a **card table** so the collector does not have to scan the whole old generation to find roots into Eden.

### How to compare collectors
- **Max pause (latency)**: the longest STW stop. Matters for request-serving services.
- **Throughput**: fraction of total time spent in application code rather than GC. Matters for batch jobs.
- **Footprint/overhead**: extra CPU (concurrent threads, barriers) and memory (remembered sets, colored pointers) the collector needs.
You cannot maximize all three; each collector picks a point.

### The collectors
| Collector | Flag | Since | Young | Old | Character |
|---|---|---|---|---|---|
| Serial | `-XX:+UseSerialGC` | 1.3 | copy, STW | mark-sweep-compact, STW | one thread; minimal footprint; default when < 2 CPUs or < 1792 MB RAM (small containers!) |
| Parallel (Throughput) | `-XX:+UseParallelGC` | 1.4 | copy, STW, multi-threaded | mark-compact, STW, multi-threaded | best throughput, long pauses; good for batch |
| CMS | `-XX:+UseConcMarkSweepGC` | 1.4 | ParNew, STW | concurrent mark-sweep, no compaction | low pause for its time; removed in Java 14 |
| **G1** | `-XX:+UseG1GC` | 7u4, default from 9 | region-based copy, STW | concurrent marking + incremental evacuation | balances pause and throughput; pause target `-XX:MaxGCPauseMillis=200` |
| **ZGC** | `-XX:+UseZGC` | 11 experimental, 15 production, generational by default since 23 | concurrent | concurrent | pauses < 1 ms regardless of heap size (up to 16 TB); colored pointers + load barriers; ~10-15 % throughput cost |
| Shenandoah | `-XX:+UseShenandoahGC` | 12 experimental, 15 production (Red Hat; not in Oracle builds) | concurrent | concurrent | same goals as ZGC via Brooks forwarding pointers; generational mode in 24 |

- CMS was the low-latency choice before G1 matured (the Java 8 default was Parallel, not CMS). Its old-generation cycle: initial mark (short STW, roots only) → concurrent mark → remark (STW) → concurrent sweep. No compaction, so fragmentation eventually forced a full STW collection ("concurrent mode failure").
- **G1** divides the heap into equal regions (1–32 MB, about 2048 of them), each Eden, survivor, old or humongous (objects larger than half a region) at any moment. Young collections evacuate live objects into fresh regions. Concurrent marking starts when old occupancy crosses 45 % (`InitiatingHeapOccupancyPercent`); then **mixed collections** evacuate the old regions with the most garbage first ("garbage first") within the pause target, so compaction is incremental. Extras: `-XX:+UseStringDeduplication`, returning idle memory to the OS (Java 12+).
- **ZGC** marks, relocates and remaps references concurrently; the only stops are root scans. A load barrier fixes stale references on access via a forwarding table. It was always free in OpenJDK; the "meant to be commercial" story refers to Oracle's early plans. Shenandoah stores a forwarding pointer per object instead of using pointer metadata bits.

### Choosing and tuning
- Java 17/21 services with 2–32 GB heaps: G1 default; set `-Xms` = `-Xmx` in containers and leave room for metaspace, threads and direct buffers ([[docker-containers-and-resources]]). p99-sensitive services with spare CPU: generational ZGC. Batch: Parallel.
- Check which collector you actually got (a one-CPU container silently gets Serial): `jcmd <pid> VM.flags`.
- Diagnose with `-Xlog:gc*`, GCeasy or JFR; allocation and promotion rates matter more than heap size. A leak shows as an old generation that never shrinks after full GCs; take a heap dump (`-XX:+HeapDumpOnOutOfMemoryError`) and look at dominators.

## Example
```bash
# Typical Spring Boot service on Java 21 in a 2 GB container, 2 CPUs
java -XX:+UseG1GC -XX:MaxGCPauseMillis=100 \
     -Xms1200m -Xmx1200m \
     -XX:MaxMetaspaceSize=256m -XX:MaxDirectMemorySize=128m \
     -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/dumps \
     -Xlog:gc*,gc+age=trace:file=/logs/gc.log:time,uptime:filecount=5,filesize=20m \
     -jar app.jar

# Same service, latency-critical: generational ZGC (default generational on 23+, flag needed on 21)
java -XX:+UseZGC -XX:+ZGenerational -Xmx1200m -Xms1200m -jar app.jar

# Reading the log: "Pause Young (Normal) (G1 Evacuation Pause) 412M->98M(1200M) 7.3ms"
# = minor GC, heap went from 412 MB to 98 MB used, 7.3 ms stop-the-world.
```

## Pitfalls
- Objects that are reachable but useless (static caches, listeners never removed, `ThreadLocal`s in pooled threads, inner classes holding the outer, [[nested-and-anonymous-classes]]) are leaks the GC cannot fix; fix the reference, or use weak/soft references ([[reference-types]]).
- `finalize` delays reclamation by a whole cycle and can resurrect objects; use `Cleaner` or `try-with-resources` ([[object-class-and-reflection]]).
- Huge humongous allocations (big byte arrays, giant `ArrayList`s) bypass the young generation in G1 and fragment old regions; stream instead of materializing.
- Setting `-Xmx` higher than the container limit minus non-heap usage gets the process OOM-killed by the kernel with no Java stack trace at all.
- Related: [[jvm-memory-areas]], [[reference-types]], [[java-memory-model]].
