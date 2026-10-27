---
title: Quorums, split brain and leader election
tags: [distributed-systems, consensus, availability, theory]
learned: 2026-10-27
---
## TL;DR
A **quorum** is the minimum set of nodes that must agree before an action counts. With a **majority** quorum (more than N/2) any two quorums overlap in at least one node, so two groups can never both believe they are in charge: that overlap is what prevents **split brain**. Leader election is "obtain a majority quorum for the claim *I am leader for term T*", and a **lease** adds a time bound so a leader can act without re-asking on every operation. The cost: with N nodes you survive the loss of fewer than half, and you need 2f+1 nodes to tolerate f failures.

## Key points

### Majority math
| Nodes N | Quorum | Failures tolerated | Notes |
|---|---|---|---|
| 1 | 1 | 0 | single primary |
| 2 | 2 | 0 | worse than 1: either node down blocks writes |
| 3 | 2 | 1 | the usual minimum for etcd/ZooKeeper/KRaft |
| 4 | 3 | 1 | same tolerance as 3, more overhead |
| 5 | 3 | 2 | tolerates one failure during maintenance of another |
| 7 | 4 | 3 | diminishing returns; every write waits for 4 acks |

- Tolerance f = floor((N−1)/2). Always use odd N for voters; extra non-voting replicas (observers, learners) can serve reads without affecting the quorum.
- The overlap property is the whole point: quorum A ∩ quorum B ≠ ∅. The overlapping node knows about both claims and rejects the later-term one.

### Quorums for data, not just leaders
- Dynamo-style **W + R > N**: write to W replicas, read from R, and at least one node in the read set has the latest write. `N=3, W=2, R=2` is common; `W=1` is fast and unsafe; `W=N` is safe and unavailable ([[multi-leader-and-leaderless]]).
- Kafka's **ISR** is a quorum of a different shape: not a majority but "all replicas currently in sync", with `min.insync.replicas` as the floor. Data is committed when every ISR member has it, so Kafka tolerates N−1 failures for durability at the price of a shrinking ISR under stress ([[kafka-architecture]]).
- Postgres synchronous replication with `synchronous_standby_names = 'ANY 1 (s1, s2)'` is a write quorum of 2 out of 3 ([[postgres-replication-and-failover]]).

### Split brain: how it happens and how quorum stops it
- Partition splits {A, B} from {C}. C stops hearing from leader A and starts an election. Without quorum rules C becomes leader of its side while A stays leader of the other: two diverging histories, nothing to merge safely later.
- With majority quorum C cannot get 2 votes out of 3, stays follower and refuses writes; A and B continue. When the partition heals, C sees the higher term and catches up. Minority-side availability is sacrificed deliberately ([[cap-and-pacelc]]).
- **Fencing** is the second half: the old leader may be paused, not dead, and may still write to shared storage; every write must carry a term/epoch the receiver checks ([[distributed-locks-and-fencing]]).

### Leader election mechanisms
| Mechanism | How | Used by |
|---|---|---|
| Consensus vote (Raft/ZAB) | candidates ask for votes, majority wins a term | etcd, ZooKeeper, KRaft, Consul ([[raft-explained]]) |
| Coordination service lock | nodes race to create an ephemeral key; holder is leader; session expiry releases it | apps on ZooKeeper/etcd, Kafka's old controller election, Spring Integration `LockRegistryLeaderInitiator` |
| Database-backed lease | row with `leader, lease_until`; conditional `UPDATE ... WHERE lease_until < now()` | small services, Quartz clustering, ShedLock |
| External watchdog | Patroni uses etcd/Consul lease for the Postgres primary | HA Postgres |
| Bully / epoch broadcast | highest id wins, no quorum | legacy, unsafe under partitions |

### Leases: election without a vote per operation
- A **lease** is leadership with an expiry. The leader renews it before it expires (heartbeat); followers only start an election after expiry. A leader whose renewal fails must **stop acting** before the expiry time, otherwise two leaders overlap.
- Correctness depends on bounded clock drift between nodes and on the holder noticing expiry despite GC pauses; that is why a lease alone is not safe for writes and needs a fencing token ([[leases-heartbeats-and-health]], [[time-and-clocks]]).

### Interview framing
- "Three nodes, one dies?" Majority of 2 still writes; two dead means read-only or unavailable, by design.
- "etcd across two datacenters?" Not with an even split: 2+2 loses quorum when the link breaks. Use 3+2 or 2+2+1 with a tie-breaker.

## Example
A database-backed leader lease with a term (fencing token) for a small service that must run one scheduler instance. The conditional update is the quorum of one: Postgres serialises the row, so only one instance wins a term.
```sql
CREATE TABLE leader_lease (
    name        text PRIMARY KEY,
    holder      text NOT NULL,
    term        bigint NOT NULL,
    lease_until timestamptz NOT NULL
);

-- try to acquire or renew; returns a row only for the winner
UPDATE leader_lease
SET holder = :me,
    term = CASE WHEN holder = :me THEN term ELSE term + 1 END,
    lease_until = now() + interval '10 seconds'
WHERE name = 'esim-scheduler'
  AND (holder = :me OR lease_until < now())
RETURNING term;
```
The service calls this every 3 seconds; if it gets no row back, it stops scheduling immediately. Every write the scheduler performs carries `term` so that a stale instance that wakes up from a pause is rejected by a `WHERE term >= :current_term` check downstream ([[postgres-locks]] for why `now()` inside one statement is consistent).

## Pitfalls
- Two-node "clusters" with automatic failover: no majority exists, so either both can become primary (split brain) or neither can (no availability). Add a witness.
- Using Redis `SETNX` with TTL as an election: safe only if the elected node also fences its writes; the TTL can expire during a pause ([[redis-patterns-and-persistence]]).
- Counting quorum across zones without thinking about zone loss: 3 voters in one AZ is one failure away from nothing.
- Observers/learners accidentally configured as voters: an even count, and now a quorum needs more acks across the slowest link.
- Related: [[raft-explained]] for a full election protocol, [[failure-modes-and-timeouts]] for why a "dead" leader may not be dead.
