---
title: Lock-free programming and compare-and-swap
tags: [concurrency, lock-free, cas, atomics, aba]
learned: 2026-10-27
---
## TL;DR
**Compare-and-swap** (CAS) is a single hardware instruction: "if the word at this address still equals `expected`, write `new` and return true, else return false". Every lock-free algorithm is a loop: read the current value, compute the new one, CAS; if another thread changed it in between, retry. **Lock-free** means at least one thread makes progress in a bounded number of steps system-wide (no thread can block the others by being descheduled while holding a lock); **wait-free** means every thread finishes in bounded steps. The classic trap is the **ABA problem**: the value is back to `A`, so CAS succeeds, but the world changed in between; fixed with a version/tag or hazard pointers. Java's `Atomic*`, `VarHandle`, `LongAdder` and the lock vs lock-free comparison are in [[locks-and-lock-free]]; this note is the general mechanism.

## Key points

### The primitive
- Hardware: `lock cmpxchg` on x86 (one instruction, the cache line is locked for its duration), `ldxr`/`stxr` load-linked/store-conditional pairs on ARM (store fails if anything touched the line in between). Both are atomic read-modify-write with a full or acquire/release fence ([[memory-models-and-visibility]]).
- Cost: ~5–20 ns uncontended, same ballpark as an uncontended mutex acquire (which is itself a CAS). Under contention a CAS loop spins on cache-line transfers between cores: each failed attempt is a round trip through the cache coherence protocol (~50–100 ns), and with many cores on one line throughput collapses ([[cpu-caches-and-cache-lines]]).
- Relatives: fetch-and-add (`lock xadd`, used by `getAndIncrement`; never fails, so it is wait-free), test-and-set (spinlocks), double-width CAS (`cmpxchg16b`, CAS pointer + counter together).
- Consensus number: CAS solves wait-free consensus for any number of threads (Herlihy), so any lock-free structure can in principle be built on `AtomicReference`.

### Progress guarantees
| Guarantee | Meaning | Example |
|---|---|---|
| Blocking | a descheduled or crashed lock holder stops everyone | mutex, `synchronized` |
| Obstruction-free | a thread finishes if it runs alone long enough | some STM designs |
| Lock-free | some thread always completes in bounded steps; individual threads may starve | CAS retry loops, Treiber stack, Michael-Scott queue, `ConcurrentLinkedQueue` |
| Wait-free | every thread completes in bounded steps | fetch-and-add counters, `LongAdder` increments, single-producer/single-consumer ring buffers |
- Lock-free is about **progress under preemption and failure**, not raw speed. A lock-free structure can be slower than a locked one under low contention; its value is that a thread paused by the OS ([[process-management]]), a GC safepoint or a page fault cannot stall the rest, and there is no priority inversion or deadlock ([[deadlocks-and-synchronization-primitives]]).

### The CAS loop
```c
// C-like pseudocode: lock-free push onto a Treiber stack
void push(Stack* s, Node* n) {
    Node* head;
    do {
        head = atomic_load(&s->head);     // 1. read
        n->next = head;                   // 2. compute based on it
    } while (!CAS(&s->head, head, n));    // 3. commit only if nobody changed head meanwhile
}
```
- Step 2 must be side-effect free or idempotent because it may run many times. Allocating a new immutable object per iteration (Java `AtomicReference.updateAndGet` with a persistent structure) is the common functional pattern; it also fits [[immutability-and-side-effects]].
- Spin discipline: on failure retry immediately a few times, then back off (`Thread.onSpinWait()` emits `pause`), because hammering a contended line slows the thread that is about to succeed.

### The ABA problem
- Thread 1 reads `head = A` (with `A.next = B`), gets preempted. Thread 2 pops `A`, pops `B`, pushes `A` back. Thread 1 resumes: `head` is still `A`, CAS succeeds, and it sets `head = B`, which is no longer in the stack. Memory corruption in C; in Java rarer (a referenced object's identity is never reused) but still possible when nodes are recycled or values are primitives.
- Fixes:
  - **Tagged pointers / version stamps**: CAS `(pointer, counter)` together so a reused `A` has a different counter. Java: `AtomicStampedReference`, `AtomicMarkableReference`, or pack a version into a `long`.
  - **Hazard pointers / epoch reclamation**: do not free or reuse a node while any thread may still be reading it. Java's GC gives this for free as long as you never reuse node objects.
  - **Fresh objects per update**: allocate a new node/snapshot every time; the old `A` can never come back.
- The same bug appears in databases and distributed systems: optimistic locking on `WHERE version = ?` is CAS with a stamp ([[jpa-locking]]); a fencing token on a distributed lock is the version that prevents a stale holder's write ([[distributed-locks-and-fencing]]); an `etag`/`If-Match` header is CAS over HTTP ([[http-caching]], [[idempotency]]).

### When to use
- Single-word state many threads update: counters (`LongAdder` when write-heavy), sequence numbers, status enums, cached references; hand-off queues between producer and consumer threads.
- Not for: multi-field invariants unless packed into one word or one immutable snapshot, long critical sections, or IO inside the "critical section". A mutex is simpler and often faster there ([[mutex-semaphore-monitor]]).

## Example
```java
// Lock-free token bucket for per-subscriber API rate limiting (one CAS per request, no lock).
final class TokenBucket {
    private record State(long tokens, long lastRefillNanos) {}
    private final AtomicReference<State> state;
    private final long capacity, refillPerSecond;

    TokenBucket(long capacity, long refillPerSecond) {
        this.capacity = capacity; this.refillPerSecond = refillPerSecond;
        this.state = new AtomicReference<>(new State(capacity, System.nanoTime()));
    }

    boolean tryAcquire() {
        while (true) {
            State cur = state.get();                                            // read
            long now = System.nanoTime();
            long refilled = Math.min(capacity,
                cur.tokens() + (now - cur.lastRefillNanos()) * refillPerSecond / 1_000_000_000L);
            if (refilled == 0) return false;
            State next = new State(refilled - 1, now);                          // compute, no side effects
            if (state.compareAndSet(cur, next)) return true;                    // commit or retry
            Thread.onSpinWait();
        }
    }
}
```
A fresh immutable `State` per attempt sidesteps ABA (an old `State` object never becomes current again) and keeps the two fields consistent because the reference is the single word being CASed ([[rate-limiting]]).

## Pitfalls
- A CAS loop on a hot shared counter from 32 threads: cache-line ping-pong makes it slower than a striped `LongAdder` or per-thread counters merged later.
- Doing non-idempotent work inside the retry loop (logging, incrementing another counter, sending a message): it runs once per failed attempt.
- Assuming "lock-free" means "no synchronization needed elsewhere": the CAS publishes only what happens-before it; mutable objects reachable from the swapped reference still need their own safety.
- ABA via object recycling or primitive values: a pool of reused node objects, or a `long` slot that returns to the same value, breaks a naive `compareAndSet`. Add a stamp.
- Writing your own lock-free queue or map for production: subtle, hardware-dependent, hard to test; use `java.util.concurrent` and reach for custom code only with `jcstress` tests.
- Related: [[locks-and-lock-free]] for the Java API surface and lock vs CAS trade-offs, [[race-conditions-and-critical-sections]] for the check-then-act bug CAS is designed to close.
