---
title: Spring Kafka consumer basics
tags: [kafka, messaging, spring-boot]
learned: 2026-10-08
---
## TL;DR
`@KafkaListener` runs on a container with N consumer threads (`concurrency`), at most one per partition. Offsets are committed by the container after the record (or batch) is processed, so a crash mid-processing means **at-least-once** delivery.

## Key points
- `concurrency` > number of partitions leaves threads idle. Partitions set the parallelism ceiling.
- `AckMode.RECORD` commits after each record; `BATCH` (default) after the poll batch. `MANUAL_IMMEDIATE` when you must commit only after a DB write.
- Slow processing beyond `max.poll.interval.ms` (5 min) triggers a rebalance and duplicate delivery.
- Use `DefaultErrorHandler` with a `DeadLetterPublishingRecoverer` instead of catching everything in the listener.
- Make handlers idempotent: see [[idempotency]]. Producers that must be reliable use the [[outbox-pattern]].

## Example
```java
@KafkaListener(topics = "esim.activation", groupId = "provisioning", concurrency = "3")
public void onActivation(ActivationRequested evt, Acknowledgment ack) {
    if (processed.exists(evt.id())) { ack.acknowledge(); return; }
    provisioning.activate(evt);
    ack.acknowledge();
}
```

## Pitfalls
- A listener annotated with [[spring-transactional|@Transactional]] does **not** make the Kafka commit transactional with the DB. The DB commits, then the offset commit may fail: duplicate.
- Key your messages so that events for one subscriber land on one partition and stay ordered.
