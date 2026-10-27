---
title: Distributed locks and fencing tokens
tags: [distributed-systems, locks, redis, etcd, reliability]
learned: 2026-10-27
---
## TL;DR
A distributed lock says "only one process may do X at a time", but no lock service can make a *paused* or *partitioned* holder stop. After a GC pause or a lost heartbeat the lock expires, another process acquires it, and the first one wakes up still believing it holds the lock: two writers at once. The fix is a **fencing token**: a number that increases with every lock acquisition and that the **protected resource** (the database, the storage service) checks, rejecting any write with a token lower than the highest it has seen. Without fencing, a distributed lock is an optimisation for efficiency, not a correctness guarantee.

## Key points

### Two reasons to lock, with different requirements
| Purpose | If it fails | Needs |
|---|---|---|
| **Efficiency**: avoid doing the same expensive job twice (send a report, rebuild a cache) | wasted work, a duplicate email | a TTL lock in Redis, ShedLock, a DB advisory lock; cheap and good enough |
| **Correctness**: prevent two writers from corrupting shared state (double activation, double charge) | data loss or money | lock **plus fencing** at the resource, or no lock at all and a transactional/idempotent design |

- Most "we need a distributed lock" conversations are the first case. For the second case, ask first whether the database can enforce the invariant directly (unique constraint, conditional update, `SELECT ... FOR UPDATE`) before adding a lock service ([[postgres-locks]], [[jpa-locking]]).

### Why the lock alone is unsafe
1. Client A acquires lock with a 30 s TTL.
2. A hits a 40 s stop-the-world GC pause, or the network to the lock service drops.
3. The lock expires; client B acquires it and writes.
4. A resumes, believes it still holds the lock, and writes stale data over B's.

No timeout tuning fixes this: the holder cannot know it has paused until after the pause ([[time-and-clocks]], [[failure-modes-and-timeouts]]). The lock service was never wrong; the resource trusted a client that had no way to know it was late.

### Fencing tokens
- The lock service returns a **monotonically increasing** token with every grant: ZooKeeper's `zxid` / znode version, etcd's lease id plus revision, a Postgres sequence or `term` column in a lease table ([[quorums-and-leader-election]]).
- Every write to the protected resource carries the token. The resource stores the highest token it has accepted and rejects anything lower: `UPDATE ... WHERE fence_token <= :token` style, or a compare-and-set on an object version ([[lock-free-and-cas]]).
- The resource must be able to enforce this, which rules out "resources" like an SMTP server or a third-party API. For those you fall back to idempotency keys ([[idempotency]]).

### Lock services compared
| | Redis `SET NX PX` (single node) | Redlock (N Redis masters) | ZooKeeper / etcd | Postgres advisory / row lock |
|---|---|---|---|---|
| Safety under node loss | lock can be lost on failover (async replication) | debated: relies on bounded clock drift and no pauses, no fencing token by itself | consensus-backed, linearizable, provides a monotonic token | as safe as the DB; lock dies with the session |
| Liveness on holder crash | TTL expiry | TTL expiry | session/lease expiry (heartbeat) | connection close |
| Latency | sub-ms | ms, N round trips | ms (quorum write) | sub-ms, same DB |
| Good for | efficiency locks, rate limits | not recommended over simpler options | leader election, correctness locks with fencing | locks that protect data in that same DB |

- The Redlock debate (Kleppmann vs antirez): Redlock assumes no process pause longer than the TTL and well-behaved clocks; without fencing it is still "lock then hope". For efficiency it is fine; for correctness use a consensus store or the database ([[redis-patterns-and-persistence]]).
- In Spring: ShedLock (`@SchedulerLock`) for "run this job on one instance", Spring Integration `JdbcLockRegistry`/`RedisLockRegistry` for explicit locks; Temporal workflows remove the need for most locks by giving each workflow id a single execution ([[temporal-concepts]]).

### Lock-free alternatives (often better)
- **Unique constraint** for "only one activation per ICCID": the database is the arbiter.
- **Conditional update** with a version column: optimistic locking is a fencing token you already have ([[jpa-locking]]).
- **Single-writer per key**: route all work for one key to one Kafka partition and one consumer ([[sharding-strategies]], [[kafka-consumer]]).

## Example
A fencing check in the protected resource. The lock service (etcd, ZooKeeper, or the lease table from [[quorums-and-leader-election]]) hands out `token`; the storage rejects stale writers at the row level.
```java
@Repository
@RequiredArgsConstructor
public class ProfileStateRepository {
    private final JdbcTemplate jdbc;

    /** Returns false when a newer lock holder has already written: the caller must abort. */
    public boolean writeState(String iccid, String newState, long fenceToken) {
        int rows = jdbc.update("""
            UPDATE esim_profile
               SET state = ?, last_fence_token = ?, updated_at = now()
             WHERE iccid = ?
               AND last_fence_token < ?          -- reject if we are stale
            """, newState, fenceToken, iccid, fenceToken);
        return rows == 1;
    }
}

// in the service
long token = lock.acquire("profile:" + iccid);         // e.g. etcd lease revision
try {
    if (!repo.writeState(iccid, "DOWNLOADED", token)) {
        throw new StaleLockException("fence token " + token + " rejected for " + iccid);
    }
} finally {
    lock.release("profile:" + iccid);
}
```
A client that paused through its TTL still calls `writeState` later, but with a token lower than the one the new holder already used, so the update matches zero rows and the stale writer finds out ([[postgres-locks]]).

## Pitfalls
- Releasing a Redis lock with a plain `DEL`: you may delete someone else's lock after your TTL expired. Store a random value and delete only if it matches, in a Lua script.
- Renewing the TTL in a background thread (a "watchdog") hides the pause problem instead of solving it; the renewal thread pauses with the rest of the JVM.
- Taking a distributed lock and then a DB lock inside it, while another path takes them in the opposite order: a cross-system deadlock nobody's detector sees ([[deadlocks-and-synchronization-primitives]]).
- Holding a lock across an external HTTP call with no timeout; the lock's TTL becomes your only bound ([[circuit-breaker]]).
- Related: [[leases-heartbeats-and-health]] for the liveness half, [[raft-explained]] for why etcd's revision is trustworthy, [[idempotency]] for resources that cannot check a token.
