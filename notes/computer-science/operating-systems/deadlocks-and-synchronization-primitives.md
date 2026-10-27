---
title: Deadlocks and OS synchronization primitives
tags: [operating-systems, deadlock, mutex, semaphore, linux]
learned: 2026-10-27
---
## TL;DR
A **deadlock** is a set of threads each waiting for a resource held by another member of the set, forever. It requires four conditions at once: **mutual exclusion**, **hold-and-wait**, **no preemption**, **circular wait**; break any one and deadlock is impossible. In practice you prevent it with a global lock ordering, bounded waits (`tryLock` with timeout) and never holding a lock across a call you do not control. At the OS level the primitives are the **mutex** (ownership, one holder), the **semaphore** (a counter, no owner) and the **condition variable** (wait for a predicate while releasing the mutex); Linux builds all of them on **futex**. Java's monitors, `ReentrantLock` and the JVM-level deadlock story live in [[synchronization-and-monitors]] and [[concurrency-hazards]]; the general theory of the primitives is in [[mutex-semaphore-monitor]].

## Key points

### Coffman's four conditions
| Condition | Meaning | How to break it |
|---|---|---|
| Mutual exclusion | the resource cannot be shared | use lock-free or immutable data ([[lock-free-and-cas]], [[immutability-and-side-effects]]) |
| Hold and wait | a thread keeps resources while requesting more | acquire everything up front, or release before requesting (two-phase, `tryLock` all-or-nothing) |
| No preemption | a resource cannot be taken away | timeouts: `tryLock(1, SECONDS)`, `lock_timeout` in Postgres; transactions can be rolled back, which is why databases *can* preempt |
| Circular wait | a cycle in the wait-for graph | global lock ordering (always lock accounts by ascending id) |
- Databases detect deadlocks by searching the wait-for graph for cycles and killing one victim with an error (`40P01` in Postgres); the application retries. Application locks have no such detector, so a JVM deadlock is forever: `jstack` prints "Found one Java-level deadlock" and only a restart helps ([[postgres-locks]]).

### Strategies
- **Prevention**: design so a condition cannot hold. Lock ordering is the workhorse; `synchronized` nesting across beans is the usual way it breaks.
- **Avoidance**: the Banker's algorithm grants a request only if the system stays in a safe state. Needs maximum demands in advance; textbook material, not used in general-purpose systems.
- **Detection and recovery**: let it happen, find cycles, abort a victim. Databases, Temporal/workflow engines (timeouts), Kubernetes liveness probes that restart a hung pod.
- **Ignore** ("ostrich"): acceptable when deadlocks are rare and restarts cheap; OS kernels do this for user-space locks. Catch ordering bugs early with `ThreadSanitizer`, `jcstress`, or `jstack` under load.

### OS-level primitives
| Primitive | State | Owner | Typical use | Linux implementation |
|---|---|---|---|---|
| Spinlock | 1 bit | the holder | kernel, very short critical sections, no sleeping allowed | atomic test-and-set, busy loop |
| Mutex | locked/unlocked | yes; only the owner unlocks | protect a data structure | `pthread_mutex_t` over futex: CAS in user space, `futex(FUTEX_WAIT)` only on contention |
| Semaphore | counter ≥ 0 | no owner; anyone may `post` | limit N concurrent users; signal between threads | `sem_t` over futex |
| Condition variable | queue of waiters | paired with a mutex | wait until a predicate becomes true | `pthread_cond_t`; `wait` atomically releases the mutex and sleeps |
| Read-write lock | readers count + writer flag | writer or readers | read-mostly data | `pthread_rwlock_t` |
| Barrier | counter | none | phase synchronization | `pthread_barrier_t` |
- **futex** (fast user-space mutex): the lock word lives in user memory; uncontended lock and unlock are one CAS with no syscall. Only when a thread must sleep or wake another does it call `futex()`. That is why `strace -c` on a contended JVM shows thousands of `futex` calls, and why an uncontended `synchronized` costs about 20 ns.
- **Mutex vs binary semaphore**: both have two states, but a mutex has ownership (unlocking from another thread is an error; priority inheritance and recursive locking are possible), a semaphore does not (thread A can `post` what thread B `wait`ed on, which makes it a signalling tool). Interviewers like this distinction.
- **Priority inversion**: a low-priority thread holds a mutex a high-priority thread needs, and a medium-priority thread starves the low one, so the high one waits indefinitely (Mars Pathfinder, 1997). Fix: priority inheritance (`PTHREAD_PRIO_INHERIT`), which a plain semaphore cannot provide because it has no owner.

- Read-write locks trade one starvation for another: writer-preferring starves readers, reader-preferring starves writers ([[locks-and-lock-free]]).

## Example
```java
// Transfer between two SIM balance accounts: classic lock-ordering deadlock and the fix.
// Each Account has its own ReentrantLock (or synchronized); locking a then b in one thread
// and b then a in another is a circular wait.

void transfer(Account from, Account to, long amount) {
    Account first  = from.id().compareTo(to.id()) < 0 ? from : to;   // global order by id
    Account second = first == from ? to : from;
    first.lock().lock();
    try {
        second.lock().lock();
        try {
            from.withdraw(amount);
            to.deposit(amount);
        } finally { second.lock().unlock(); }
    } finally { first.lock().unlock(); }
}

// Alternative: no ordering, but no indefinite hold-and-wait either
boolean tryTransfer(Account from, Account to, long amount) throws InterruptedException {
    if (!from.lock().tryLock(200, TimeUnit.MILLISECONDS)) return false;
    try {
        if (!to.lock().tryLock(200, TimeUnit.MILLISECONDS)) return false;  // back off, caller retries with jitter
        try { from.withdraw(amount); to.deposit(amount); return true; }
        finally { to.lock().unlock(); }
    } finally { from.lock().unlock(); }
}
```
In a database the same transfer deadlocks if two transactions `UPDATE` the rows in opposite order; order the `SELECT ... FOR UPDATE` by primary key, or let Postgres detect it and retry on `40P01`.

## Pitfalls
- Calling out to unknown code (listeners, callbacks, another service's bean, a logger with its own lock) while holding a lock: you do not know what it locks, so you cannot keep an ordering.
- Nested `synchronized` on `this` plus a `ConcurrentHashMap.compute` that calls back into the same object: `compute` holds a bin lock, re-entry deadlocks or throws `IllegalStateException: Recursive update`.
- Thread pool deadlock without any lock: tasks in a bounded pool submit subtasks to the same pool and block on their `Future`; all workers wait for queued work that can never run ([[executors-and-thread-pools]]).
- Timeouts hide, not fix, ordering bugs: with `tryLock` the system "works" at 1% failure rate; log and alert on timeouts so the root cause is found.
- Semaphore used as a mutex: a bug that calls `release()` twice lets two threads into the critical section, and the semaphore cannot detect it.
- Related: [[mutex-semaphore-monitor]] for the primitives as abstract concepts, [[distributed-locks-and-fencing]] for the same problem across processes where timeouts and fencing tokens replace the wait-for graph.
