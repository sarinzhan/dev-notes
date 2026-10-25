---
title: Race conditions, deadlock, livelock and starvation
tags: [race-condition, deadlock, livelock, starvation]
learned: 2026-10-22
---
## TL;DR
Four ways a multithreaded program goes wrong: a **race condition** makes the result depend on scheduling order; a **deadlock** freezes threads that wait for each other's locks; a **livelock** keeps them busy without progress; **starvation** keeps one thread from ever getting its turn.

## Key points
- **Race condition**: two or more threads access a shared resource (a variable, a file, a structure) and the outcome depends on the order in which the scheduler runs them. The stricter term is *non-determinism of parallelism*. The classic case is check-then-act (`if (!map.containsKey(k)) map.put(k, v)`) and read-modify-write (`count++`). Fix: make the compound action atomic (lock, `putIfAbsent`, `AtomicInteger`).
- **Deadlock**: thread A holds lock 1 and waits for lock 2, thread B holds lock 2 and waits for lock 1. Neither can continue. Needs four conditions: mutual exclusion, hold-and-wait, no preemption, circular wait. Break any one of them, usually circular wait, by always acquiring locks in the same global order, or by using `tryLock` with a timeout.
- **Livelock**: threads keep changing state in response to each other (both back off, both retry, both back off…) and never progress. Looks alive, does nothing. Fix: randomized backoff.
- **Starvation**: a thread never gets the resource because others always win (unfair locks, higher-priority threads, a writer that never gets in because readers keep arriving). Fix: fair locks (`new ReentrantLock(true)`), bounded waiting.

## Example
```java
// Deadlock: lock order differs between the two methods
void transfer(Account from, Account to, long amount) {
    synchronized (from) {
        synchronized (to) { from.debit(amount); to.credit(amount); }
    }
}
// transfer(a, b) and transfer(b, a) in parallel can freeze forever.

// Fix: impose a global order
void transferSafe(Account from, Account to, long amount) {
    Account first = from.id < to.id ? from : to;
    Account second = first == from ? to : from;
    synchronized (first) { synchronized (second) { from.debit(amount); to.credit(amount); } }
}
```

## Pitfalls
- A deadlock shows up in a thread dump as threads in `BLOCKED` with "waiting to lock <X> ... locked <Y>"; `jstack` reports it explicitly. Analyze dumps instead of guessing.
- Database deadlocks are the same pattern with rows instead of monitors; Postgres detects them and aborts one transaction. See [[postgres-isolation-levels]].
- Races are not fixed by `volatile` alone; it fixes visibility, not atomicity ([[java-memory-model]]).
- The tools for avoiding these problems are in [[synchronization-and-monitors]] and [[locks-and-lock-free]].
