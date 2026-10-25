---
title: Synchronization, monitors and wait/notify
tags: [synchronized, monitor, mutex, wait-notify, thread-safety, cas]
learned: 2026-10-22
---
## TL;DR
**Synchronization** is organizing sequential access to a shared resource (limiting how many threads may be inside a piece of code at once) so the program does not misbehave through a race condition. In Java the built-in tool is the monitor behind `synchronized`; the alternatives are the locks and atomics of `java.util.concurrent`.

## Key points
### Vocabulary
- **Critical section**: code that touches a shared resource and must not run in more than one thread at a time. Two threads inside it → race condition.
- **Mutex** (mutual exclusion): a lock that lets exactly one thread own a resource at a time.
- **Monitor**: a higher-level construct, a mutex plus a wait set. Every Java object has one; `synchronized` acquires it.
- **Thread safety**: a property of code or an object that behaves correctly no matter how many threads use it at once.
- **Immutable objects** are thread-safe for free: nothing can change after construction, so there is nothing to synchronize. Prefer them (records, `final` fields, defensive copies).

### Tools, from heaviest to lightest
- `synchronized` method or block: pessimistic locking, turns concurrent execution into sequential execution on that monitor.
- `java.util.concurrent.locks`: `ReentrantLock`, `ReadWriteLock`, `Semaphore` ([[locks-and-lock-free]]).
- Atomics (`AtomicInteger`, `AtomicReference`): optimistic, lock-free, built on **CAS** (compare-and-swap), which the CPU executes as one indivisible instruction. Before writing, CAS checks that the value is still what the thread last read; if not, the update fails and the thread retries. This is what made the Java 5 concurrency package possible; before it, "concurrent" work on one resource was only possible through a monitor.

### wait / notify / notifyAll
- `wait()` (on `Object`) releases the monitor and parks the current thread in the object's wait set until another thread calls `notify()` / `notifyAll()` on the same object. Optionally takes a timeout. Throws `InterruptedException`.
- `notify()` wakes one waiting thread, `notifyAll()` wakes all of them. The woken thread must re-acquire the monitor before continuing.
- They must be called inside `synchronized` on that object: the methods themselves are not synchronized, and checking the condition and waiting has to be atomic, otherwise a notify can be lost between the check and the wait.
- `sleep()` is different: it keeps any monitors it holds and does not depend on another thread.

## Example
```java
class BoundedBuffer<T> {
    private final Deque<T> items = new ArrayDeque<>();
    private final int capacity;

    synchronized void put(T item) throws InterruptedException {
        while (items.size() == capacity) wait();   // always 'while', never 'if'
        items.addLast(item);
        notifyAll();
    }
    synchronized T take() throws InterruptedException {
        while (items.isEmpty()) wait();
        T item = items.removeFirst();
        notifyAll();
        return item;
    }
}
```

## Pitfalls
- Waiting with `if` instead of `while`: spurious wake-ups and `notifyAll` mean the condition may still be false after `wait()` returns.
- Synchronizing on `this` or on a public object lets outside code lock you; prefer a private final lock object.
- `synchronized` on a *method* of a class locks the whole instance; two unrelated methods block each other.
- Blocking inside `synchronized` pins a [[virtual-threads|virtual thread]] to its carrier (before JDK 24); use `ReentrantLock` there.
- The memory-visibility side of `synchronized` is explained in [[java-memory-model]].
