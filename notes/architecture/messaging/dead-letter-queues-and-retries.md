---
title: Dead letter queues, retry topics and poison messages
tags: [messaging, kafka, spring-kafka, reliability]
learned: 2026-10-27
---
## TL;DR
A consumer that fails on a message has three choices: retry in place (blocks the partition), retry later through a **retry topic** with backoff (non-blocking), or give up and park the message in a **dead letter queue/topic (DLQ/DLT)** with the error attached. Transient errors (dependency down, lock timeout) deserve bounded retries with exponential backoff; permanent errors (bad payload, business rule violation) go straight to the DLT. A DLT without an alert and a replay tool is a black hole; a retry without a cap is an infinite loop on a **poison message**.

## The problem it solves
- **Without it:** a consumer of `esim.activation.requested` meets a record it cannot process: a malformed payload, or an SM-DP+ that answers 503 for ten minutes. With in-place retries and no cap, the poison message stalls the partition and every activation behind it waits. If the code gives up instead, the record is skipped and the activation request is lost without anyone noticing.
- **What it changes:** transient errors get a bounded number of delayed retries while the partition keeps moving; whatever still fails lands in a DLT with the error and its original coordinates, triggers an alert, and can be replayed after the fix.
- **When you don't need it:** the messages are cheap to lose or will arrive again anyway (periodic status snapshots, metrics), or on failure you can simply stop the consumer, because order matters more than progress and an operator watches the flow.

## Key points

### Classify the failure first
| Error | Example | Action |
|---|---|---|
| Transient | HTTP 503 from SM-DP+, `SQLTransientConnectionException`, Kafka `RetriableException` | Retry with backoff, then DLT |
| Permanent, technical | Deserialization failure, schema mismatch, `NullPointerException` in the handler | DLT immediately; fix code, replay |
| Permanent, business | "profile already released", validation failed | Usually not a DLT case: record the outcome, ack, maybe emit a `...Rejected` event |
| Ordering-dependent | `ProfileActivated` arrived before `SimRegistered` | Short retry (the predecessor is probably in flight), then DLT |
- `DefaultErrorHandler.addNotRetryableExceptions(...)` and Resilience4j's `retryExceptions`/`ignoreExceptions` are where this classification lives in code ([[circuit-breaker]]).

### Blocking retry vs retry topics
- **Blocking (in-place)**: Spring Kafka `DefaultErrorHandler` with a `BackOff` seeks back and re-polls the same record. Simple, preserves order, but every record behind it on that partition waits; a 3 × 10 s backoff stalls the partition for 30 s ([[spring-kafka-consumer]]).
- **Non-blocking (retry topics)**: on failure the record is published to `orders-retry-1000` (consumed after a 1 s delay), then `orders-retry-10000`, then `orders-dlt`. The main partition moves on. Order across retried and non-retried records is lost, so the consumer must tolerate it ([[idempotency]]). Spring Kafka `@RetryableTopic` creates the topics and the delayed consumers for you.
- RabbitMQ: a queue with `x-dead-letter-exchange` and per-queue `x-message-ttl` gives the same delayed-retry ladder; a `nack(requeue=false)` sends to the DLX ([[rabbitmq-basics]]). ActiveMQ has redelivery policy + `ActiveMQ.DLQ` ([[activemq-basics]]).

### What a dead letter record must carry
- Original topic, partition, offset and timestamp; exception class, message and stack trace (truncated); consumer group; retry count; the original headers (`traceparent`, `event-id`). Spring's `DeadLetterPublishingRecoverer` adds `kafka_dlt-*` headers with exactly this.
- Keep the original key so a replay lands on the same partition and keeps per-aggregate order.
- Retention on the DLT is longer than on the main topic (weeks), because replay happens after the fix.

### Operating the DLT
- **Alert** on DLT message rate and on consumer lag of the retry topics ([[slo-sli-and-alerting]]); a single DLT record for a payment flow is a page, a hundred for analytics is a ticket.
- **Replay tool**: a small admin endpoint or CLI that reads a DLT range and republishes to the original topic (same key, `replayed-from` header), optionally after a fix is deployed. Replaying into a consumer that is still broken just fills the DLT again, so replay one record first.
- **Poison message**: a record that crashes the consumer every time (malformed JSON, giant payload). Without `ErrorHandlingDeserializer` it fails inside `poll()` and the listener never sees it, so the consumer loops forever at the same offset. Deserialization errors are the first thing to route to the DLT.
- **Redelivery and idempotency**: a retried message may already have been half-processed; the handler must be idempotent by event id in the same transaction as its effect.

### Backoff numbers
- Exponential with jitter, capped: 1 s, 2 s, 4 s ... up to 1–5 minutes; total attempts 3–6 for user-facing flows, more (hours) for batch integration where the partner has planned downtime.
- Retrying faster than the dependency's recovery time only produces load; match the ladder to the [[circuit-breaker]] open duration.

## Example
Non-blocking retries with `@RetryableTopic` for the activation consumer: three delayed retries on transient errors, straight to the DLT on validation errors, and a DLT listener that records the failure for the replay tool.

```yaml
spring:
  kafka:
    consumer:
      group-id: provisioning
      value-deserializer: org.springframework.kafka.support.serializer.ErrorHandlingDeserializer
      properties:
        spring.deserializer.value.delegate.class: org.springframework.kafka.support.serializer.JsonDeserializer
        spring.json.trusted.packages: kg.beeline.esim.events
    listener:
      ack-mode: record
```

```java
@Component
@RequiredArgsConstructor
public class ActivationConsumer {
    private final ActivateProfileHandler activate;
    private final DeadLetterRepository deadLetters;

    @RetryableTopic(
            attempts = "4",                                        // 1 main + 3 retries
            backoff = @Backoff(delay = 2_000, multiplier = 3.0, maxDelay = 60_000, random = true),
            topicSuffixingStrategy = TopicSuffixingStrategy.SUFFIX_WITH_INDEX_VALUE,   // -retry-0, -retry-1, -retry-2
            dltStrategy = DltStrategy.FAIL_ON_ERROR,
            exclude = { ActivationValidationException.class, DeserializationException.class },  // straight to DLT
            autoCreateTopics = "false")
    @KafkaListener(topics = "esim.activation.requested", groupId = "provisioning")
    public void on(ActivationRequestedV1 evt,
                   @Header(KafkaHeaders.RECEIVED_TOPIC) String topic,
                   @Header(name = KafkaHeaders.DELIVERY_ATTEMPT, required = false) Integer attempt) {
        activate.handle(new ActivateProfile(evt.simId(), new Iccid(evt.iccid()), "kafka"));   // idempotent by evt.id()
    }

    @DltHandler
    public void dead(ActivationRequestedV1 evt,
                     @Header(KafkaHeaders.ORIGINAL_TOPIC) String origin,
                     @Header(KafkaHeaders.EXCEPTION_MESSAGE) String error,
                     @Header(KafkaHeaders.ORIGINAL_OFFSET) long offset) {
        deadLetters.save(DeadLetter.of(origin, offset, evt.id(), error));   // feeds the replay UI and the alert
    }
}
```

Replay: `kafka-console-consumer` or an admin endpoint reads `esim.activation.requested-dlt`, strips the `kafka_dlt-*` headers, and republishes to `esim.activation.requested` with the same key.

## Pitfalls
- `attempts` left at the default of 3 with a 1 s fixed delay: the dependency is still down three seconds later and everything lands in the DLT during every short outage.
- Retry topics with `autoCreateTopics` in production: topics appear with the broker default of one partition and replication factor 1; create them explicitly ([[kafka-architecture]]).
- Catching `Exception` inside the listener and logging it: no retry, no DLT, the offset commits, the message is gone.
- Replaying the whole DLT after a fix without checking for duplicates: the replayed records interleave with new traffic; the consumer's idempotency key saves you only if it exists.
- A DLT nobody reads: dashboard the message count by topic and age of the oldest record, not just the rate ([[grafana-dashboards-and-red-use]]).
- Related: [[spring-kafka-consumer]], [[idempotency]], [[kafka-consumer]] (lag and offsets), [[event-driven-architecture]], [[outbox-pattern]].
