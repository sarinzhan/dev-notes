---
title: Message queues and streams as a building block — queue vs log, Kafka vs RabbitMQ vs SQS
tags: [system-design, messaging, kafka, rabbitmq, async]
learned: 2026-10-27
---
## TL;DR
A queue or a log between two components buys four things: **decoupling** (the producer does not know or wait for the consumer), **buffering** (a spike of 10 K/s is absorbed and drained at the consumer's pace), **reliability** (the message survives a consumer crash and is retried), and **fan-out** (several consumers get the same event). The two models differ in ownership of the message: a **queue** (RabbitMQ, SQS, ActiveMQ) hands each message to one consumer and deletes it on ack; a **log** (Kafka, Pulsar, Kinesis) keeps an ordered, replayable record per partition and consumers track their own offset. Choose by the questions below; the internals are in [[kafka-architecture]], [[rabbitmq-basics]] and [[activemq-basics]].

## Key points

### Queue vs log
| | Queue (RabbitMQ, SQS) | Log (Kafka) |
|---|---|---|
| Delivery | push to one consumer, ack deletes | consumers pull, commit offsets; message stays until retention |
| Ordering | per queue, lost with competing consumers and requeue | strict per partition (by key) |
| Replay | no (message gone after ack) | yes: reset offset, reprocess a day |
| Fan-out | exchange → several queues (copies) | several consumer groups read the same partitions |
| Per-message features | priority, TTL, delayed delivery, routing keys, per-message ack/nack, DLQ built in | none of these natively; retries and DLQ are topics you build ([[dead-letter-queues-and-retries]]) |
| Throughput per node | tens of thousands msg/s | hundreds of thousands to millions msg/s, sequential disk |
| Consumer parallelism | any number of consumers on one queue | ≤ partitions per group; add partitions to scale |
| Backpressure | broker-side flow control, prefetch | consumer pulls as fast as it can; lag is the signal ([[backpressure-and-flow-control]]) |
| Best fit | task queues, RPC-style work, routing, delays | event streams, audit, CDC, multiple independent consumers, replay |

### When to use which
- **Task distribution** ("send this SMS", "generate this PDF"), per-message priority or delay, many small queues with different consumers: a queue. RabbitMQ self-hosted; SQS when on AWS and ops time matters (managed, at-least-once, 14-day retention, FIFO queues at lower throughput).
- **Event backbone** (one fact consumed by billing, analytics, notifications, search indexer), ordering per entity, replay for a new consumer or a bug fix, high volume: Kafka. The eSIM platform and the notification service both belong here ([[esim-provisioning-platform]], [[design-notification-service]]).
- **Both** is common: Kafka for events, a queue or a workflow engine for the per-task retries ([[background-jobs-and-schedulers]], [[workflow-engines-vs-sagas]]).
- Not a queue at all: a request that needs the answer now (login, balance check) stays synchronous ([[service-communication-sync-vs-async]]).

### Guarantees you must design, not assume
- Delivery is **at-least-once** in every practical setup; consumers must be idempotent ([[delivery-guarantees]], [[exactly-once-myth]], [[idempotency]]).
- Publishing is atomic with the DB write only through the [[outbox-pattern]]; `save(); send();` loses or phantoms events.
- Ordering is per key per partition and only while one consumer handles that partition; a retry topic breaks ordering for the retried key ([[dead-letter-queues-and-retries]]).
- Schema changes need a compatibility rule, otherwise an old consumer crashes on a new field ([[message-schema-evolution]]).

### Sizing the log
- Partitions: `max(target throughput / per-consumer throughput, number of consumers you want)`, rounded up with growth room. 12 partitions let 12 workers run; 13 workers leave one idle.
- Key choice decides hot partitions: `userId` spreads evenly across millions of users; `tenantId` with one huge tenant does not ([[sharding-strategies]] has the same lesson).
- Retention is a disk budget: `msg/s × size × replication × seconds` ([[back-of-envelope-estimation]]); 7 days is a common default, longer for replayable facts, compacted topics for "latest state per key".
- Consumer lag is the backlog metric; alert on lag growth, not on absolute lag ([[slo-sli-and-alerting]]).

### Interview talking points
- "Why not call the service directly?" Coupling and availability: a synchronous chain's availability is the product of every hop's; with a queue the producer is up when the consumer is down.
- "What if the broker is down?" The outbox keeps events in Postgres and the relay catches up; the API still returns `202`.
- "What about a poison message?" Retry N times with backoff, then DLQ with the error, alert, and a replay tool.
- "How do you scale consumers?" Add instances up to the partition count; beyond that, more partitions or a second stage keyed differently.

## Example
Topic layout and consumer configuration for the notification pipeline: an events topic keyed by subscriber, per-channel work topics, retry and DLQ topics, and a Spring Kafka consumer tuned for at-least-once with manual commits.

```yaml
# topics (created by an operator script or Terraform; Kafka 4.x, KRaft)
topics:
  - name: notifications.requested          # every accepted notification, from the outbox relay
    partitions: 24
    replication: 3
    config: { retention.ms: 604800000, min.insync.replicas: 2 }        # 7 days
  - name: notifications.sms                # per-channel work, same key (subscriberId) keeps per-user order
    partitions: 12
    replication: 3
  - name: notifications.sms.retry.1m       # retry tiers with increasing delay, consumer pauses until due
    partitions: 12
    replication: 3
  - name: notifications.sms.dlq
    partitions: 3
    replication: 3
    config: { retention.ms: 2592000000 }                                # 30 days, replayed manually
  - name: subscriber.preferences           # compacted: latest prefs per subscriber for cache warm-up
    partitions: 12
    replication: 3
    config: { cleanup.policy: compact }

# application.yml of the sms-worker
spring:
  kafka:
    bootstrap-servers: kafka-0:9092,kafka-1:9092,kafka-2:9092
    consumer:
      group-id: sms-worker
      enable-auto-commit: false              # commit after the provider call succeeded (at-least-once)
      auto-offset-reset: earliest
      max-poll-records: 50
      properties:
        max.poll.interval.ms: 300000         # 50 records × 3 s provider timeout × retries must fit here
        isolation.level: read_committed
    listener:
      ack-mode: manual_immediate
      concurrency: 12                        # = partitions; more threads would idle
    producer:
      acks: all
      properties:
        enable.idempotence: true
        linger.ms: 5
```

Why a queue would also work here: per-message delay and priority ("HIGH before LOW") are native in RabbitMQ and awkward in Kafka; the choice for Kafka is replay, the shared event backbone and ordering per subscriber. Details in [[kafka-consumer]], [[kafka-producer]] and [[spring-kafka-consumer]].

## Pitfalls
- Using Kafka as a task queue with per-message acks in mind: one slow message blocks its partition; put slow work behind a worker pool with its own retry tier, or use a queue.
- Too few partitions "to start with": repartitioning changes key → partition mapping and breaks ordering during the transition; over-provision early (partitions are cheap until the thousands).
- A consumer group with `concurrency` higher than partitions, or two services sharing one group id by copy-paste: half the messages go to the wrong service.
- Unbounded retries on a transient error that is really permanent (invalid phone number): the message circles forever and the DLQ stays empty. Classify errors ([[error-handling-strategies]]).
- Forgetting the broker is stateful infrastructure: disk, replication, rebalances, upgrades. SQS or a managed Kafka removes most of this for a small team ([[requirements-and-constraints]]).
- Related: [[event-driven-architecture]] for the architectural style on top; [[async-processing-and-event-driven-scaling]] for scaling the consumers.
