---
title: Thread pools, ExecutorService and Future
tags: [executor, thread-pool, future, async, spring-boot]
learned: 2026-10-22
---
## TL;DR
A **thread pool** is a set of pre-created threads that take tasks from a queue and are reused. Since Java 5 the `java.util.concurrent` package provides `Executor` / `ExecutorService` for that, `Future` for the result of a submitted task, and `Executors` as a factory. Never create threads by hand in application code.

## Key points
### The interfaces
- `Executor`: one method, `execute(Runnable)`.
- `ExecutorService`: adds `submit(Callable)` → `Future`, `invokeAll`, `shutdown()` / `shutdownNow()` / `awaitTermination()`. Since Java 19 it is `AutoCloseable`, so `try (var pool = ...)` works and waits for tasks on exit.
- `ScheduledExecutorService`: run with a delay or periodically.
- `Executors` factories: `newFixedThreadPool(n)`, `newCachedThreadPool()` (reuses idle threads, creates new ones on demand, kills threads idle for 60 s; unbounded), `newSingleThreadExecutor()`, `newVirtualThreadPerTaskExecutor()` (Java 21, see [[virtual-threads]]). Behind them is `ThreadPoolExecutor(core, max, keepAlive, queue, factory, rejectionHandler)`.

### Future and FutureTask
- `Future<V>`: `get()` blocks until the result is ready (use the timeout overload), `cancel(mayInterrupt)`, `isDone()`, `isCancelled()`.
- `FutureTask` implements both `Runnable` and `Future`: it is the task the pool runs and the handle you keep. Useful when you need to run the same computation once and let many threads wait for it.
- Composable, non-blocking alternative: [[completable-future]].

### Sizing a pool
- CPU-bound work: about `cores` to `cores + 1` threads.
- Blocking I/O: `cores * (1 + waitTime / computeTime)`, or just use virtual threads.
- For maximum throughput with minimal contention a common rule of thumb is `cores * 1.5`. Measure; the database pool is often the real limit ([[hikaricp-pool-sizing]]).

### @Async in Spring
- `@EnableAsync` + `@Async` run a bean method on another thread through a proxy (same rules as `@Transactional`: public, called from another bean).
- Plain Spring default is `SimpleAsyncTaskExecutor`, a new thread per call with no pooling. Spring Boot 2+ auto-configures a `ThreadPoolTaskExecutor` (`spring.task.execution.pool.*`, core size 8, queue unbounded by default). Customize via `AsyncConfigurer` or a named `Executor` bean. `spring.threads.virtual.enabled=true` switches it to virtual threads.

## Example
```java
ExecutorService pool = Executors.newFixedThreadPool(8);
try {
    List<Future<Report>> futures = regions.stream()
        .map(r -> pool.submit(() -> buildReport(r)))   // Callable<Report>
        .toList();
    for (Future<Report> f : futures) {
        reports.add(f.get(10, TimeUnit.SECONDS));
    }
} finally {
    pool.shutdown();
}
```

## Pitfalls
- An unbounded queue (`newFixedThreadPool`) never rejects, so under overload it eats memory; a bounded queue with a rejection policy fails fast instead.
- `newCachedThreadPool` under a burst creates thousands of threads.
- A task's exception is stored in the `Future` and only surfaces on `get()`; with `execute()` it goes to the thread's uncaught-exception handler and disappears from your logs.
- Debug async code with thread dumps, and name your threads (`ThreadFactory`) so the dump is readable.
- Pooled threads keep `ThreadLocal` values between tasks ([[threads-basics]]).
