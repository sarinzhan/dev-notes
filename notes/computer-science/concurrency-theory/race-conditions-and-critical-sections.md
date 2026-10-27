---
title: Race conditions, data races and critical sections
tags: [concurrency, race-condition, atomicity, critical-section]
learned: 2026-10-27
---
## TL;DR
A **race condition** is a correctness bug where the result depends on the relative timing of concurrent operations. A **data race** is narrower and lower-level: two threads access the same memory location without synchronization and at least one writes; in Java and C++ it is a defined term that makes the behaviour (or the visibility) undefined. You can have a race condition with no data race (two atomic steps that should have been one) and a data race that is benign in practice. The fix for both is the same idea: identify the **critical section** (the compound action that must appear atomic to others) and enforce **mutual exclusion** or make the compound action a single atomic operation. Java tooling for this (`synchronized`, `Atomic*`, `ConcurrentHashMap`) is in [[synchronization-and-monitors]], [[locks-and-lock-free]] and [[concurrent-collections]]; the hazard zoo (deadlock, livelock, starvation) is in [[concurrency-hazards]].

## Key points

### Race condition vs data race
| | Race condition | Data race |
|---|---|---|
| Level | logic / invariants | memory access |
| Definition | outcome depends on interleaving in a way that violates a spec | unsynchronized concurrent access, at least one write |
| Detected by | reasoning, tests under load, model checking | tools: ThreadSanitizer, Java `jcstress`, the memory model |
| Example without the other | `if (!exists(k)) insert(k)` with each call internally synchronized | two threads writing a `boolean done` flag with the same value |
| Fix | make the whole compound action atomic | add synchronization or use atomics so the memory model gives a happens-before edge ([[memory-models-and-visibility]]) |
- The two classic shapes of race condition: **check-then-act** (`if (balance >= amount) balance -= amount`; TOCTOU in filesystems: `if exists(file) then open(file)`) and **read-modify-write** (`count++`, `map.put(k, map.get(k) + 1)`).
- Interleaving math: with two threads each doing `count++` (load, add, store) there are 20 possible interleavings; two of the outcomes lose an increment. A bug that reproduces 1% of the time under load is still a bug.

### Critical section
- A region of code that touches shared state and must not be interleaved with other such regions on the same state. Requirements for a correct solution (Dijkstra/Peterson era): **mutual exclusion**, **progress** (if nobody is inside, someone who wants in gets in), **bounded waiting** (no starvation).
- The section is defined by the **invariant**, not by the syntax: "the sum of both accounts is constant" makes both updates one critical section even if they are separate methods. Choosing the section too small gives races; too large gives contention and deadlock risk ([[deadlocks-and-synchronization-primitives]]).
- Guard every access, not just writes: a reader that sees a half-updated structure (a resized `HashMap` mid-rehash, a 64-bit `long` written in two halves on 32-bit JVMs) is also a race.

### Atomicity at different levels
| Level | What is atomic | Tool |
|---|---|---|
| Hardware | aligned word load/store, CAS, fetch-and-add | `lock cmpxchg`, `ldxr/stxr` |
| Language | `volatile` read/write, `AtomicLong.incrementAndGet`, `ConcurrentHashMap.compute` | [[lock-free-and-cas]] |
| Code block | anything inside a mutex/monitor | [[mutex-semaphore-monitor]] |
| Database | a transaction, or a single `UPDATE ... SET n = n + 1 WHERE ...` statement | [[transactions-acid-and-isolation]], [[postgres-isolation-levels]] |
| Distributed | compare-and-set on a version, idempotency key, lease | [[idempotency]], [[distributed-locks-and-fencing]] |
- The same race migrates across levels. `SELECT balance` then `UPDATE balance = ?` in Java is check-then-act against the database; two instances of the service race just like two threads. The fix is also the same shape: one atomic statement (`UPDATE ... WHERE balance >= ?` and check the row count), a row lock (`FOR UPDATE`), or optimistic versioning (`WHERE version = ?`, [[jpa-locking]]).

### Finding races
- Code review heuristics: any shared mutable field read in one statement and written in another; any `get`-then-`put`; lazy initialisation; counters; caches; "singleton" with a null check.
- Tests: run the operation from N threads with a `CyclicBarrier` start ([[synchronizers]]) and assert the invariant; `jcstress` for memory-level races; a load test against a real Postgres for the SQL version (`pgbench` custom script).
- Production: duplicated records with the same business key, negative balances, counters that drift from the source of truth, "impossible" states.

## Example
```java
// eSIM activation quota per subscriber: at most N active profiles.
// Race: check-then-act across two app instances. Thread/instance A and B both see 2 < 3 and both insert.
@Transactional
public void activate(UUID subscriberId, String iccid) {
    int active = repo.countActive(subscriberId);          // read
    if (active >= 3) throw new QuotaExceeded();           // check
    repo.insertActive(subscriberId, iccid);               // act  <- two inserts slip through
}

// Fix 1: make the compound action one atomic statement at the database level
@Transactional
public void activateAtomic(UUID subscriberId, String iccid) {
    int inserted = jdbc.update("""
        INSERT INTO active_profile (subscriber_id, iccid)
        SELECT ?, ?
        WHERE (SELECT count(*) FROM active_profile WHERE subscriber_id = ?) < 3
        """, subscriberId, iccid, subscriberId);
    // still racy under READ COMMITTED: both SELECTs can run before either INSERT is visible.
    // So also serialise per subscriber:
    if (inserted == 0) throw new QuotaExceeded();
}

// Fix 2: serialise the critical section per subscriber with a row lock, then check-then-act is safe
@Transactional
public void activateLocked(UUID subscriberId, String iccid) {
    jdbc.queryForObject("SELECT id FROM subscriber WHERE id = ? FOR UPDATE", UUID.class, subscriberId);
    if (repo.countActive(subscriberId) >= 3) throw new QuotaExceeded();
    repo.insertActive(subscriberId, iccid);
}
```
The row lock is the mutex; the transaction boundary is the critical section; the invariant "≤ 3 active per subscriber" is what defines it. A `UNIQUE` constraint on `(subscriber_id, slot)` would be the lock-free alternative: let the database reject the loser ([[constraints-and-relationships]]).

## Pitfalls
- Making each method `synchronized` and believing the class is thread-safe: `if (!list.contains(x)) list.add(x)` on a `synchronizedList` is still check-then-act.
- "It is just a counter, who cares about an occasional lost update": metrics drift, rate limiters let extra requests through, quotas leak money.
- `volatile` to fix `count++`: it fixes visibility, not atomicity of read-modify-write. Use `AtomicLong`/`LongAdder`.
- Relying on the database without choosing the isolation level consciously: `READ COMMITTED` lets two transactions both pass a count check; `SERIALIZABLE` or explicit locking is needed for check-then-act on aggregates ([[postgres-isolation-levels]]).
- Double-checked locking without `volatile` on the field: a classic published-before-constructed race in Java before and after JDK 5 rules.
- Related: [[memory-models-and-visibility]] for why a data race is more than a timing bug, [[immutability-and-side-effects]] for the design that removes shared mutable state altogether.
