---
title: Spring Kafka consumer basics
tags: [kafka, messaging, spring-boot, reliability]
learned: 2026-10-08
---
## TL;DR
`@KafkaListener` runs on a container with N consumer threads (`concurrency`), each owning some partitions of the group. Offsets are committed by the container after the record (or batch) is processed, so a crash mid-processing means **at-least-once** delivery and the handler must be [[idempotency|idempotent]]. Error handling is configured once in `DefaultErrorHandler` (retries with backoff, then dead letter), not in try/catch inside the listener.

## Key points

### Listener, concurrency, partitions
- One `@KafkaListener` creates a `ConcurrentMessageListenerContainer` with `concurrency` child containers, each a `KafkaConsumer` on its own thread. A partition is owned by exactly one consumer in the group, so `concurrency` > partitions leaves threads idle; partitions set the parallelism ceiling ([[kafka-architecture]]).
- Ordering is guaranteed only within a partition. Key messages by subscriber/aggregate id so related events stay ordered ([[kafka-producer]]).
- Same `groupId` → work is split; different `groupId` → each group gets every record.

```anim
kafka-consumer-group
```

### Acknowledgement modes
| `AckMode` | Commit when |
|---|---|
| `BATCH` (default) | after all records from one `poll()` are processed |
| `RECORD` | after each record |
| `TIME` / `COUNT` / `COUNT_TIME` | periodically |
| `MANUAL` | `ack.acknowledge()` called, committed at the end of the poll loop |
| `MANUAL_IMMEDIATE` | `ack.acknowledge()` called, committed right away |
- `MANUAL_IMMEDIATE` when you must commit only after a DB write succeeded. Even then, "DB committed, offset commit failed" re-delivers the record: this is why dedup lives in the DB ([[idempotency]]), not in the ack mode.
- `enable.auto.commit` is set to `false` by Spring; the container owns commits.

### Rebalance and the poll loop
- `max.poll.interval.ms` (5 min) is the time allowed between two `poll()` calls; `max.poll.records` (500) is the batch size per poll. If processing 500 records takes longer than 5 min, the broker evicts the consumer, rebalances, and another consumer re-processes the same batch, which also takes too long: a **rebalance storm** with duplicates and zero progress.
- Fix: lower `max.poll.records`, raise `max.poll.interval.ms`, or make processing faster (batch DB writes, async I/O). Watch `kafka.consumer.fetch.manager.records.lag` and rebalance counts.
- Cooperative sticky assignor (`CooperativeStickyAssignor`) avoids stop-the-world revocation; `group.instance.id` (static membership) prevents a rebalance on a quick restart.

### Error handling
- `DefaultErrorHandler(recoverer, backOff)` (Spring Kafka 2.8+): on exception, seek back and retry the record per the `BackOff` (`FixedBackOff`, `ExponentialBackOffWithMaxRetries`), then call the recoverer. Retries block the partition: use short backoffs, or non-blocking `@RetryableTopic` for long ones.
- `DeadLetterPublishingRecoverer` publishes the failed record to `<topic>.DLT` with exception headers; alert on DLT lag and build a replay tool.
- `addNotRetryableExceptions(DeserializationException.class, ValidationException.class, ...)` skips retries for errors that will never succeed; `DefaultErrorHandler` already classifies `DeserializationException`, `MessageConversionException`, `MethodArgumentResolutionException`, `ClassCastException` as fatal.
- Deserialization errors happen in `poll()`, before the listener. Without `ErrorHandlingDeserializer` the consumer loops forever on the poison record. With it, the record reaches the error handler with the exception in a header and goes to the DLT.
- Batch listeners (`@KafkaListener(batch = "true")`, `List<ConsumerRecord<K,V>>`): the error handler re-delivers the whole batch unless you throw `BatchListenerFailedException(msg, index)` to say which record failed.

### Transactions
- `@Transactional` on the listener method makes the DB write atomic, but the offset commit is a separate system: still at-least-once ([[spring-transactional]]).
- Kafka transactions (`transaction-id-prefix`, `KafkaTransactionManager`) give exactly-once only for consume → produce **within Kafka**; a DB write inside is not covered. `ChainedKafkaTransactionManager` is deprecated; use the [[outbox-pattern]] for DB + Kafka.

### Testing
- `@EmbeddedKafka` starts an in-JVM broker: fast, fine for listener logic. [[testcontainers]] `KafkaContainer` (or `ConfluentKafkaContainer`) runs the real broker image and catches serialization and config issues; prefer it for integration tests. Use `@DynamicPropertySource` to inject `bootstrap-servers`.

## Example
```java
@Configuration
public class KafkaConsumerConfig {

    @Bean
    public DefaultErrorHandler errorHandler(KafkaTemplate<Object, Object> template) {
        var recoverer = new DeadLetterPublishingRecoverer(template,
            (rec, ex) -> new TopicPartition(rec.topic() + ".DLT", rec.partition()));
        var backOff = new ExponentialBackOffWithMaxRetries(3);
        backOff.setInitialInterval(500);
        backOff.setMultiplier(2.0);
        var handler = new DefaultErrorHandler(recoverer, backOff);
        handler.addNotRetryableExceptions(ActivationValidationException.class);
        return handler;
    }

    @Bean
    public ConcurrentKafkaListenerContainerFactory<String, ActivationRequested> factory(
            ConsumerFactory<String, ActivationRequested> cf, DefaultErrorHandler handler) {
        var f = new ConcurrentKafkaListenerContainerFactory<String, ActivationRequested>();
        f.setConsumerFactory(cf);
        f.setCommonErrorHandler(handler);
        f.getContainerProperties().setAckMode(ContainerProperties.AckMode.MANUAL_IMMEDIATE);
        return f;
    }
}

@Component
@RequiredArgsConstructor
public class ActivationListener {
    private final ProvisioningService provisioning;

    @KafkaListener(topics = "esim.activation", groupId = "provisioning", concurrency = "3")
    public void onActivation(ActivationRequested evt, Acknowledgment ack) {
        provisioning.activate(evt);   // @Transactional: processed_events insert + business write
        ack.acknowledge();
    }
}
```
```yaml
spring:
  kafka:
    consumer:
      key-deserializer: org.springframework.kafka.support.serializer.ErrorHandlingDeserializer
      value-deserializer: org.springframework.kafka.support.serializer.ErrorHandlingDeserializer
      properties:
        spring.deserializer.value.delegate.class: org.springframework.kafka.support.serializer.JsonDeserializer
        spring.json.trusted.packages: kg.beeline.esim.events
        max.poll.records: 100
```

## Pitfalls
- A listener annotated with [[spring-transactional|@Transactional]] does **not** make the Kafka commit transactional with the DB. The DB commits, then the offset commit may fail: duplicate.
- Catching all exceptions inside the listener hides failures from `DefaultErrorHandler`: no retries, no DLT, silent data loss.
- Blocking retries with long backoff on a partition stall every record behind the failing one; use `@RetryableTopic` for retry-later semantics.
- `spring.json.trusted.packages` not set: `JsonDeserializer` rejects the type header and every record is a deserialization error.
- Related: [[kafka-consumer]] for the raw client, [[distributed-tracing]] for propagating trace ids through headers, [[esim-provisioning-platform]] for the real topic layout.
