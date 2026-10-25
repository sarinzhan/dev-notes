---
title: ReentrantLock, ReadWriteLock, spin locks and lock-free structures
tags: [locks, reentrantlock, readwritelock, cas, lock-free]
learned: 2026-10-22
---
## TL;DR
`synchronized` is a language construct with one monitor and no options. `java.util.concurrent.locks` (Java 5) gives the same mutual exclusion as a class with options: try with timeout, interruptible waiting, fairness, several wait conditions, and separate read/write locks. Lock-free structures skip locks entirely and rely on CAS.

## Key points
### Lock vs synchronized
- `tryLock()` checks availability without waiting; `tryLock(timeout, unit)` waits at most that long. Avoids deadlock by giving up.
- `lockInterruptibly()`: a thread waiting for the lock can be interrupted. A thread blocked on `synchronized` cannot.
- Fair mode (`new ReentrantLock(true)`): threads get the lock in FIFO order; slower but no starvation.
- `Condition` objects (`lock.newCondition()`): several wait sets per lock, each with `await()` / `signal()` / `signalAll()`. With a monitor you get exactly one wait set.
- Reentrant means the owning thread can lock again without blocking itself; both `synchronized` and `ReentrantLock` are reentrant.

### ReadWriteLock
- `readLock()`: many readers at once. `writeLock()`: one writer, and readers are blocked while it is held.
- Implementation `ReentrantReadWriteLock`, with optional fairness for writers so they are not starved by a stream of readers. For read-mostly data with tiny critical sections `StampedLock` (optimistic reads) is faster.

### Spin lock
A thread that wants the lock keeps checking it in a loop instead of parking. Avoids the cost of a context switch when the wait is microseconds long; burns a core when it is not. The JVM already spins briefly before parking on a contended monitor.

### Lock-free (and wait-free) structures
- Built on atomic operations such as CAS instead of locks: read the current value, compute the new one, CAS it in, retry if someone else changed it first.
- Lock-free: at least one thread always makes progress. Wait-free: every thread finishes in a bounded number of steps (rarer, harder).
- In the JDK: `AtomicInteger`/`AtomicLong`/`AtomicReference`, `LongAdder`, `ConcurrentLinkedQueue`, parts of `ConcurrentHashMap` ([[concurrent-collections]]).

## Example
```java
private final ReentrantLock lock = new ReentrantLock();
private final Condition notEmpty = lock.newCondition();

void put(T item) {
    lock.lock();
    try { queue.add(item); notEmpty.signal(); }
    finally { lock.unlock(); }          // always in finally
}

T take() throws InterruptedException {
    lock.lock();
    try {
        while (queue.isEmpty()) notEmpty.await();
        return queue.poll();
    } finally { lock.unlock(); }
}
```

## Pitfalls
- Forgetting `unlock()` in a `finally` leaks the lock forever; `synchronized` cannot have this bug.
- `ReadWriteLock` is slower than a plain lock when critical sections are short or writes are frequent; measure.
- CAS loops under heavy contention waste CPU on retries; `LongAdder` beats `AtomicLong` for counters.
- Background on why locks are needed at all: [[synchronization-and-monitors]], [[concurrency-hazards]].
