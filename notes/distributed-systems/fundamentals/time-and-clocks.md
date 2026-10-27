---
title: Time and clocks in distributed systems
tags: [distributed-systems, clocks, ordering, theory]
learned: 2026-10-27
---
## TL;DR
Wall clocks on different machines disagree (skew), run at different speeds (drift), and can jump backwards after an NTP correction. So "which event happened first" cannot be decided by comparing timestamps from two nodes. Use **logical clocks** for ordering: a **Lamport clock** gives a total order consistent with causality, a **vector clock** also detects concurrency, and a **hybrid logical clock** keeps timestamps readable while staying causal. Keep wall-clock time for humans and TTLs, and measure durations with a **monotonic** clock.

## Key points

### Why physical time is unreliable
- **Skew**: two NTP-synced servers in one datacenter typically differ by a few milliseconds; across clouds or with a misconfigured host, by seconds. Google's Spanner needs atomic clocks and GPS to bound the uncertainty to about 7 ms and still waits out that interval on commit.
- **Drift**: quartz oscillators drift by tens of ppm, that is seconds per day without sync.
- **Jumps**: NTP slews small errors but *steps* large ones; `System.currentTimeMillis()` can go backwards. Leap seconds have broken systems that assumed monotonic wall time.
- **Pauses**: a stop-the-world GC, a VM live migration or a laptop lid closing stops the process, not the world. A lease that "has 2 s left" can already be expired when the thread wakes up ([[distributed-locks-and-fencing]], [[garbage-collection]]).

### Two kinds of clock in Java
| Need | Use | Not |
|---|---|---|
| Timestamp for a record, log line, TTL | `Instant.now()` / `Clock` (time-of-day, can jump) | `System.nanoTime()` (meaningless across processes) |
| Measuring elapsed time, timeouts, rate limits | `System.nanoTime()` (monotonic, per JVM) | `currentTimeMillis()` deltas (break on NTP step) |

- Inject `java.time.Clock` into services so tests can control time ([[testing-async-and-kafka-flows]]).

### Lamport clock (logical, total order)
- Each node keeps a counter. On a local event: `c = c + 1`. On send: attach `c`. On receive: `c = max(c, received) + 1`.
- Guarantee: if A happened-before B, then `L(A) < L(B)`. The converse does **not** hold: `L(A) < L(B)` does not imply causality; the events may be concurrent.
- Ties are broken by node id, giving a total order usable for, say, choosing a winner among conflicting writes deterministically.

### Vector clock (detects concurrency)
- Each node keeps a vector `[c1, c2, ..., cn]`, one slot per node. Increment your own slot on events; on receive, take the element-wise max then increment yours.
- `V(A) < V(B)` (every component ≤ and at least one <) means A happened-before B. Neither ≤ means **concurrent**: a real conflict that needs merging ([[multi-leader-and-leaderless]]).
- Cost: O(nodes) per timestamp; Dynamo-style stores use per-key version vectors with pruning. Cassandra avoided them and uses last-writer-wins on wall clock, with the well-known consequences.

### Hybrid logical clock (HLC)
- Timestamp = `(physicalTime, logicalCounter)`. Physical part tracks the local clock but never goes backwards; the counter increments for events within the same millisecond or when a received timestamp is ahead.
- Keeps causality like Lamport, stays within bounded distance of real time, sortable as a 64-bit number. Used by CockroachDB, YugabyteDB, MongoDB's cluster time.

### Where this bites a backend developer
- **Last-writer-wins** on `updated_at` from different services silently drops updates when clocks disagree.
- **Ordering events** by producer timestamp across Kafka partitions is not causal ordering; use the partition offset for order within a key ([[kafka-architecture]]).
- **Token expiry / TTL** validated on a node whose clock is 30 s ahead rejects valid JWTs ([[jwt-authentication]]); allow clock skew leeway.
- **Distributed traces** with spans that end before they start: clock skew between services ([[distributed-tracing]]).
- **UUID v7 and Snowflake ids** embed wall time: sortable in practice, not a happened-before proof ([[time-money-and-ids]]).

## Example
A Lamport clock as a small thread-safe class, plus the ordering comparison with node-id tie-break that a conflict resolver would use.
```java
public final class LamportClock {
    private final AtomicLong counter = new AtomicLong();

    /** Local event or send: returns the stamp to attach to the message. */
    public long tick() {
        return counter.incrementAndGet();
    }

    /** Receive: merge the sender's stamp before processing. */
    public long receive(long remote) {
        return counter.updateAndGet(local -> Math.max(local, remote) + 1);
    }

    public record Stamp(long time, String nodeId) implements Comparable<Stamp> {
        @Override public int compareTo(Stamp o) {
            int byTime = Long.compare(time, o.time);
            return byTime != 0 ? byTime : nodeId.compareTo(o.nodeId);  // deterministic tie-break
        }
    }
}

// usage in a Kafka consumer: stamp arrives in a header, process in a consistent order
long stamp = clock.receive(Long.parseLong(new String(record.headers().lastHeader("lamport").value())));
```
Two nodes that each update the same subscriber "at the same time" will produce stamps where exactly one wins everywhere, regardless of whose wall clock is ahead.

## Pitfalls
- `new Date()`/`Instant.now()` for timeouts: an NTP step makes a 5 s timeout fire instantly or never. Use `nanoTime()` or a `Clock`-based `Duration`.
- Sorting a merged event stream by `createdAt` from several services and calling it "the order things happened".
- Storing local time without zone (`timestamp` instead of `timestamptz`) and then comparing across servers in different zones ([[time-money-and-ids]]).
- Trusting that a lease "still has time left" after a long pause; the holder must re-check with a fencing token on every protected write.
- Related: [[consistency-models]] for what "latest write" can mean, [[leases-heartbeats-and-health]] for leases built on clocks, [[failure-modes-and-timeouts]] for why a slow node looks dead.
