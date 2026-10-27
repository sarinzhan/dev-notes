---
title: Mutex, semaphore and monitor
tags: [concurrency, mutex, semaphore, monitor, synchronization]
learned: 2026-10-27
---
## TL;DR
Three classic primitives, three different guarantees. A **mutex** gives mutual exclusion with ownership: one holder at a time and only the holder releases. A **semaphore** is a counter with atomic `acquire` (wait until > 0, decrement) and `release` (increment): with N it limits concurrency, with 1 it signals between threads, and nobody owns it. A **monitor** bundles a mutex with the data it protects and **condition variables** so threads can wait inside the critical section for a predicate ("queue not empty") and be woken when it changes; Java's `synchronized` + `wait/notify` and `ReentrantLock` + `Condition` are monitors. The Java APIs themselves are in [[synchronization-and-monitors]], [[locks-and-lock-free]] and [[synchronizers]]; OS implementations (futex, pthread) are in [[deadlocks-and-synchronization-primitives]].

## Key points

### Side by side
| | Mutex | Semaphore | Monitor |
|---|---|---|---|
| State | locked / unlocked + owner | integer count ≥ 0 | mutex + protected data + condition queues |
| Who releases | the owner only | anyone | the owner (on exit or on `wait`) |
| Count | 1 | N | 1 (the mutex) |
| Purpose | protect shared data | limit concurrency, signal events | protect data and coordinate on its state |
| Blocking wait on a condition | no (spin or sleep outside) | partially (wait for count > 0) | yes: `wait(cond)` releases the mutex atomically and sleeps |
| Reentrant | optional (recursive mutex) | not meaningful | usually yes (Java) |
| Java | `ReentrantLock`, `synchronized` | `java.util.concurrent.Semaphore` | `synchronized` + `wait/notify`, `Lock` + `Condition` |
| Postgres analogue | row lock `FOR UPDATE` | connection limit, `max_connections` | advisory lock + `LISTEN/NOTIFY` |

### Mutex
- Guarantees: at most one thread inside; progress; with a fair implementation, bounded waiting. Ownership enables error detection (unlock by a non-owner), recursion, and **priority inheritance** (a high-priority waiter lends its priority to the holder).
- Costs: an uncontended acquire is one CAS (~20 ns). Under contention the loser must either spin (cheap if the hold time is nanoseconds and there are free cores) or park in the kernel (microseconds, futex). Adaptive mutexes spin briefly, then park.
- Read-write variant: many readers or one writer. Helps only when reads are long and far outnumber writes; otherwise the extra bookkeeping costs more than a plain mutex.

### Semaphore
- Invented by Dijkstra (`P`/`V`). A **counting semaphore** initialised to N admits N threads: connection pools, bulkheads limiting concurrent calls to a slow downstream ([[circuit-breaker]] often pairs with it), rate limiting of in-flight requests, bounding a queue's capacity.
- A **binary semaphore** (N = 1) looks like a mutex but is not: thread A can `release()` what thread B acquired, which is exactly what you want for **signalling** ("the data is ready, wake the consumer") and exactly what you do not want for a critical section (no ownership, no priority inheritance, no detection of double release).
- Producer/consumer with two semaphores (`empty = N`, `full = 0`) plus a mutex for the buffer index is the textbook solution to bounded buffers; Java's `ArrayBlockingQueue` implements the same with one lock and two conditions.

### Monitor
- Proposed by Hoare and Brinch Hansen: a class whose methods run under one implicit mutex, with condition variables for waiting. The key operation is `wait(cond)`: **atomically** release the mutex and block; on wake, reacquire the mutex before returning. Without atomicity there is a lost-wakeup window between "check predicate" and "sleep".
- **Hoare vs Mesa semantics**: Hoare monitors hand the lock directly to the signalled thread, so the predicate is guaranteed true when it wakes. Mesa monitors (Java, pthreads, every real system) just move the waiter to the ready queue; by the time it reacquires the lock another thread may have changed the state. Hence the rule: **always wait in a `while` loop**, never an `if`. Spurious wakeups are allowed for the same reason.
- `signal`/`notify` wakes one waiter of a condition; `broadcast`/`notifyAll` wakes all. Use one condition per predicate (`notFull`, `notEmpty`) with `signal`, or a single condition with `broadcast`. Signalling the wrong single waiter with one shared condition is a classic deadlock.
- Java specifics: every object is a monitor (`synchronized`, `wait`, `notify`); `ReentrantLock.newCondition()` gives multiple conditions per lock ([[synchronization-and-monitors]]).

### Choosing
- Protect a data structure: mutex (or CAS for single-word state, [[lock-free-and-cas]]). Limit how many may do X at once: counting semaphore. One-shot "it happened": a latch or future ([[synchronizers]], [[completable-future]]). Wait for a state change on protected data: monitor with conditions, or a blocking queue that already implements it.

## Example
```java
// Bounded buffer as a monitor with two conditions (what ArrayBlockingQueue does internally),
// used to hand eSIM download orders from HTTP threads to a small pool of SM-DP+ workers.
final class OrderBuffer {
    private final ReentrantLock lock = new ReentrantLock();
    private final Condition notFull  = lock.newCondition();
    private final Condition notEmpty = lock.newCondition();
    private final Deque<Order> items = new ArrayDeque<>();
    private final int capacity;

    OrderBuffer(int capacity) { this.capacity = capacity; }

    void put(Order o) throws InterruptedException {
        lock.lock();
        try {
            while (items.size() == capacity) notFull.await();   // while, not if: Mesa semantics
            items.addLast(o);
            notEmpty.signal();                                   // wake exactly one consumer
        } finally { lock.unlock(); }
    }

    Order take() throws InterruptedException {
        lock.lock();
        try {
            while (items.isEmpty()) notEmpty.await();
            Order o = items.removeFirst();
            notFull.signal();
            return o;
        } finally { lock.unlock(); }
    }
}

// The semaphore version of "at most 8 concurrent calls to the SM-DP+ server" (a bulkhead):
private final Semaphore smdpSlots = new Semaphore(8, true);
Response callSmdp(Request r) throws InterruptedException {
    if (!smdpSlots.tryAcquire(2, TimeUnit.SECONDS)) throw new BulkheadFull();
    try { return smdpClient.send(r); } finally { smdpSlots.release(); }
}
```
In production use `ArrayBlockingQueue` and Resilience4j's `Bulkhead`; the point is to recognise which primitive each one is.

## Pitfalls
- Using a semaphore as a mutex: a double `release()` silently admits two threads; no priority inheritance; no reentrancy. Reach for a mutex when there is an owner.
- `if (!condition) wait()` instead of `while`: wrong under Mesa semantics and spurious wakeups; manifests as a rare `NoSuchElementException` or a negative count.
- `notify()` with one shared condition variable for two different predicates: wakes a producer when a consumer was needed; everything stalls. Use two conditions or `notifyAll()`.
- Holding the monitor while doing IO (`synchronized` around an HTTP call): throughput collapses to one request at a time, and timeouts turn into deadlock-like hangs.
- Forgetting that `Semaphore` permits are not tied to threads: a `release()` in a `finally` that runs after a failed `tryAcquire` adds a permit that never existed.
- Related: [[deadlocks-and-synchronization-primitives]] for how these are built on futex and where deadlocks come from, [[race-conditions-and-critical-sections]] for what the critical section they protect actually is.
