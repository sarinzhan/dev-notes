---
title: CQRS — separate read and write models
tags: [cqrs, architecture, microservices, postgres]
learned: 2026-10-27
---
## TL;DR
CQRS (command query responsibility segregation) splits a service's API into **commands** that change state through the domain model and **queries** that read from a model shaped for the screen or report. The write side stays small and invariant-focused ([[aggregates-and-invariants]]); the read side can be a denormalised table, a materialised view, Elasticsearch or Redis. The simplest CQRS is two handler classes over one Postgres database; the full form has a separate read store fed by events and is eventually consistent. Most systems need the simple form; the full form is justified only by real read/write asymmetry.

## The problem it solves
- **Without it:** one JPA model serves reads and writes. The write side wants a small aggregate with invariants; the "subscriber page" wants SIM + active profile + tariff + last 5 events in one row. One entity graph for both gives [[jpa-n-plus-one]] on reads and giant aggregates on writes. Reads usually outnumber writes 100:1, yet they cannot be scaled or stored separately.
- **What it changes:** commands go through the aggregate and are the audit surface (who did what); queries cannot change state and read a model shaped for the screen. That model can be cached, replicated and sharded on its own ([[database-scaling-path]]) or live in another store — full-text search in Elasticsearch ([[search-and-indexing-services]]), feeds in Redis, analytics in ClickHouse ([[column-and-wide-column-stores]]) — while the write model stays in Postgres.
- **When you don't need it:** CRUD where the screen shows the entity as it is (level 0). For most other services level 1 (two handlers over one Postgres) is enough; a separate read store pays off only with real read/write asymmetry.

## Key points

### Levels of CQRS
| Level | Write model | Read model | Consistency | When |
|---|---|---|---|---|
| 0: plain service | Entities | Same entities | Immediate | CRUD |
| 1: split handlers | Aggregates via repository | SQL/`JdbcClient` into DTOs, same DB | Immediate | Default for any non-trivial service ([[use-cases-and-application-layer]]) |
| 2: read replica / views | Same | Materialised views, read replica | Replica lag (ms–s) | Heavy reporting, dashboards ([[read-write-splitting-and-cqrs-lite]]) |
| 3: separate read store | Same | Projection tables, Elasticsearch, Redis, updated from events | Eventual (s) | Different query shape or scale (search, feeds) |
| 3 + ES | Event store | Projections rebuilt from events | Eventual | [[event-sourcing]] |
- CQRS does not require event sourcing and event sourcing does not require a separate read store; they are often taught together because event sourcing makes querying the write model impractical.

### Keeping the read side in sync (level 3)
- **Same transaction**: the command handler updates the projection table too. Immediate consistency, simple, but couples the write path to every projection.
- **Domain events in-process** (`@TransactionalEventListener`): projection updated after commit; a crash between them loses the update, so add a rebuild job ([[domain-events-and-integration-events]]).
- **Integration events via outbox + consumer**: the projector is a Kafka consumer, idempotent by event id, can run in another service, can be replayed from offset 0 to rebuild ([[outbox-pattern]], [[idempotency]]).
- **CDC** (Debezium on the write tables) when you cannot change the writer.
- Every projection should be rebuildable from scratch; that is the escape hatch for bugs in the projector.

### Living with eventual consistency
- The UI posts a command, then reads a projection that is not updated yet: the classic "I saved it and it is not there". Options: return the new state from the command response; version the read ("show me at least version N", wait or retry); optimistic UI update; or keep level 1 for that screen.
- Staleness is a product decision: a support dashboard tolerates seconds, a balance check does not ([[consistency-models]]).

### Commands and queries in code
- Command: immutable intent, one handler, returns void or an id; query: immutable parameters, one handler, returns a DTO. A tiny `CommandBus`/`QueryBus` or plain Spring beans; frameworks (Axon) are optional and heavy.
- `@Transactional(readOnly = true)` on query handlers routes to a replica when a routing `DataSource` is configured and lets Hibernate skip dirty checking ([[spring-transactional]]).

## Example
Level 3 projector: a Kafka consumer maintains the `subscriber_overview` read table from SIM events; the query handler reads it with one `SELECT`.

```java
// Projection table: one wide row per subscriber, no joins at query time
// CREATE TABLE subscriber_overview (subscriber_id uuid PRIMARY KEY, msisdn text, active_iccid text,
//   tariff text, last_event text, last_event_at timestamptz, version bigint NOT NULL);

@Component
@RequiredArgsConstructor
public class SubscriberOverviewProjector {
    private final JdbcClient jdbc;
    private final ProcessedEvents processed;

    @KafkaListener(topics = "esim.sim.events", groupId = "subscriber-overview-projector")
    @Transactional
    public void on(ConsumerRecord<String, byte[]> rec, Acknowledgment ack) {
        UUID eventId = UUID.fromString(header(rec, "event-id"));
        if (!processed.insertIfAbsent(eventId)) { ack.acknowledge(); return; }
        if ("ProfileActivated.v1".equals(header(rec, "type"))) {
            var e = codec.decode(rec.value(), ProfileActivatedV1.class);
            jdbc.sql("""
                INSERT INTO subscriber_overview (subscriber_id, msisdn, active_iccid, last_event, last_event_at, version)
                VALUES (:sid, :msisdn, :iccid, 'ProfileActivated', :at, :v)
                ON CONFLICT (subscriber_id) DO UPDATE
                   SET active_iccid = EXCLUDED.active_iccid, last_event = EXCLUDED.last_event,
                       last_event_at = EXCLUDED.last_event_at, version = EXCLUDED.version
                 WHERE subscriber_overview.version < EXCLUDED.version""")   // ignore stale/out-of-order
                .param("sid", e.subscriberId()).param("msisdn", e.msisdn()).param("iccid", e.iccid())
                .param("at", e.occurredAt()).param("v", e.aggregateVersion())
                .update();
        }
        ack.acknowledge();
    }
}

@Service
@RequiredArgsConstructor
public class GetSubscriberOverview {
    private final JdbcClient jdbc;

    @Transactional(readOnly = true)
    public Optional<SubscriberOverview> handle(UUID subscriberId) {
        return jdbc.sql("SELECT * FROM subscriber_overview WHERE subscriber_id = :id")
                .param("id", subscriberId).query(SubscriberOverview.class).optional();
    }
}
```

## Pitfalls
- Full CQRS for a CRUD admin panel: two models, a projector, a Kafka topic and eventual consistency to show a table of tariffs. Level 1 was enough.
- Projections without an aggregate version: out-of-order or replayed events overwrite newer state with older. Keep `version` and compare, as above.
- A read model that is never rebuilt: the first projector bug corrupts it forever. Script the rebuild (truncate + replay) and run it in staging regularly.
- Querying the write model "just this once" from the read API: the aggregate becomes the query API again, and the split is gone.
- Related: [[event-sourcing]], [[event-driven-architecture]], [[read-write-splitting-and-cqrs-lite]], [[design-news-feed]] (fan-out as a projection problem).
