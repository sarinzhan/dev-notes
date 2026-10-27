---
title: Read/write splitting and CQRS-lite
tags: [system-design, scaling, cqrs, consistency, postgres]
learned: 2026-10-27
---
## TL;DR
Most services are read-heavy (10:1 to 100:1), so the cheapest scaling move is to send reads somewhere other than the primary: a replica, a cache, or a purpose-built read model. "CQRS-lite" means separate read and write **code paths** (queries return DTOs straight from SQL or a projection, commands go through the domain model) without the full event-sourced architecture. The price is always the same: the read side can be stale, so every read-after-write flow must be identified and routed to the fresh source. Full CQRS with separate stores is owned by [[cqrs]].

## Key points

### Three levels of splitting
| Level | Read source | Write source | Staleness | When |
|---|---|---|---|---|
| Code-level | same DB, different code path (JDBC projections) | domain model + JPA | none | always worth doing; cheap |
| Replica-level | Postgres streaming replica | primary | replication lag (ms–s) | read throughput exceeds one node |
| Model-level | denormalised read model (table, Redis, Elasticsearch) | primary, events via outbox | async projection lag | reads need a different shape or store |

### Code-level: separate query path
- Commands load aggregates, check invariants, save ([[aggregates-and-invariants]]). Queries do not need entities: a `JdbcClient`/`JdbcTemplate` projection into a record avoids the persistence context, lazy loading and N+1 ([[jpa-n-plus-one]], [[jpa-persistence-context-and-entity-lifecycle]]).
- Read DTOs are shaped for the screen (one row per list item with joined columns), not for the domain.
- `@Transactional(readOnly = true)` on the query side lets Hibernate skip dirty checking and lets the routing data source pick a replica.

### Replica-level: routing reads
- Routing options: application (`AbstractRoutingDataSource`, see [[database-scaling-path]]), driver (`targetServerType=preferSecondary` in the PgJDBC URL with several hosts), or a proxy (PgBouncer pairs, pgpool, HAProxy with health checks).
- Consistency caveats:
  - **Read-your-writes**: after `POST /orders` the client calls `GET /orders/{id}` and gets 404 from a lagging replica. Fixes: route reads for N seconds after a write by that user to the primary (sticky by session), or wait for the replica to pass the write's LSN (`pg_last_wal_replay_lsn() >= <lsn>`), or return the created resource in the POST response so no immediate GET is needed.
  - **Monotonic reads**: two consecutive reads hitting two replicas with different lag can show a value disappearing. Pin a session to one replica.
  - Both are instances of [[consistency-models]]; the operational side (lag monitoring, failover) is in [[read-replicas-and-stale-reads]] and [[postgres-replication-and-failover]].

### Model-level: projections
- Write side emits events through the [[outbox-pattern]]; a projector consumes them and maintains a read table (`order_summary`), a Redis hash, or an Elasticsearch document ([[search-and-indexing-services]]).
- Projections are idempotent upserts keyed by aggregate id and event version, so Kafka redelivery is harmless ([[idempotency]]).
- Rebuild is a feature: truncate the projection and replay the topic (or re-scan the source table). Design for it from day one.
- This is where CQRS-lite ends and [[cqrs]] / [[event-sourcing]] begin: once the write model itself is stored as events, replays and projections become the primary mechanism.

### Choosing
- Start at code-level; it costs nothing and removes most ORM-induced slowness.
- Move to replica-level when the primary's CPU or I/O is dominated by `SELECT`s and the read-after-write paths are known and few.
- Move to model-level when the read shape differs a lot (search, feeds, dashboards) or when reads must survive the primary being slow.

### Interview angles
- "How do you avoid stale reads after a write?" Primary for the same user for a few seconds, LSN wait, or return the full resource from the write call.
- "Is this CQRS?" Separate paths yes, separate models optional, event sourcing no. Say so explicitly; interviewers like the distinction.
- "How do you keep the projection in sync?" Outbox → Kafka → idempotent upsert; monitor lag per projector; rebuild by replay.

## Example
An order-history projection fed by Kafka, kept idempotent with a version column:

```sql
CREATE TABLE order_summary (
    order_id     uuid PRIMARY KEY,
    msisdn       text NOT NULL,
    status       text NOT NULL,
    iccid        text,
    updated_at   timestamptz NOT NULL,
    version      bigint NOT NULL          -- event sequence within the aggregate
);
CREATE INDEX order_summary_msisdn_idx ON order_summary (msisdn, updated_at DESC);

-- projector upsert: ignore out-of-order or duplicate events
INSERT INTO order_summary (order_id, msisdn, status, iccid, updated_at, version)
VALUES (:orderId, :msisdn, :status, :iccid, :occurredAt, :version)
ON CONFLICT (order_id) DO UPDATE
SET status = EXCLUDED.status,
    iccid = COALESCE(EXCLUDED.iccid, order_summary.iccid),
    updated_at = EXCLUDED.updated_at,
    version = EXCLUDED.version
WHERE order_summary.version < EXCLUDED.version;
```

The `WHERE version <` guard makes the upsert safe under at-least-once delivery and partition rebalances; the query side reads `order_summary` with one index scan instead of joining orders, profiles and SIMs.

## Pitfalls
- Routing every `GET` to replicas without listing the read-after-write screens; the bug reports say "my order disappeared for a second".
- Replica used for reads inside a write transaction: the read is stale, the write is based on it. Keep reads that feed decisions on the primary ([[transactions-acid-and-isolation]]).
- Projection without a version or event id check: redelivery flips status backwards.
- Forgetting the replica can be promoted on failover; the "read-only" pool must be re-pointed, or routing by role (not by host) must be used.
- Related: [[caching]] for the other read-offload path, [[event-driven-architecture]] for the integration side.
