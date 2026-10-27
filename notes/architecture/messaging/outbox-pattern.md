---
title: Transactional outbox pattern
tags: [messaging, kafka, reliability, postgres]
learned: 2026-08-20
---
## TL;DR
Write the domain change and the event to publish in the **same database transaction** (an `outbox` table). A separate relay reads the outbox and publishes to Kafka. This removes the dual-write problem: the event exists if and only if the business change was committed. Delivery becomes **at-least-once**, so consumers must be [[idempotency|idempotent]].

## Key points

### The dual-write problem, concretely
- Naive code: `orders.save(order); kafka.send(event);` inside one method. Two failure modes:
  - DB commits, Kafka send fails (broker down, timeout, pod killed between the two calls): the order exists but nobody downstream ever hears about it. Silent data loss.
  - Kafka send succeeds, DB commit fails (constraint violation, deadlock, connection lost at commit): consumers react to an order that does not exist. Phantom event.
- Moving `send()` inside the `@Transactional` method does not help: the send is not part of the DB transaction, it just happens before commit, so case 2 is still possible, and now the DB connection is held during a network call ([[hikaricp-pool-sizing]]).
- Interview: "why not 2PC/XA?" Kafka has no XA resource manager; even where it exists (JMS + JDBC), 2PC is slow, blocks on coordinator failure, and most cloud brokers do not support it. The outbox turns the problem into a single local transaction plus asynchronous delivery.

### Relay: polling publisher vs CDC (Debezium)
| | Polling publisher | Debezium CDC |
|---|---|---|
| Mechanism | `@Scheduled` job selects unpublished rows, sends, marks as published | Reads the Postgres WAL via logical replication slot, Outbox Event Router SMT turns rows into Kafka records |
| Latency | poll interval (100 ms – 1 s) | near real-time |
| DB load | repeated queries, needs a partial index | none on the table, but a replication slot that must be consumed or WAL grows |
| Ops | just app code | Kafka Connect cluster, connector config, slot monitoring |
| Scaling | `FOR UPDATE SKIP LOCKED` to run several instances | one connector per database, parallelism via Connect tasks |
- With Debezium the row can be deleted right after insert in the same transaction: the WAL still contains the insert, so the table stays empty by design.

### Multiple relay instances
- `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 100` lets N instances pull disjoint batches without blocking each other. Rows locked by a crashed instance are released when its transaction ends.
- Keep one relay transaction per batch: select, send with `get()`/callback wait, update `published_at`, commit. If the pod dies after sending but before the update, the batch is re-sent: that is the at-least-once guarantee, not a bug.

### Ordering
- Order is only meaningful per aggregate. Use `aggregate_id` as the Kafka key so all events of one subscriber land on one partition ([[kafka-producer]]).
- Select by `created_at, id` and send sequentially per key; a parallel relay must not reorder rows of the same aggregate (partition batches by key, or run one instance per table).

### Exactly-once is an illusion here
- The relay gives at-least-once. The Kafka idempotent producer only removes duplicates from producer retries, not from relay re-runs. End-to-end "exactly-once" is really at-least-once delivery plus an idempotent consumer keyed by the outbox `id` ([[idempotency]]).

### Retention
- Mark rows as published, do not delete them one by one. Purge in batches (`DELETE ... WHERE published_at < now() - interval '7 days' LIMIT ...` via a CTE) to avoid long locks and bloat. A partial index on `published_at IS NULL` keeps the hot set small ([[postgres-indexes]]).

## Example
```java
@Component
@RequiredArgsConstructor
public class OutboxRelay {
    private final JdbcTemplate jdbc;
    private final KafkaTemplate<String, String> kafka;

    @Scheduled(fixedDelay = 500)
    @Transactional
    public void relay() {
        List<OutboxRow> rows = jdbc.query("""
            SELECT id, aggregate_id, type, payload::text
            FROM outbox WHERE published_at IS NULL
            ORDER BY created_at, id
            FOR UPDATE SKIP LOCKED LIMIT 100
            """, (rs, i) -> new OutboxRow(rs.getObject("id", UUID.class),
                 rs.getString("aggregate_id"), rs.getString("type"), rs.getString("payload")));
        for (OutboxRow row : rows) {
            var record = new ProducerRecord<>("esim.events", row.aggregateId(), row.payload());
            record.headers().add("event-id", row.id().toString().getBytes(UTF_8));
            record.headers().add("type", row.type().getBytes(UTF_8));
            kafka.send(record).get(5, TimeUnit.SECONDS);   // block: we need the ack before marking
            jdbc.update("UPDATE outbox SET published_at = now() WHERE id = ?", row.id());
        }
    }
}
```
The business service stays trivial: `@Transactional place(cmd)` saves the aggregate and inserts the outbox row through the same `EntityManager`/`JdbcTemplate`, so both commit or neither does ([[spring-transactional]]).

## Pitfalls
- Writing the outbox row outside the transaction boundary (for example in a `@TransactionalEventListener(AFTER_COMMIT)`) reintroduces the dual write.
- `kafka.send()` without waiting for the ack and then marking the row published turns at-least-once into at-most-once.
- Polling with `ORDER BY created_at` and no `SKIP LOCKED`: a second instance blocks on the first instance's rows, and all parallelism is lost.
- Debezium slot not consumed (connector down) keeps WAL on disk until the volume fills. Alert on `pg_replication_slots` lag.
- JSON payload schema evolves; put a `version` field in from day one.
- Related: [[saga]] uses outbox for every step, [[kafka-architecture]] for partition semantics, [[esim-provisioning-platform]] for a real deployment.
