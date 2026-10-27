---
title: Read replicas and stale reads in practice
tags: [distributed-systems, replication, postgres, spring]
learned: 2026-10-27
---
## TL;DR
Read replicas are the first scaling step for a read-heavy service: route `SELECT`s to asynchronous followers and keep the primary for writes. The price is **replication lag**, which turns every replica read into an eventually-consistent read. Most of the engineering is deciding **which reads may be stale** (catalog, history, dashboards) and **which may not** (what the user just changed, anything used to make a decision), then routing accordingly, with a fallback when lag crosses a threshold.

## Key points

### Where lag comes from
- Network and apply delay: normally milliseconds.
- Replica busy: a long reporting query on a Postgres standby conflicts with WAL apply; with `hot_standby_feedback=off` the query is cancelled, with it on, the primary keeps dead tuples and bloats ([[postgres-vacuum-and-bloat]]).
- Primary bursts: a bulk `UPDATE`, an index build, a migration generates WAL faster than the replica applies it.
- Replica restart or network partition: lag grows to minutes; the replica is "healthy" from the load balancer's point of view and serves data from the past ([[failure-modes-and-timeouts]]).

### Classifying reads
| Read | Stale OK? | Route |
|---|---|---|
| Product catalog, tariff list, reference data | yes (seconds to minutes) | replica, even cache ([[caching]]) |
| User's own profile right after editing | no | primary, or replica with LSN wait |
| Balance before a debit, inventory before reserve | no, it is a decision | primary inside the write transaction (`SELECT ... FOR UPDATE`) ([[postgres-locks]]) |
| Reports, exports, analytics | yes | dedicated replica with `hot_standby_feedback` and long timeouts |
| Search results, feeds | yes, with monotonic reads per user | replica, sticky per session |

- "Decision reads" are the dangerous category: reading a stale balance on a replica and then writing on the primary is a lost update in slow motion, and no isolation level helps because the two statements are on different nodes ([[postgres-isolation-levels]]).

### Routing strategies
- **Static by use case**: a separate `DataSource`/repository for read-only use cases; the simplest and most explicit ([[read-write-splitting-and-cqrs-lite]]).
- **By transaction attribute**: Spring `@Transactional(readOnly = true)` plus a routing `DataSource` (`AbstractRoutingDataSource` or `LazyConnectionDataSourceProxy` keyed on `TransactionSynchronizationManager.isCurrentTransactionReadOnly()`). Easy to apply, easy to misroute: a `readOnly` method called right after a write in the same request reads stale data.
- **Proxy level**: PgBouncer/pgpool/ProxySQL or the cloud's reader endpoint split by statement type; opaque to the application and cannot know about read-your-writes.
- **Lag-aware**: a health checker polls `pg_last_wal_replay_lsn()` or `now() - pg_last_xact_replay_timestamp()`; replicas over the threshold are removed from the read pool ([[load-balancing]]).

### Giving users read-your-writes
- **Pin after write**: after a write, send that user's reads to the primary for N seconds (a cookie or a key in Redis). Simple, slightly over-routes.
- **LSN token**: the write returns `pg_current_wal_insert_lsn()`; subsequent reads wait until `pg_last_wal_replay_lsn() >= token` on the replica, or fall back to the primary. Exact, more code ([[consistency-models]] has the implementation).
- **Monotonic reads**: hash the user id to a replica so one session never jumps between replicas with different lag.
- With Kafka in the loop: a consumer that reads from a replica to "enrich" an event it just caused can miss its own write; read from the primary in consumers or carry the needed data in the event ([[event-driven-architecture]]).

### Observability
- Expose lag in bytes and seconds per replica; alert at a threshold tied to the product promise (if the UI says "updates within 5 s", alert at 3 s) ([[slo-sli-and-alerting]]).
- Tag queries with the target (`/* replica */` comments or a `db.role` span attribute) so a slow or stale read is attributable in traces ([[distributed-tracing]]).

## Example
A routing `DataSource` for Spring Boot: read-only transactions go to the replica pool, everything else to the primary; the replica is bypassed when its lag exceeds a limit.
```java
@Configuration
public class DataSourceConfig {
    @Bean @Primary
    DataSource routingDataSource(@Qualifier("primary") DataSource primary,
                                 @Qualifier("replica") DataSource replica,
                                 ReplicaLagMonitor lag) {
        var routing = new AbstractRoutingDataSource() {
            @Override protected Object determineCurrentLookupKey() {
                boolean readOnly = TransactionSynchronizationManager.isCurrentTransactionReadOnly();
                return readOnly && lag.withinLimit(Duration.ofSeconds(2)) ? "replica" : "primary";
            }
        };
        routing.setTargetDataSources(Map.of("primary", primary, "replica", replica));
        routing.setDefaultTargetDataSource(primary);
        // LazyConnectionDataSourceProxy defers getConnection() until the first statement,
        // so the readOnly flag is already set when the lookup key is computed
        return new LazyConnectionDataSourceProxy(routing);
    }
}

@Service
public class TariffQueryService {
    @Transactional(readOnly = true)       // -> replica when lag is fine
    public List<Tariff> listActive() { ... }
}
```
`ReplicaLagMonitor` runs `SELECT extract(epoch from now() - pg_last_xact_replay_timestamp())` on the replica every second. Without the `LazyConnectionDataSourceProxy`, Spring would acquire the connection before `readOnly` is visible and everything would hit the primary ([[spring-transactional]], [[hikaricp-pool-sizing]]).

## Pitfalls
- `@Transactional(readOnly = true)` on a method that is called from a write transaction: the outer transaction wins (propagation `REQUIRED`), and the read still goes to the primary; conversely a top-level read-only call right after a separate write hits the replica and may miss it.
- Replica used as a failover target *and* a read scaler: during failover you lose both the standby and the read capacity at once.
- Long reports on the replica without `hot_standby_feedback`: queries cancelled with "conflict with recovery"; with it on, bloat on the primary. Give analytics its own replica ([[postgres-replication-and-failover]]).
- Counting on the replica's `now()` for TTL or expiry checks while its clock and apply position differ from the primary's ([[time-and-clocks]]).
- Related: [[leader-follower-replication]] for the mechanics behind lag, [[database-scaling-path]] for what to do when replicas are not enough, [[distributed-cache-coherence]] for the same staleness problem one layer up.
