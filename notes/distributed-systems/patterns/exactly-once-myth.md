---
title: The exactly-once myth and effectively-once processing
tags: [distributed-systems, kafka, idempotency, reliability]
learned: 2026-10-27
---
## TL;DR
"Exactly-once delivery" between two independent systems is impossible: the receiver cannot acknowledge and apply a message in one atomic step unless both live in the same transactional system. What every working system does instead is **effectively-once**: deliver at least once with a stable message id, and make the receiver **idempotent** so the second delivery changes nothing. Kafka's exactly-once semantics is a real, useful feature, but scoped to Kafka-to-Kafka pipelines; the moment a consumer writes to Postgres or calls an HTTP API, the guarantee is yours to build with [[outbox-pattern]] on the producer side and [[idempotency]] on the consumer side.

## Key points

### The impossibility, stated once
- Receiver does: (1) apply side effect, (2) acknowledge. Crash between 1 and 2 → redelivery → duplicate. Swap the order → crash between them → loss. Two independent systems, no shared commit, no third option. This is the two-generals problem applied to a single message ([[failure-modes-and-timeouts]], [[delivery-guarantees]]).
- The only escape is to put the side effect and the acknowledgement in **one** atomic commit: Kafka transactions do that when both are Kafka topics; a database does it when both the business row and the "processed" marker are rows in the same database.

### What Kafka EOS gives and does not
| Layer | Mechanism | Removes duplicates from | Does not cover |
|---|---|---|---|
| Idempotent producer (`enable.idempotence=true`, default) | producer id + sequence per partition; broker drops repeats | network retries of one producer session | app calling `send()` twice; producer restart (new pid) |
| Transactions (`transactional.id`, `read_committed` consumers) | offsets of input and records to output committed atomically | the consume-transform-produce loop | any side effect outside Kafka; consumers with `read_uncommitted` |
| Kafka Streams `exactly_once_v2` | the above packaged with state stores | stream processing topologies | external sinks (Connect sinks must be idempotent/upsert) |

- Even inside Kafka, a transactional producer that restarts with the same `transactional.id` fences the old instance (epoch bump): that is a fencing token, the same idea as in [[distributed-locks-and-fencing]].
- Costs: transaction coordinator round trips, `transaction.timeout.ms`, higher end-to-end latency (consumers only see committed data), and one `transactional.id` per input partition set for proper fencing ([[kafka-producer]]).

### Effectively-once, end to end
1. **Stable identity**: every message carries an id that survives retries: outbox row id, order id + step name, or a deterministic hash of the business key. Kafka offsets are not enough because reprocessing from a different consumer group or after a partition change yields a different offset.
2. **Producer**: write the event in the same transaction as the change ([[outbox-pattern]]). At-least-once, no phantoms.
3. **Consumer**: in one local transaction, insert the id into a dedup table (or upsert the target row keyed by it), apply the effect, commit. Then ack. Duplicates hit the unique key and do nothing ([[idempotency]]).
4. **External effects** you cannot wrap in your transaction (payment gateway, SMS, SM-DP+ profile order): use the provider's idempotency key, or make the call naturally idempotent (`PUT state=ACTIVE`, not `POST activate`), and record the provider's response id before acking ([[esim-provisioning-platform]]).
5. **Ordering per key**: keep all events for one key on one partition and process them in order; dedup does not protect against out-of-order application of two different events ([[kafka-architecture]]).

### Where duplicates come from (so you can list them in an interview)
- Producer retries (handled by idempotent producer), outbox relay re-run after a crash, consumer rebalance mid-batch, redelivery after `max.poll.interval.ms`, DLQ replay, replaying a topic to rebuild a projection, a human re-sending a webhook, an upstream partner retrying an HTTP call ([[dead-letter-queues-and-retries]]).
- Any of these alone justifies idempotent consumers; together they make it non-negotiable.

### Natural idempotency vs dedup table
- **Natural**: set-based updates (`status = 'ACTIVE'`), upserts keyed by the business id, "insert if absent". Cheapest, no extra table, but incremental operations (`balance = balance - 10`) are not naturally idempotent.
- **Dedup table**: `processed_events(event_id, consumer, processed_at)` with a unique key, in the same transaction as the effect. Works for anything, needs retention and an index ([[postgres-indexes]]).
- **Version/sequence check**: apply only if `event.version > row.version`; also rejects out-of-order stale events, which a dedup table does not.

## Example
A Kafka Streams pipeline with real exactly-once inside Kafka, and the honest boundary where it ends: the sink to Postgres uses an upsert keyed by the event id.
```yaml
# streams app: input topic -> enrich -> output topic, exactly-once within Kafka
spring:
  kafka:
    streams:
      application-id: activation-enricher
      properties:
        processing.guarantee: exactly_once_v2     # transactional producer + read_committed consumer
        commit.interval.ms: 100
    consumer:
      isolation-level: read_committed            # downstream consumers must opt in too
```
```sql
-- sink consumer (plain @KafkaListener + JDBC): the boundary where EOS stops being Kafka's job
INSERT INTO activation_projection (event_id, iccid, status, updated_at)
VALUES (:eventId, :iccid, :status, now())
ON CONFLICT (event_id) DO NOTHING;             -- duplicate delivery -> no change
```
The Streams stage never duplicates or loses records between the two topics even across restarts; the sink turns at-least-once delivery into a correct projection with one upsert.

## Pitfalls
- Reading "exactly-once" in a broker's marketing page and skipping consumer idempotency; the first rebalance during a deploy produces the duplicate.
- Dedup keyed on Kafka `topic-partition-offset`: breaks on replay from a new group, topic migration, or partition count change.
- Dedup check and side effect in **different** transactions (check in Redis, write in Postgres): a crash between them re-opens the window.
- Making the producer transactional and leaving consumers at `read_uncommitted` (the default), so they see aborted records anyway.
- Related: [[delivery-guarantees]] for the taxonomy, [[outbox-pattern]] and [[idempotency]] for the two halves of the solution, [[saga]] for multi-step flows where every step needs this.
