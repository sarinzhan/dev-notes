---
title: CountDownLatch, CyclicBarrier and Semaphore
tags: [countdownlatch, cyclicbarrier, semaphore, coordination]
learned: 2026-10-22
---
## TL;DR
Synchronizers coordinate *when* threads proceed rather than protecting data. `CountDownLatch` lets threads wait for a counter to reach zero (one-shot). `CyclicBarrier` lets a group of threads wait for each other at a point and then continue together (reusable). `Semaphore` limits how many threads may be inside a section at once.

## Key points
### CountDownLatch
- Created with a count. Waiting threads call `await()`; worker threads call `countDown()`. When the count hits zero, every waiter continues.
- One-shot: the count cannot be reset. Typical use: "wait until N services are initialized", or in tests: "start all workers at the same time".

### CyclicBarrier
- Created with the number of parties and an optional barrier action. Each thread calls `await()` when it reaches the barrier; when all parties are there, the action runs once and all threads continue.
- Reusable: resets automatically after each trip. Typical use: phased parallel algorithms (compute phase, then merge, then next iteration). `Phaser` is the more flexible version with dynamic parties.

### Semaphore
- A set of permits. `acquire()` blocks until a permit is free, `release()` returns it. With one permit it is a mutex that any thread may release (unlike a lock).
- Typical use: throttling access to a pool of N resources, limiting concurrent calls to an external API.

## Example
```java
// Start 10 workers at exactly the same moment and wait for all to finish
CountDownLatch start = new CountDownLatch(1);
CountDownLatch done  = new CountDownLatch(10);
for (int i = 0; i < 10; i++) {
    pool.submit(() -> {
        start.await();      // everyone waits here
        doWork();
        done.countDown();
        return null;
    });
}
start.countDown();          // go
done.await(30, TimeUnit.SECONDS);
```

## Pitfalls
- A `countDown()` that is skipped because of an exception leaves waiters hanging; put it in `finally`.
- `await()` without a timeout in production code is a latent hang; always bound it.
- A `CyclicBarrier` is broken if any party is interrupted or times out; the others get `BrokenBarrierException`.
- Context on the thread pools these usually run in: [[executors-and-thread-pools]].
