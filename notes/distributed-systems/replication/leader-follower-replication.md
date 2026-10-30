---
title: Leader-follower replication, sync vs async and failover
tags: [distributed-systems, replication, postgres, kafka]
learned: 2026-10-27
---
## TL;DR
In leader-follower (primary-replica, master-slave) replication one node accepts all writes and streams the change log to followers, which apply it in order. **Synchronous** replication waits for a follower's acknowledgement before confirming the commit (no acknowledged write is ever lost, but the leader stalls if the follower stalls); **asynchronous** confirms immediately (fast, but a leader crash loses the tail that followers never received). Failover promotes a follower, and the hard part is not promotion but deciding the old leader is really dead and discarding its unreplicated writes safely.

## Key points

### What flows from leader to follower
- A **log of changes**, not SQL statements: Postgres ships WAL records (physical) or decoded row changes (logical replication); MySQL ships the binlog; Kafka followers fetch the partition log; Raft replicates command entries ([[raft-explained]]).
- Statement-based replication is non-deterministic (`now()`, `random()`, auto-increment under concurrency) and is essentially dead.
- Followers apply the log in the leader's order, so a single follower is always a consistent prefix of the leader's history: it is *behind*, never *different*.

### Sync vs async vs semi-sync
| Mode | Commit returns when | Durability on leader loss | Latency | Availability if a follower dies |
|---|---|---|---|---|
| **Async** | leader has written locally | tail of writes lost (RPO > 0) | lowest | unaffected |
| **Sync (one follower)** | that follower has fsynced/applied | zero loss for acknowledged writes | + one round trip | leader blocks until a follower is back or config changes |
| **Semi-sync / quorum** | any K of N followers acked | zero loss while K−1 followers fail | + slowest of the fastest K | tolerates N−K follower failures |

- "Sync" has levels: the follower received the record, wrote it to disk, or applied it so reads see it. Postgres `synchronous_commit` = `remote_write` / `on` / `remote_apply` are exactly those ([[postgres-replication-and-failover]]).
- Kafka's model is quorum-sync with a dynamic set: `acks=all` waits for the whole ISR; a slow follower is removed from the ISR after `replica.lag.time.max.ms` instead of blocking the leader forever ([[kafka-architecture]]).

### Replication lag and what it breaks
- Async followers lag by milliseconds normally and by minutes under a bulk load, a long-running query on the replica, or a network hiccup. Reads from a follower are **eventually consistent**; the user-visible breakages are missing read-your-writes, non-monotonic reads and broken causality ([[consistency-models]], [[read-replicas-and-stale-reads]]).
- Measure lag in bytes and in time (Postgres `pg_stat_replication`, Kafka `UnderReplicatedPartitions` / consumer-group lag); alert on both ([[grafana-dashboards-and-red-use]]).

### Failover, step by step
1. **Detect** the leader is dead: heartbeats over a timeout. Too eager and you fail over on a GC pause; too lazy and you have minutes of downtime ([[failure-modes-and-timeouts]]).
2. **Choose** the new leader: the follower with the most recent log position, ideally via a quorum-backed election so that two followers cannot both decide ([[quorums-and-leader-election]]).
3. **Reconfigure** clients and remaining followers to the new leader: DNS/VIP swap, proxy (HAProxy/PgBouncer), or client-side discovery.
4. **Fence** the old leader so that if it comes back it cannot accept writes: STONITH, demote on reconnect, epoch number checks ([[distributed-locks-and-fencing]]).
- With async replication the old leader may hold committed writes the new leader never saw. Options: discard them (data loss) or keep them aside for manual repair. Famous consequence: an auto-increment key reused by the new leader collides with an external system that already saw the old value, and an external cache serves a row that no longer exists.

### Lost writes in practice
- Postgres async: a client got `COMMIT` ok, the primary dies, the promoted replica is 200 ms behind, those transactions are gone. For eSIM orders that means an order that was confirmed to the customer and does not exist ([[esim-provisioning-platform]]).
- Mitigation without going fully sync: `synchronous_commit = on` for the few critical transactions (it is settable per session/transaction), async for the rest.
- Kafka: `unclean.leader.election.enable=false` refuses to promote an out-of-sync replica; the partition stays offline instead of losing acknowledged records.

### Setting up a follower
- Copy a consistent snapshot of the leader (Postgres `pg_basebackup`), note the log position, stream from there. The leader must retain log from that position: Postgres **replication slots** do this and are the classic way to fill a disk when a follower is away ([[postgres-vacuum-and-bloat]]).

### Pros and cons of replication
| Pros | Cons |
|---|---|
| read scaling: spread reads over followers | eventual consistency on async followers: stale reads ([[read-replicas-and-stale-reads]]) |
| high availability: promote a follower when the leader dies | sync replication adds commit latency and can block writes |
| a follower near users in another region | more servers, WAL/bandwidth cost, failover tooling to run |
| backups and long reports run on a follower, not the leader | writes still go to one node: replication does not scale writes (that needs [[sharding-strategies]]) |
- Vocabulary: master/standby = leader/follower = primary/replica; multi-master = multi-leader ([[multi-leader-and-leaderless]]).

## Example
Postgres primary with one synchronous and one asynchronous standby, plus per-transaction control so only money-relevant commits pay for the sync round trip.
```yaml
# postgresql.conf on the primary
wal_level: replica
max_wal_senders: 10
synchronous_standby_names: 'FIRST 1 (standby_eu1, standby_eu2)'   # one must ack
synchronous_commit: 'on'          # cluster default: wait for one standby's fsync

# application: HikariCP connection-init for the bulk/reporting pool
spring:
  datasource:
    hikari:
      connection-init-sql: "SET synchronous_commit = 'local'"   # async for this pool only
```
Order-placing code runs on the default pool and keeps `on`; the reporting/analytics pool trades durability for throughput. Patroni on top handles detection, election through etcd, and fencing ([[postgres-replication-and-failover]] for the operational details).

## Pitfalls
- Enabling sync replication with a single standby and no fallback: a standby restart freezes every commit on the primary. Use `FIRST 1 (a, b)` or `ANY 1 (a, b)`.
- Auto-failover timeouts shorter than a full-GC or a network blip: flapping primaries and duplicate leaders. Patroni's `ttl`/`loop_wait` defaults (30 s/10 s) are conservative for a reason.
- Forgetting the old leader can come back: a stale primary accepting writes from clients that cached its address is the classic split brain.
- Reading your own write from a lagging follower and "fixing" it with a retry loop on the client.
- Related: [[multi-leader-and-leaderless]] for when one leader is not enough, [[database-scaling-path]] for where replicas sit in the scaling sequence, [[cap-and-pacelc]] for the sync-vs-async trade-off in theory terms.
