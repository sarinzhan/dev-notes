---
title: Consistency models from linearizable to eventual
tags: [distributed-systems, consistency, replication, theory]
learned: 2026-10-27
---
## TL;DR
A consistency model is a contract about which values a read may return when data lives on several nodes. From strongest to weakest: **linearizable** (one copy, real-time order), **sequential** (one global order, but not tied to wall-clock), **causal** (only cause-and-effect order is preserved), **eventual** (replicas converge if writes stop). Between them sit the **session guarantees** you actually ask for in application code: read-your-writes, monotonic reads, monotonic writes. Stronger models cost latency and availability; pick the weakest model that keeps the business invariant.

## Key points

### The ladder
| Model | Guarantee | Cost | Where you meet it |
|---|---|---|---|
| **Linearizable** | Every operation appears to take effect atomically at some instant between its start and end; all clients agree on that order and it matches real time | Quorum or leader round trip per operation; unavailable under partition (CP in [[cap-and-pacelc]]) | etcd, ZooKeeper, Postgres primary, Kafka reads from leader, uniqueness checks, locks ([[distributed-locks-and-fencing]]) |
| **Sequential** | All clients see the same order of operations, and each client's own operations stay in program order; the global order need not match real time | Cheaper than linearizable: a replica may lag as long as everyone sees the same history | Kafka single partition for a single consumer group; most "serializable but replicated" setups |
| **Causal** | If write B happened after reading A, nobody sees B without A; concurrent writes may be seen in any order | Needs version vectors or dependency tracking | Comment under a post never appears before the post; MongoDB causal sessions |
| **Eventual** | If writes stop, all replicas converge to the same value; no bound on when, no ordering promise | Cheapest; always available | DNS, async read replicas, Cassandra at `ONE`, caches |

### Session guarantees (the ones you code against)
- **Read-your-writes**: after I save my profile, my next request shows it. Broken by async replicas when the read is routed to a lagging follower ([[read-replicas-and-stale-reads]]). Fix: route the user's reads to the leader for N seconds after a write, or pass the last write's LSN/offset and wait for the replica to catch up.
- **Monotonic reads**: once I have seen version 5, I never see version 4 again. Broken by round-robin over replicas. Fix: sticky routing per user/session.
- **Monotonic writes**: my own writes are applied in the order I issued them. Broken by multi-leader setups with independent paths ([[multi-leader-and-leaderless]]).
- **Writes-follow-reads**: a reply I write after reading a message is ordered after that message. This is the causal guarantee at session level.

### Linearizable vs serializable
- Linearizability is about a **single object** and **real time**: one register, reads see the latest write.
- Serializability is about **transactions over many objects**: the result equals *some* serial order, not necessarily the real-time one ([[transactions-acid-and-isolation]], [[postgres-isolation-levels]]).
- "Strict serializable" = both. A single-node Postgres under `SERIALIZABLE` is effectively strict serializable; add an async replica and reads from it are serializable-but-stale.

### Why linearizable reads are expensive
- A leader can only answer a linearizable read if it is *sure* it is still the leader. A deposed leader with a stale lease would answer with old data. Raft solves this with a read index (heartbeat round to the majority) or lease-based reads ([[raft-explained]], [[leases-heartbeats-and-health]]).
- A quorum read in Dynamo-style systems (`R + W > N`) gives "a read sees the latest *acknowledged* write" but is still not linearizable under concurrent writes without read repair; DDIA's classic example.

### Choosing in practice
- Money, inventory, unique handles, leader election: linearizable, through one leader or a consensus store.
- Timelines, counters, analytics, notifications: eventual with session guarantees where the user would notice.
- Anything "the user just did and now expects to see": read-your-writes is the minimum.

### Eventual consistency, concretely
- Where it comes from: an async read replica that is 200 ms behind, a cache entry that lives until its TTL, a search index fed by CDC, a second region. In each case a read right after a write can return the old value.
- It is not limited to distributed databases: a single Postgres with a read replica, or Redis in front of it, is already eventually consistent for whoever reads from the replica or cache ([[read-replicas-and-stale-reads]], [[caching]]).
- Make it acceptable rather than pretending it is not there: read your own writes from the primary, show "processing" states, version cache keys, and design idempotent consumers that tolerate reordering ([[idempotency]]).
- Two meanings of "consistency" (ACID vs CAP) are compared in [[transactions-acid-and-isolation]].

## Example
Read-your-writes on top of Postgres streaming replication: the writer returns the commit LSN, the reader waits for the replica to replay past it (or falls back to the primary) before serving the follow-up request.
```java
@Service
@RequiredArgsConstructor
public class SubscriberService {
    private final JdbcTemplate primary;
    private final JdbcTemplate replica;

    /** Returns the WAL position the client should send back as a token. */
    @Transactional
    public String updateTariff(UUID subscriberId, String tariff) {
        primary.update("UPDATE subscriber SET tariff = ? WHERE id = ?", tariff, subscriberId);
        return primary.queryForObject("SELECT pg_current_wal_insert_lsn()::text", String.class);
    }

    public Subscriber get(UUID subscriberId, String lsnToken) {
        boolean caughtUp = lsnToken == null || Boolean.TRUE.equals(replica.queryForObject(
            "SELECT pg_last_wal_replay_lsn() >= ?::pg_lsn", Boolean.class, lsnToken));
        JdbcTemplate source = caughtUp ? replica : primary;   // cheap path, else leader
        return source.queryForObject("SELECT id, tariff FROM subscriber WHERE id = ?",
            (rs, i) -> new Subscriber(rs.getObject("id", UUID.class), rs.getString("tariff")), subscriberId);
    }
}
```
The token can travel in a cookie or a response header; it gives one session its own consistency without forcing every read onto the primary ([[postgres-replication-and-failover]]).

## Pitfalls
- Calling an async-replicated system "consistent" because it is ACID per node: ACID is about transactions on one node, consistency models are about what other nodes see.
- Serving a read from a cache or replica right after a write and then "fixing" it with a client-side refresh: that is a missing read-your-writes guarantee, not a UI bug ([[distributed-cache-coherence]]).
- Assuming Kafka is linearizable across partitions: order holds only within one partition; a consumer reading two partitions can see effects out of causal order ([[kafka-consumer]]).
- Choosing linearizable everywhere "to be safe" and then wondering why p99 latency doubled and the system goes down with one availability zone ([[latency-numbers]]).
- Related: [[cap-and-pacelc]] for the trade-off these models sit in, [[time-and-clocks]] for why "latest" needs logical clocks, [[leader-follower-replication]] for the mechanics behind lag.
