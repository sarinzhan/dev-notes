---
title: Virtual threads (Project Loom)
tags: [concurrency, jvm, java21]
learned: 2026-10-14
---
## TL;DR
Virtual threads are cheap, JVM-managed threads mounted on a small pool of carrier (platform) threads. They make the classic thread-per-request model scale to millions of concurrent blocking calls without rewriting code into [[completable-future|async style]].

## Key points
- Preview in Java 19, final in Java 21 (JEP 444). A virtual thread costs about 1 KB of heap; a platform thread reserves 1–2 MB of stack.
- Platform (carrier) threads are OS threads under **preemptive** multitasking: the OS interrupts a thread after its time slice whether it finished or not. Virtual threads are **cooperative**: a virtual thread runs on a carrier until it blocks, then yields.
- Lifecycle: the JVM mounts the virtual thread on a free carrier from a `ForkJoinPool` → on a blocking operation (I/O, `sleep`, JDBC) it **parks**, its stack is copied to the heap and the carrier is freed → when the result arrives (data from the network) the JVM mounts it on a free carrier again.
- Created with `Thread.ofVirtual().start(r)` or `Executors.newVirtualThreadPerTaskExecutor()`.
- Not faster for CPU-bound work. The win is *throughput of blocking I/O*, not latency.
- Spring Boot 3.2+: `spring.threads.virtual.enabled=true` switches Tomcat and `@Async` to virtual threads.

## Example
```java
try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
    List<Future<Profile>> futures = ids.stream()
        .map(id -> executor.submit(() -> client.fetchProfile(id)))
        .toList();
    for (var f : futures) profiles.add(f.get());
} // waits for all tasks
```

## Pitfalls
- **Pinning**: blocking inside a `synchronized` block or a native call keeps the carrier busy. Use `ReentrantLock` on hot paths. Check with `-Djdk.tracePinnedThreads=full`.
- Connection pools become the real limit: 10 000 virtual threads still share 20 HikariCP connections. See [[hikaricp-pool-sizing]].
- Don't pool virtual threads. Create one per task.

## Links
- JEP 444
- Related: [[completable-future]], [[spring-kafka-consumer]]
