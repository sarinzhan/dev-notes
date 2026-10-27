---
title: CompletableFuture essentials
tags: [concurrency, async, java]
learned: 2026-09-30
---
## TL;DR
`CompletableFuture` is Java's promise: compose async steps with `thenApply`, `thenCompose`, `thenCombine`, `allOf`, and handle failures with `exceptionally` / `handle` / `whenComplete`. Never run blocking I/O on the default `ForkJoinPool.commonPool()`; pass your own executor. With [[virtual-threads]] (Java 21) plain blocking code covers most cases, but `CompletableFuture` is still the tool for fan-out/fan-in and timeouts.

## Key points

### Future vs CompletableFuture
- `Future` (Java 5) can only be polled or blocked on with `get()`; no callbacks, no composition, no manual completion ([[executors-and-thread-pools]]).
- `CompletableFuture` (Java 8) implements `Future` and `CompletionStage`: chainable callbacks, combinators, explicit `complete()`/`completeExceptionally()`, and (Java 9) `orTimeout`, `completeOnTimeout`, `delayedExecutor`, `copy`, `minimalCompletionStage`.

### Composition
| Method | Analogy | Use |
|---|---|---|
| `thenApply(fn)` | map | transform the result synchronously |
| `thenCompose(fn -> CF)` | flatMap | next step is itself async; avoids `CF<CF<T>>` |
| `thenCombine(other, bi)` | zip | two independent futures, both results needed |
| `thenAccept` / `thenRun` | forEach | side effect, returns `CF<Void>` |
| `allOf(cf...)` | join all | returns `CF<Void>`; read results with `join()` on each input afterwards |
| `anyOf(cf...)` | race | first to complete, including exceptionally |
- `*Async` variants (`thenApplyAsync`) run the callback on the executor instead of on the thread that completed the previous stage. Without `Async`, the callback runs on the thread that completes the stage, or on the **registering thread** if the stage was already complete when you chained it.

### Errors
- `exceptionally(ex -> fallback)`: recover, like `catch`. `handle((res, ex) -> ...)`: always called, choose result. `whenComplete((res, ex) -> ...)`: observe only, exception propagates.
- The exception arrives wrapped in `CompletionException`; unwrap with `ex.getCause()` before `instanceof`. `join()` throws unchecked `CompletionException`, `get()` throws checked `ExecutionException` ([[exceptions]]).
- Java 12 added `exceptionallyCompose` for an async fallback.

### Executors and the common pool
- `supplyAsync(s)` without an executor uses `ForkJoinPool.commonPool()`: `cores - 1` threads shared by the whole JVM, parallel streams included ([[fork-join-framework]]). Blocking HTTP or JDBC calls there starve everything else; on a 2-vCPU container the pool has one thread.
- Always pass a dedicated executor in services, name its threads, size it for the I/O it will block on, and make it a Spring bean so it is shut down gracefully. On Java 21, `Executors.newVirtualThreadPerTaskExecutor()` is the simplest choice for I/O fan-out.
- The common pool does not propagate MDC, `SecurityContext`, or the Spring transaction ([[spring-transactional]]); copy context explicitly or use Micrometer's `ContextExecutorService`.

### Timeouts and cancellation
- `orTimeout(2, SECONDS)` (Java 9) completes the future with `TimeoutException`; `completeOnTimeout(default, 2, SECONDS)` completes it with a fallback value. Both run on a single shared scheduler thread; keep the callbacks light.
- A timeout or `cancel(true)` only changes the state of the future; the task keeps running and `mayInterruptIfRunning` is ignored. To stop real work, use a client with its own timeout (HttpClient, WebClient) or check `Thread.interrupted()` in the task.
- Cancelling a derived stage does not cancel its source.

### Virtual threads (Java 21)
- With virtual threads, "call three services in parallel" can be three `executor.submit` calls plus `get()`, or a `StructuredTaskScope` (preview until Java 24, final in 25). Blocking is cheap, stack traces are readable, no callback chains.
- `CompletableFuture` still wins for: timeouts composed with fallbacks, racing (`anyOf`), integrating with libraries that return `CompletionStage` (Spring `@Async`, Kafka `send()` returns `CompletableFuture` since Spring Kafka 3.0).

## Example
```java
@Service
public class SubscriberSummaryService {
    private final ExecutorService io = Executors.newVirtualThreadPerTaskExecutor();

    public Summary summary(String msisdn) {
        var balance = CompletableFuture.supplyAsync(() -> billing.balance(msisdn), io);
        var plan    = CompletableFuture.supplyAsync(() -> crm.plan(msisdn), io);
        var sims    = CompletableFuture.supplyAsync(() -> provisioning.sims(msisdn), io)
                          .completeOnTimeout(List.of(), 800, TimeUnit.MILLISECONDS); // optional data

        return CompletableFuture.allOf(balance, plan, sims)
            .thenApply(v -> new Summary(balance.join(), plan.join(), sims.join()))
            .orTimeout(2, TimeUnit.SECONDS)
            .exceptionally(ex -> {
                Throwable cause = ex instanceof CompletionException ? ex.getCause() : ex;
                log.warn("summary degraded for {}: {}", msisdn, cause.toString());
                return Summary.fallback(msisdn);
            })
            .join();
    }
}
```
Three remote calls run in parallel; total latency is the slowest call, not the sum. `sims` degrades to an empty list on its own, while `balance`/`plan` failure degrades the whole response.

## Pitfalls
- Exceptions are wrapped: unwrap `CompletionException.getCause()` before matching types; `exceptionally` on a stage after `thenApply` sees the wrapped form.
- A timeout does not cancel the underlying work. Combine with a cancellable client or the thread pool fills with zombie calls.
- `allOf(...).join()` after a failure throws the **first** failure; the other futures may still be running.
- Calling `join()` inside a callback that runs on the common pool can deadlock the pool (all threads waiting on each other).
- `thenApply` after `supplyAsync` on a long task: if the task finished before you chained, the callback runs synchronously on your thread. Do not assume a thread.
- Related: [[spring-reactive-webclient-sse]] for the reactive alternative, [[circuit-breaker]] for fallbacks that remember failures, [[synchronizers]] for `CountDownLatch`-style fan-in without futures.
