---
title: Delivery guarantees, at-most-once to exactly-once
tags: [distributed-systems, messaging, kafka, reliability]
learned: 2026-10-27
---
## TL;DR
A message between two processes over an unreliable network can be delivered **at most once** (send and forget: may be lost, never duplicated), **at least once** (retry until acknowledged: never lost, may be duplicated), or, in a very narrow sense, **exactly once** inside a single system that controls both sides. Across system boundaries exactly-once delivery is impossible; what you can build is **effectively-once processing**: at-least-once delivery plus an idempotent consumer. Everything in Kafka's "exactly-once semantics" is a special case of that, scoped to Kafka-to-Kafka pipelines.

## Key points

### The three guarantees
| Guarantee | Mechanism | Loses | Duplicates | Who wants it |
|---|---|---|---|---|
| **At-most-once** | fire and forget; ack before processing | yes | no | metrics, logs, presence pings, anything where a newer value replaces the old |
| **At-least-once** | ack only after durable processing; retry on timeout | no | yes | almost all business events; the default for Kafka, RabbitMQ, SQS, HTTP retries |
| **Exactly-once** | at-least-once + dedup, or an atomic commit spanning input offset and output | no | no (as observed) | payments, provisioning, inventory |

- The choice is made by **where the ack happens** relative to the side effect, not by a flag on the broker.
- Kafka consumer: with `enable.auto.commit=true` the offset is committed on the next `poll()`, regardless of whether the previous batch was fully processed; a crash in between can lose records (processed late) or duplicate them (committed late). Commit manually after the side effect for a clean at-least-once ([[kafka-consumer]], [[spring-kafka-consumer]]).

### Why true exactly-once across boundaries is impossible
- The consumer does its side effect (HTTP call, DB write, email) and then acknowledges. If it crashes between the two, the message is redelivered and the effect happens twice. If it acknowledges first, a crash loses the message. There is no third ordering, because the side effect and the ack are in different systems with no shared transaction ([[failure-modes-and-timeouts]]).
- 2PC between broker and database technically closes the gap but blocks on coordinator failure and is unsupported by Kafka and by most cloud services ([[cross-shard-queries-and-transactions]]).

### What Kafka's "exactly-once" actually covers
- **Idempotent producer** (`enable.idempotence=true`, default since Kafka 3.0): a producer id plus per-partition sequence numbers let the broker drop duplicates caused by *producer retries*. It does not cover an application that calls `send()` twice, or a producer that restarts with a new pid.
- **Transactions** (`transactional.id`, `isolation.level=read_committed`): a consume-transform-produce loop commits the input offsets and the output records atomically. Exactly-once holds *within Kafka*: input topic → output topic. The moment the transform writes to Postgres or calls an API, you are back to at-least-once for that side effect.
- Kafka Streams `processing.guarantee=exactly_once_v2` is this machinery packaged.

### Effectively-once: the pattern that works everywhere
1. Producer side: write the event in the same DB transaction as the business change, publish asynchronously ([[outbox-pattern]]). Delivery is at-least-once with a stable event id.
2. Consumer side: dedupe on that id **inside the same transaction** as the side effect: insert into `processed_events(event_id PRIMARY KEY)`, do the work, commit; a duplicate hits the unique constraint and is skipped ([[idempotency]]).
3. For external side effects that cannot be in your transaction (an SMS gateway), use the provider's idempotency key, or make the operation naturally idempotent (set status to X rather than increment).

### Ordering is a separate axis
- At-least-once with retries reorders: message 3 fails, 4 succeeds, 3 is retried after 4. Kafka keeps order within a partition only if the producer has `max.in.flight.requests.per.connection ≤ 5` with idempotence on, and if the consumer does not retry out of band ([[kafka-producer]], [[dead-letter-queues-and-retries]]).
- Consumers that need both no-loss and in-order processing per key must block the partition on failure or park the key, not skip ahead.

### Interview framing
- "Does Kafka give exactly-once?" Yes within a Kafka-to-Kafka transaction; no for your database or HTTP side effects. Then explain outbox + idempotent consumer ([[exactly-once-myth]]).
- "At-least-once or at-most-once for X?" Decide by asking which is cheaper to fix: a lost message you never notice, or a duplicate you can dedupe. Almost always the second.

## Example
A consumer that turns at-least-once delivery into effectively-once processing: dedup row and business write in one Postgres transaction, offset committed only after that commits.
```java
@Component
@RequiredArgsConstructor
public class ProfileDownloadedListener {
    private final JdbcTemplate jdbc;

    @KafkaListener(topics = "esim.events", groupId = "billing")
    @Transactional                                   // DB transaction; commit precedes the Kafka ack
    public void on(ConsumerRecord<String, String> rec, Acknowledgment ack) {
        UUID eventId = UUID.fromString(new String(rec.headers().lastHeader("event-id").value(), UTF_8));
        int inserted = jdbc.update(
            "INSERT INTO processed_events(event_id, consumer) VALUES (?, 'billing') ON CONFLICT DO NOTHING",
            eventId);
        if (inserted == 1) {                          // first time we see it
            jdbc.update("UPDATE subscription SET status = 'ACTIVE', activated_at = now() WHERE iccid = ?",
                rec.key());
        }                                             // duplicate: skip work, still ack
        ack.acknowledge();                            // manual ack, AckMode.MANUAL
    }
}
```
If the pod dies after the DB commit but before `acknowledge()`, the record is redelivered, the insert conflicts, and nothing happens twice. The `processed_events` table needs a retention job; a week is usually enough ([[postgres-indexes]] for the partial-index trick on queue-like tables).

## Pitfalls
- Auto-commit plus asynchronous processing (handing records to an executor): offsets advance before work finishes, which is at-most-once in disguise.
- Deduping in an in-memory set or a cache with TTL: a restart or eviction reopens the window; dedup state must be as durable as the side effect.
- `enable.idempotence=true` read as "no duplicates ever": it only covers broker-side retries of one producer session.
- Putting the external HTTP call before the DB transaction so a rollback leaves the external effect in place; order side effects from most to least reversible.
- Related: [[exactly-once-myth]] for the end-to-end argument, [[outbox-pattern]] for the producer half, [[message-queues-and-streams]] for how RabbitMQ/SQS acks compare.
