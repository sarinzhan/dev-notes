---
title: CompletableFuture essentials
tags: [concurrency, async]
learned: 2026-09-30
---
## TL;DR
`CompletableFuture` is Java's promise: compose async steps with `thenApply`, `thenCompose`, `allOf`, and handle failures with `exceptionally` / `handle`. With [[virtual-threads]] it is needed less often, but it is still the tool for fan-out/fan-in.

## Key points
- `thenApply` = map, `thenCompose` = flatMap, `thenCombine` = zip two futures.
- `supplyAsync(r)` without an executor uses the **common ForkJoinPool**. Always pass your own executor in services.
- `allOf(...)` returns `CompletableFuture<Void>`; collect results with `join()` after it completes.
- `join()` throws unchecked `CompletionException`, `get()` throws checked `ExecutionException`.

## Example
```java
var balance = CompletableFuture.supplyAsync(() -> billing.balance(id), pool);
var plan    = CompletableFuture.supplyAsync(() -> crm.plan(id), pool);
return balance.thenCombine(plan, Summary::new)
              .orTimeout(2, TimeUnit.SECONDS)
              .exceptionally(ex -> Summary.fallback(id));
```

## Pitfalls
- Exceptions are wrapped: unwrap `CompletionException.getCause()` before matching types.
- A timeout does not cancel the underlying work. Combine with a cancellable client.
