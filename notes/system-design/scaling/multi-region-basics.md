---
title: Multi-region basics — active/passive, active/active, data residency
tags: [system-design, scaling, availability, multi-region, consistency]
learned: 2026-10-27
---
## TL;DR
A second region buys two things: survival of a whole data-centre failure, and lower latency for users far from the first region. **Active/passive** keeps a warm copy and fails over; it is simple and the data problem is "one-way replication plus a few seconds of possible loss". **Active/active** serves writes in several regions; it is fast everywhere but every write conflict, every uniqueness check and every "latest value" question becomes a distributed-systems problem. Most companies, including telecoms serving one country, need active/passive plus a CDN, not active/active. Regulation (data residency) can force the choice before latency does.

## Key points

### Why go multi-region at all
- **Availability**: AZ failures are handled inside a region; a region-wide outage (power, network, cloud control plane) needs a second region. Measured as RTO (time to recover) and RPO (data you may lose).
- **Latency**: a round trip across continents is 100–300 ms ([[latency-numbers]]); a page with three sequential calls to a far region feels broken. Static content goes to a [[cdn]] first; only dynamic, personalised data needs regional compute.
- **Residency**: GDPR, local telecom or banking rules may require that subscriber data stay in-country. Then the region split follows the law, not the traffic map.

### Topologies
| | Active/passive | Active/active (partitioned) | Active/active (shared data) |
|---|---|---|---|
| Writes | one region | each region owns a subset of users/tenants | any region, any row |
| Data | async replication primary → standby | per-partition primaries, cross-replicated for DR | multi-leader or consensus across regions |
| Failover | DNS/LB switch + promote standby, minutes | reroute the partition, minutes | automatic, seconds |
| Conflicts | none | none (writes are partitioned) | yes: LWW, CRDTs, or app merge |
| Latency for writes | far users pay the round trip | local | local, or +1 RTT if synchronous consensus |
| Complexity | low | medium | high |

- Partitioned active/active ("home region" per user, like a telecom with one country per region) gives most of the benefit with none of the conflict handling; it is sharding at region level ([[sharding-strategies]]).
- Shared-data active/active needs [[multi-leader-and-leaderless]] replication or a globally consistent store (Spanner, CockroachDB, YugabyteDB) that pays a cross-region round trip per commit ([[raft-explained]], [[quorums-and-leader-election]]).

### The data layer decides everything
- Postgres across regions: asynchronous streaming replication to a standby in region B ([[postgres-replication-and-failover]]). RPO = replication lag at failure time (usually under a second, more under bursts). Synchronous replication across regions makes every commit wait for the far region: fine at 10 ms, painful at 100 ms.
- Kafka: MirrorMaker 2 or cluster linking replicates topics; offsets differ per cluster, so consumers must be able to resume by timestamp or by idempotent reprocessing ([[idempotency]]).
- Redis: cross-region caches are usually independent per region; treat a cache as a cache, never as the source of truth ([[caching]]).
- Object storage replicates asynchronously bucket-to-bucket ([[file-and-blob-storage]]).

### Traffic steering
- Global load balancing at DNS level (latency- or geo-based records, health-checked, low TTL) or anycast ([[load-balancing]], [[dns-resolution]]). DNS TTLs and client caches mean a failover takes minutes to be seen by everyone; design the standby to accept early traffic gracefully.
- Keep a region self-sufficient: a request that lands in region B must not call services in region A on the hot path, or the latency win disappears and the failure domains merge.

### Failover mechanics (active/passive)
1. Detect: health checks from outside both regions, not from inside the failing one ([[failure-modes-and-timeouts]], [[leases-heartbeats-and-health]]).
2. Fence: make sure the old primary cannot accept writes (stop it, revoke credentials, or use a fencing token) to avoid split brain ([[distributed-locks-and-fencing]]).
3. Promote the standby, repoint the application (role-based discovery, not hostnames).
4. Switch traffic (DNS/LB), then replay or reconcile anything that was in flight.
5. Rehearse: an untested failover is a plan, not a capability. Game days quarterly.

### Interview angles
- "Active/active or active/passive?" Users in one country with in-country data rules: active/passive in a second in-country site; global users with per-user writes: partitioned active/active.
- "A user's home region is down, they write?" Reject for that partition (CP) or accept in the DR region and reconcile later (AP); say which and why ([[cap-and-pacelc]], [[consistency-models]]).
- "What RPO/RTO can you promise?" RPO = replication lag, RTO = detection + promotion + DNS propagation: seconds / 5–15 minutes.

## Example
A two-region Postgres setup with an in-country standby, asynchronous for throughput but with lag alerting; the app discovers the primary by role via Patroni/HAProxy rather than by host:

```yaml
# region-a primary
postgresql.conf:
  wal_level: replica
  max_wal_senders: 10
  synchronous_commit: "on"            # local durability; standby is async
  synchronous_standby_names: ""      # no cross-region sync wait

# region-b standby (streaming from a replication slot)
primary_conninfo: "host=pg-a.internal port=5432 user=replicator application_name=standby_b"
primary_slot_name: "standby_b"

# alert: lag over 30 s or slot inactive → page, because RPO is no longer "seconds"
# promql (postgres_exporter)
# pg_replication_lag_seconds{application_name="standby_b"} > 30
# pg_replication_slots_active{slot_name="standby_b"} == 0
```

The app's JDBC URL lists both HAProxy endpoints with `targetServerType=primary`, so after promotion it reconnects to whichever node reports itself as primary without a config change.

## Pitfalls
- Cross-region synchronous replication "for zero RPO" without measuring the write latency it adds to every transaction.
- Failover that promotes the standby while the old primary is still serving writes: two primaries, diverged data, and a painful manual merge.
- Services in region B that still point to Redis, Kafka or a config server in region A; the outage takes both regions down.
- Residency assumed satisfied by region placement while backups, logs with MSISDNs, or the observability stack ship data elsewhere ([[observability-three-pillars]]).
- Related: [[horizontal-vs-vertical-scaling]], [[leader-follower-replication]], [[time-and-clocks]] for why "latest write" is ambiguous across regions.
