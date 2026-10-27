---
title: Postgres streaming replication and failover mechanics
tags: [postgres, replication, high-availability, wal]
learned: 2026-10-27
---
## TL;DR
Postgres replicates by shipping the **write-ahead log (WAL)** from a primary to standbys. Physical streaming replication copies the byte-level WAL so standbys are exact read-only copies; logical replication decodes the WAL into row changes for selected tables and can cross versions. Replication is asynchronous by default; `synchronous_commit` plus `synchronous_standby_names` makes commit wait for one or more standbys. Postgres itself does not elect a new primary; a tool such as Patroni does. The theory of leader-follower replication, lag and lost writes is in [[leader-follower-replication]]; this note is the Postgres mechanics.

## Key points

### Physical streaming replication
- The standby starts from a base backup (`pg_basebackup`) and connects with a replication connection (`primary_conninfo`); the primary's **walsender** process streams WAL records, the standby's **walreceiver** writes them and the startup process replays them continuously ("hot standby", readable while replaying).
- Everything replicates: all databases in the cluster, DDL, indexes, vacuum. The standby must be the same major version and architecture.
- A standby is **read-only**; writes fail with `cannot execute INSERT in a read-only transaction`. Spring routes read-only traffic with a routing `DataSource` ([[read-replicas-and-stale-reads]], [[read-write-splitting-and-cqrs-lite]]).
- **Replication slots** (`pg_create_physical_replication_slot`) make the primary keep WAL until the standby has consumed it, so a standby that is down for an hour can catch up instead of needing a new base backup. The price: a dead slot keeps WAL forever and fills the disk; `max_slot_wal_keep_size` (PG 13+) caps it and marks the slot `lost`.
- Cascading replication: a standby can feed other standbys to spare the primary's network.

### Synchronous vs asynchronous
| `synchronous_commit` | Commit returns when | Lost on primary crash | Typical use |
|---|---|---|---|
| `off` | WAL buffered locally | last ~200 ms of commits | bulk loads, non-critical data |
| `local` (default behaviour with no sync standby) | WAL fsynced on primary | nothing locally; everything not yet shipped if the primary disk dies | default |
| `remote_write` | standby received and wrote WAL to OS cache | only if both crash at once | HA with low latency cost |
| `on` | standby fsynced WAL | nothing | money, orders |
| `remote_apply` | standby replayed WAL, visible to queries | nothing, and read-your-writes on the standby | reads routed to replicas |

- `synchronous_standby_names = 'FIRST 1 (replica1, replica2)'` or `'ANY 1 (...)'` names which standbys count. If no listed standby is connected, **commits hang**: a sync setup needs at least two standbys or an operator ready to flip the setting.
- `synchronous_commit` can be set per transaction (`SET LOCAL synchronous_commit = off` for an audit insert), which is cheaper than a global choice.

### Monitoring lag
- On the primary: `pg_stat_replication` gives `sent_lsn`, `write_lsn`, `flush_lsn`, `replay_lsn` and `write_lag`/`flush_lag`/`replay_lag` per standby.
- On the standby: `pg_last_wal_receive_lsn()`, `pg_last_wal_replay_lsn()`, `now() - pg_last_xact_replay_timestamp()` as a time-based lag estimate (misleading on an idle primary).
- Replay can lag behind receive when the standby runs long queries that conflict with vacuum: `max_standby_streaming_delay` decides whether to cancel the query (`canceling statement due to conflict with recovery`) or delay replay. `hot_standby_feedback = on` tells the primary not to vacuum rows the standby still needs, at the cost of bloat on the primary ([[postgres-vacuum-and-bloat]]).

### Failover and switchover
- **Promotion**: `pg_ctl promote` or `SELECT pg_promote()` turns a standby into a primary on a new timeline. The old primary must never accept writes again without being rebuilt (`pg_rewind` can resync it if WAL is available), otherwise you have split brain.
- Postgres has no built-in leader election. **Patroni** runs next to each instance, keeps a leader lease in etcd/Consul/Kubernetes, promotes the most up-to-date standby when the lease expires, and rewrites `primary_conninfo` on the others. Clients reach the current leader through HAProxy health checks against Patroni's REST API, or through a Kubernetes service that Patroni relabels ([[quorums-and-leader-election]], [[leases-heartbeats-and-health]]).
- Asynchronous failover loses the un-shipped tail of WAL: commits the application already acknowledged to users. Patroni's `maximum_lag_on_failover` refuses to promote a standby that is too far behind; synchronous mode (`synchronous_mode: true`) removes the loss at the cost of latency.
- Connection strings can list several hosts with `target_session_attrs=read-write` (PG 10+); the JDBC driver reconnects to whichever host accepts writes.

### Logical replication
- `CREATE PUBLICATION` on the source, `CREATE SUBSCRIPTION` on the target; the walsender decodes WAL into inserts/updates/deletes (`pgoutput` plugin) via a logical slot. Tables need a `REPLICA IDENTITY` (primary key by default) for updates and deletes.
- Use cases: major-version upgrades with minimal downtime, replicating a subset of tables to a reporting database, feeding Debezium/CDC for the [[outbox-pattern]]. DDL and sequences (before PG 17) are not replicated; migrations must run on both sides.

## Example
```bash
# Lag per standby, run on the primary
psql -x -c "SELECT application_name, state, sync_state,
                   pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS replay_bytes_behind,
                   replay_lag
            FROM pg_stat_replication;"
# -[ RECORD 1 ]-------+----------------
# application_name    | replica1
# state               | streaming
# sync_state          | sync
# replay_bytes_behind | 16384
# replay_lag          | 00:00:00.004

# Slots that keep WAL on disk even when nobody is reading them
psql -c "SELECT slot_name, active, wal_status,
                pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
         FROM pg_replication_slots;"
```
```yaml
# patroni.yml fragment: one synchronous standby, refuse failover beyond 1 MB of lag
bootstrap:
  dcs:
    synchronous_mode: true
    maximum_lag_on_failover: 1048576
    postgresql:
      parameters:
        wal_level: replica
        max_wal_senders: 10
        hot_standby_feedback: "on"
```

## Pitfalls
- An orphaned replication slot (decommissioned replica, stopped Debezium connector) fills the data volume with WAL; alert on `pg_replication_slots` and set `max_slot_wal_keep_size`.
- Reading from an async replica right after a write: the row is not there yet. Route read-after-write to the primary or use `remote_apply`.
- Treating `synchronous_commit = on` with one standby as "HA": when that standby dies, every commit blocks until someone intervenes.
- Promoting a standby while the old primary is still up and reachable by clients: two primaries, divergent data, no automatic merge. Fence the old node (Patroni does, hand-rolled scripts rarely do).
- Theory and lag semantics: [[leader-follower-replication]]; where this sits on the scaling path: [[database-scaling-path]].
