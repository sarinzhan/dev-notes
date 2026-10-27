---
title: HikariCP pool sizing
tags: [jdbc, performance, postgres, spring-boot]
learned: 2026-10-02
---
## TL;DR
A small pool is usually faster. Hikari's rule of thumb: `connections = cores * 2 + effective_spindle_count`, so 10–20 for a typical Postgres box, shared across **all** app instances. More connections mostly add context switching and lock contention on the database side. Most "pool exhausted" incidents are not a sizing problem but a transaction that holds a connection while waiting on the network.

## Key points

### Why bigger pools are slower
- The database can only run as many queries in parallel as it has cores. Extra connections queue inside Postgres (on CPU, on buffer locks, on the WAL lock) instead of inside Hikari, and queueing inside the DB is more expensive.
- The formula counts cores of the **database** server, not the app. Spindles matter for disk-bound workloads; on SSD/NVMe the term is roughly 1.
- Each Postgres connection is a forked backend process with its own memory (`work_mem` per sort/hash, a few MB of base overhead). 200 app pods × 50 connections = 10 000 backends: the box runs out of memory before it runs out of queries.
- `max_connections` in Postgres (default 100) is a hard global limit. Pool size per instance × number of instances + migrations + monitoring + admin sessions must stay below it, with headroom for a rolling deploy when old and new pods run at the same time.
- When the arithmetic does not work out, put PgBouncer in **transaction** pooling mode in front: thousands of client connections map to a few dozen server connections. Costs: no session state (no `SET`, no prepared statements unless `max_prepared_statements` in PgBouncer 1.21+, no advisory locks across transactions, no `LISTEN`).

### Hikari timeouts
| Property | Default | Meaning |
|---|---|---|
| `connectionTimeout` | 30 s | how long `getConnection()` waits before `SQLTransientConnectionException`; lower it to 1–3 s so a saturated pool fails fast |
| `idleTimeout` | 10 min | when idle connections above `minimumIdle` are closed; irrelevant with a fixed pool |
| `maxLifetime` | 30 min | retire a connection regardless; keep it below the DB/load-balancer idle kill (Postgres `idle_session_timeout`, cloud NAT ~ 10–15 min) |
| `keepaliveTime` | 0 | ping idle connections so firewalls do not drop them |
| `validationTimeout` | 5 s | for the `isValid()` check before handing out a connection |
| `leakDetectionThreshold` | 0 (off) | log a stack trace when a connection is held longer than this |
- `maximumPoolSize = minimumIdle` (fixed pool) avoids open/close churn under bursty load, which is what Hikari's authors recommend.

### Pool exhaustion
- Symptom: `HikariPool-1 - Connection is not available, request timed out after 30000ms`, latency spikes exactly at `connectionTimeout`, `hikaricp.connections.pending` > 0 while `hikaricp.connections.active == maximumPoolSize`.
- Common cause: a [[spring-transactional|@Transactional]] method that calls an HTTP API, Kafka, or S3. The connection is held for the whole call; with a 10-connection pool, ten slow calls to a partner block every other DB operation in the app, including health checks.
- Other causes: Open Session In View holding the connection during response rendering, `REQUIRES_NEW` inside `REQUIRED` (two connections per thread, deadlock when the pool is smaller than the thread count), forgotten `close()` on a raw `DataSource.getConnection()`.
- With [[virtual-threads]] the thread pool is no longer the limit, so the connection pool becomes the first bottleneck; the queueing just moves to `getConnection()`.

### Metrics to watch
- Micrometer exposes `hikaricp.connections.{active,idle,pending,timeout,usage,acquire,creation}`; `pending` and `timeout` are the alerts, `usage` (time a connection is held) is the diagnosis.
- On the Postgres side: `pg_stat_activity` count by `state`, `idle in transaction` sessions (an app holding a transaction open), `max_connections` utilization.

## Example
```yaml
spring:
  datasource:
    hikari:
      maximum-pool-size: 16
      minimum-idle: 16
      connection-timeout: 3000          # fail fast
      max-lifetime: 1500000             # 25 min, below PgBouncer/NAT idle limits
      keepalive-time: 300000            # 5 min
      leak-detection-threshold: 20000   # log held > 20 s
      data-source-properties:
        ApplicationName: esim-provisioning   # visible in pg_stat_activity
  jpa:
    open-in-view: false
```
Rule of thumb for the numbers: 3 pods × 16 = 48 connections against a Postgres with `max_connections = 100` leaves room for Flyway, Grafana and a second deployment during rollout.

## Pitfalls
- `leak-detection-threshold` is the fastest way to find a forgotten `close()` or a transaction that calls an external API; it only logs, it does not reclaim the connection.
- Setting `maximumPoolSize` to 100 "to be safe" makes p99 latency worse under load and can trigger `FATAL: too many connections` for the migration job.
- `maxLifetime` above the infrastructure idle timeout gives sporadic `Connection reset` errors on the first query after a quiet period.
- Spring Boot's `@Scheduled` relays ([[outbox-pattern]]) share the same pool; a stuck relay transaction is invisible from the HTTP side until the pool empties.
- `validationTimeout` and `connection-test-query` are unnecessary for JDBC4 drivers; Postgres JDBC supports `isValid()`.
- Related: [[spring-boot-properties-and-profiles]] for overriding per environment, [[postgres-isolation-levels]] for what `idle in transaction` sessions block.
