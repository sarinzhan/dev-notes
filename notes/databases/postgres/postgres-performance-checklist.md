---
title: Postgres performance checklist for a Spring service
tags: [postgres, performance, monitoring, connection-pool]
learned: 2026-10-27
---
## TL;DR
Most Postgres performance problems in a typical Spring Boot service come from five places, in this order: too many or too few connections, missing or wrong indexes, N+1 and chatty ORM access, long transactions and unbounded statements, and stale statistics or bloat. The checklist below is what to verify before touching server parameters, and `pg_stat_statements` is the tool that tells you where to start. The individual topics have their own notes; this one is the ordered walk-through for an incident or a pre-launch review.

## Key points

### 1. Connections
- Postgres forks one process per connection; each costs memory and a slot in the lock table, and beyond a few hundred the scheduler overhead dominates. Pool size per instance should be small: `connections = cores × 2 + spindles` as a starting point, typically 10–20 per service instance ([[hikaricp-pool-sizing]]). Ten pods × 20 = 200 backends; `max_connections = 300` with headroom for admins and replication.
- Put PgBouncer (transaction pooling) in front when the number of services grows; then `SET`-based features (advisory session locks, prepared statements with the old protocol) need care.
- Check `pg_stat_activity` for `idle in transaction`: a transaction open across an HTTP or Kafka call. Set `idle_in_transaction_session_timeout` and move remote calls out of `@Transactional` ([[spring-transactional]]).

### 2. Find the expensive statements
- Enable `pg_stat_statements` (`shared_preload_libraries`), then `SELECT queryid, calls, mean_exec_time, total_exec_time, rows, shared_blks_read FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20`. Sort by `total_exec_time` for throughput problems, by `mean_exec_time` for latency problems, by `calls` for N+1.
- `auto_explain` with `log_min_duration = 200ms` and `log_analyze = on` captures real plans of slow statements in the log; `log_min_duration_statement` alone gives the text.
- `pg_stat_user_tables.seq_scan` and `seq_tup_read` growing on a large table means a missing index; `pg_stat_user_indexes.idx_scan = 0` means a useless one ([[database-indexes]], [[postgres-indexes]]).

### 3. Query shape and the ORM
- N+1: `calls` in the hundreds of thousands for a `SELECT ... WHERE id = $1`; fix with fetch joins, `@EntityGraph`, batch size ([[jpa-n-plus-one]]).
- Pagination with `OFFSET 100000` reads and discards 100 000 rows; use keyset pagination (`WHERE (created_at, id) < (?, ?) ORDER BY created_at DESC, id DESC LIMIT 50`).
- `SELECT *` with a `jsonb` or `text` column pulls TOAST data on every read; project only needed columns, especially for list endpoints.
- Large `IN (...)` lists from `findAllById`: switch to `= ANY(?)` with an array parameter, which keeps one plan in the cache ([[query-planning-and-execution]]).
- `count(*)` for pagination totals on big tables is a full scan; estimate from `pg_class.reltuples` or drop the total.

### 4. Timeouts and limits
| Setting | Where | Purpose |
|---|---|---|
| `statement_timeout` | per role or in the JDBC URL `options=-c statement_timeout=5s` | no query runs forever; reporting roles get a longer one |
| `lock_timeout` | migrations | DDL fails fast instead of queueing ([[postgres-locks]]) |
| `idle_in_transaction_session_timeout` | server | frees locks and lets vacuum proceed ([[postgres-vacuum-and-bloat]]) |
| `connection-timeout`, `max-lifetime` | HikariCP | surface pool exhaustion as an error instead of a hang |
| `work_mem` | per session for reports | sorts and hashes stay in memory without risking the server globally |

### 5. Server-side basics
- `shared_buffers` ≈ 25 % of RAM, `effective_cache_size` ≈ 70 %, `random_page_cost = 1.1` on SSD, `max_wal_size` large enough that checkpoints happen on `checkpoint_timeout` and not on size (`pg_stat_bgwriter.checkpoints_req` should stay low), `wal_compression = on`.
- Cache hit ratio from `pg_stat_database` (`blks_hit / (blks_hit + blks_read)`) under ~99 % for OLTP means the working set does not fit in memory or an index is missing.
- Autovacuum per hot table, `ANALYZE` after bulk loads, bloat check monthly; partition tables with retention ([[postgres-partitioning]]).
- Replication lag and slot retention if replicas are read from ([[postgres-replication-and-failover]]).

### 6. Observability
- Export `pg_stat_*` with `postgres_exporter` into Prometheus/Mimir and dashboard: connections by state, transactions per second, cache hit ratio, longest transaction age, replication lag, dead tuples per table, slow queries from `pg_stat_statements`. Alert on `idle in transaction` age, connection pool saturation (`hikaricp_connections_pending`), and `age(datfrozenxid)` ([[grafana-dashboards-and-red-use]], [[slo-sli-and-alerting]]).

## Example
```yaml
# application.yml: bounded pool, statement timeout from the client, slow query log from the server
spring:
  datasource:
    url: jdbc:postgresql://pg-primary:5432/esim?options=-c%20statement_timeout=5000%20-c%20idle_in_transaction_session_timeout=30000
    hikari:
      maximum-pool-size: 16
      minimum-idle: 4
      connection-timeout: 3000     # fail the request instead of queueing forever
      max-lifetime: 1800000        # 30 min, below any LB/PgBouncer idle timeout
      leak-detection-threshold: 20000
  jpa:
    properties:
      hibernate.jdbc.batch_size: 50
      hibernate.order_inserts: true
      hibernate.default_batch_fetch_size: 50
```
```sql
-- The weekly check: top statements by total time, with cache behaviour
SELECT calls, round(mean_exec_time::numeric, 2) AS mean_ms,
       round(total_exec_time::numeric / 1000, 1) AS total_s,
       shared_blks_hit, shared_blks_read, left(query, 80) AS query
FROM pg_stat_statements
ORDER BY total_exec_time DESC LIMIT 10;
```

## Pitfalls
- Raising `maximum-pool-size` to "fix" slow queries: more backends compete for the same CPU and locks, throughput drops, and the database becomes the single congested resource for every service.
- `statement_timeout` set only in the application: a migration or an ad-hoc analyst query has no limit. Set a default per role.
- Tuning `shared_buffers` and `work_mem` before looking at `pg_stat_statements`: the top query is usually an index or N+1 fix that no parameter can replace.
- Reading `mean_exec_time` without `calls`: a 2 ms query called 5 million times a day is the problem, the 3 s report once a night is not.
- Related: [[explain-analyze]] for the individual query, [[database-scaling-path]] for what comes after the checklist is exhausted.
