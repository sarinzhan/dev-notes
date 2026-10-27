---
title: Async processing and event-driven scaling
tags: [system-design, scaling, kafka, async, reliability]
learned: 2026-10-27
---
## TL;DR
Anything the user does not need to wait for should leave the request path: accept the command, persist it, return `202`, and let workers consume it from a queue or log. This decouples the peak of incoming traffic from the throughput of slow dependencies (SMS providers, HLR, billing), lets each stage scale on its own, and turns outages into backlog instead of errors. The cost is a new set of guarantees to engineer: at-least-once delivery, so consumers must be idempotent; ordering per key; a dead-letter path for poison messages; and visibility of lag. Queue vs log choice is in [[message-queues-and-streams]]; this note is about using them to scale.

## Key points

### Why async scales where sync does not
- A synchronous chain's latency and availability multiply: three dependencies at 99.9% give 99.7%, and a slow one holds a thread in every upstream service ([[service-communication-sync-vs-async]]).
- With a queue in between, the API's capacity is "how fast can I write a row and publish", which is thousands per second on one node. The consumer side runs at the dependency's rate; the gap becomes **lag**, which is measurable and bounded by retention.
- Each stage becomes a separate consumer group with its own replica count, thread pool, timeouts and [[circuit-breaker]]; a slow email provider no longer slows push.

### Shape of an async pipeline
```
client ──POST──▶ API ──tx: row + outbox──▶ Postgres ──relay──▶ topic (key = aggregate id)
                                                                     │
                                            consumer group (N ≤ partitions) ──▶ dependency
                                                                     │ fail permanently
                                                                     └─▶ DLQ topic + alert
```
- The [[outbox-pattern]] makes "accepted" and "published" atomic; without it the API either loses commands or publishes phantom ones.
- Partition key = the entity whose order matters (user, order, SIM). Parallelism = partitions; consumer replicas beyond that idle ([[kafka-architecture]], [[kafka-consumer]]).

### Idempotent consumers
- Delivery is at-least-once: rebalances, crashes after the side effect but before the offset commit, relay re-runs. The consumer deduplicates by event id in the same transaction as its effect, or makes the effect naturally idempotent ([[idempotency]], [[exactly-once-myth]]).
- External calls carry the same idempotency key (`orderId:step`) so the provider also deduplicates ([[esim-provisioning-platform]]).

### Retries and dead letters
- Transient errors (timeouts, 5xx, 429): retry with exponential backoff and jitter. Blocking retry inside the consumer holds the partition; non-blocking retry uses retry topics with growing delays ([[dead-letter-queues-and-retries]]).
- Permanent errors (validation, 4xx, unknown subscriber): no retry, straight to the DLQ with the error reason in headers, status `FAILED` on the row, alert on DLQ rate.
- A DLQ needs a consumer: a replay tool that re-publishes selected messages after the bug is fixed.

### Backpressure and scaling signals
- Consumer lag (`records-lag-max`, Burrow, Kafka exporter) is the autoscaling metric for workers: KEDA scales a deployment on lag per partition ([[kubernetes-basics]]).
- Bounded in-process queues and `max.poll.records` keep one worker from pulling more than it can finish within `max.poll.interval.ms`; otherwise it gets kicked from the group and the rebalance storm begins ([[backpressure-and-flow-control]]).
- Rate-limit the dependency side (token bucket per provider) rather than letting retries amplify an outage ([[rate-limiting]]).

### Request/reply over async
- The client needs an answer eventually: return `202` with an id and a `GET /status` endpoint, push a webhook, or stream progress over SSE/WebSocket ([[websockets-sse-and-long-polling]], [[spring-reactive-webclient-sse]]).
- For multi-step flows with compensation, a workflow engine replaces hand-written consumer chains ([[temporal-concepts]], [[workflow-engines-vs-sagas]], [[saga]]).

### When not to go async
- The user must see the result in the same request (payment authorisation, login). Then make the sync path fast and fail fast; put only the follow-ups (receipt, analytics, notification) on the queue.
- Tiny systems: a queue adds a broker, a relay, retention and monitoring. A `@Scheduled` poller over a table with `SKIP LOCKED` is a valid first queue ([[background-jobs-and-schedulers]]).

## Example
Spring Kafka consumer with non-blocking retries and a DLQ, deduplicating by event id in the same transaction as the business effect:

```java
@Component
@RequiredArgsConstructor
public class SmsDispatchConsumer {
    private final SmsProvider provider;
    private final ProcessedEventRepository processed;
    private final NotificationRepository notifications;

    @RetryableTopic(
        attempts = "4",
        backoff = @Backoff(delay = 2_000, multiplier = 3),   // 2s, 6s, 18s
        exclude = { InvalidRecipientException.class },        // permanent → DLQ at once
        dltTopicSuffix = ".dlq")
    @KafkaListener(topics = "notifications.sms", groupId = "sms-dispatch", concurrency = "6")
    @Transactional
    public void onMessage(@Header("event-id") String eventId, SmsRequested msg) {
        if (processed.existsById(eventId)) return;             // duplicate delivery
        provider.send(msg.msisdn(), msg.text(), eventId);      // provider dedupes by the same key
        notifications.markSent(msg.notificationId(), Channel.SMS);
        processed.save(new ProcessedEvent(eventId, Instant.now()));
    }

    @DltHandler
    public void dead(SmsRequested msg, @Header(KafkaHeaders.EXCEPTION_MESSAGE) String reason) {
        notifications.markFailed(msg.notificationId(), Channel.SMS, reason);
    }
}
```

Six listener threads match six partitions; adding a seventh pod changes nothing until the topic is repartitioned.

## Pitfalls
- `202 Accepted` with no status endpoint or callback; the client polls nothing and support asks "did it send?".
- Infinite blocking retries on a permanent error stall the partition for every other user behind it.
- Consumers that call a dependency without a timeout exceed `max.poll.interval.ms`, trigger a rebalance, and redeliver everything in flight.
- Dedup table without a purge job grows forever; key it with a TTL or partition it by day ([[postgres-partitioning]]).
- Related: [[event-driven-architecture]] for the design side, [[spring-kafka-consumer]] and [[testing-async-and-kafka-flows]] for implementation and tests.
