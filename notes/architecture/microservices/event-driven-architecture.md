---
title: Event-driven architecture
tags: [microservices, events, kafka, architecture]
learned: 2026-10-27
---
## TL;DR
In an event-driven architecture (EDA) services communicate by publishing **facts** (`OrderPaid`, `ProfileActivated`) to a broker and reacting to the facts of others, instead of calling each other. The producer does not know its consumers, so adding a consumer costs nothing on the producer side and a slow consumer does not slow the producer. The price: eventual consistency, duplicates, ordering questions, and a contract (the event schema) that is harder to evolve than a REST endpoint because you cannot see who depends on it. EDA is the choreography half of [[saga]], built on [[outbox-pattern]] + [[idempotency]].

## Key points

### Events vs commands vs queries
| | Event | Command | Query |
|---|---|---|---|
| Meaning | Something happened (past tense) | Do something (imperative) | Tell me something |
| Receivers | 0..n, unknown to the sender | Exactly one handler | One responder |
| Can be rejected | No, it is a fact | Yes | n/a |
| Transport | Topic, fan-out | Queue or direct call | Sync call |
| Coupling | Consumer depends on producer's schema | Sender depends on handler's API | Caller depends on responder |
- A topic named `send-sms` carrying `{to, text}` is a command over Kafka: legitimate, but it has one consumer and the sender expects it to be done. Naming it `sms.requested` does not make it an event.
- Event notification (id only, call back for data) vs event-carried state transfer (payload has the data): the second decouples availability, the first keeps payloads small ([[domain-events-and-integration-events]]).

### Topologies
- **Broker topology** (choreography): each service reacts and emits; no central coordinator. Simple to start, hard to follow after ten services ("who reacts to `OrderPaid`?").
- **Mediator topology** (orchestration): a coordinator consumes events and issues commands; Temporal or a saga orchestrator plays this role ([[temporal-concepts]]).
- Most systems mix: choreography for fan-out side effects (notifications, analytics, audit), orchestration for multi-step business flows with compensation.

### Consumer coupling, the hidden cost
- The schema is the contract. Removing a field breaks consumers you have never heard of; add fields only, version on breaking change, keep a registry ([[message-schema-evolution]]).
- **Semantic coupling**: consumers that infer `status == "ACTIVE"` means "bill the subscriber" couple to the producer's internal state machine. Publish events at business granularity (`SubscriptionStarted`), not row-change granularity.
- **Temporal coupling** goes away (producer up, consumer down is fine); **ordering coupling** appears: `ProfileActivated` before `SimRegistered` because they sit on different partitions. Key by aggregate id ([[aggregates-and-invariants]], [[kafka-producer]]) and make consumers tolerate out-of-order across aggregates.

### Delivery semantics the consumer must handle
- At-least-once: dedup by event id in the same transaction as the effect ([[idempotency]], [[exactly-once-myth]]).
- Replay: consumers that can be re-run from offset 0 (rebuild a projection) are the dream; consumers with side effects (SMS) need a "replay mode" flag or a time guard.
- Poison messages: retries with backoff, then a dead letter topic with alerting ([[dead-letter-queues-and-retries]]).
- Backpressure: a consumer that cannot keep up shows as lag, not as errors; alert on lag ([[kafka-consumer]], [[backpressure-and-flow-control]]).

### When EDA fits and when it does not
- Fits: many consumers of one fact, long-running or bursty work, audit/analytics streams, decoupling teams, integrating a new system without touching producers ([[async-processing-and-event-driven-scaling]]).
- Does not fit: the caller needs the answer now (price check, auth), strict read-your-writes, simple CRUD with one consumer. Use a sync call and keep the event for the side effects ([[service-communication-sync-vs-async]]).
- Interview framing: "I use events for facts other services care about and sync calls for questions I need answered; every event goes through the outbox and every consumer is idempotent."

## Example
Topic and consumer conventions for the eSIM platform: one topic per aggregate type with the event type in a header, keyed by aggregate id, consumers grouped per service.

```yaml
# topics.yaml (applied by a Kafka admin job / Strimzi KafkaTopic CRs)
topics:
  - name: esim.sim.events          # all facts about one Sim, ordered per key (sim id)
    partitions: 12
    replicationFactor: 3
    config:
      retention.ms: 2592000000     # 30 days: consumers can be rebuilt from here
      cleanup.policy: delete
  - name: esim.sim.events.DLT
    partitions: 12
    replicationFactor: 3
    config:
      retention.ms: 7776000000     # 90 days for post-mortems

# consumer side (application.yaml of the billing service)
spring:
  kafka:
    consumer:
      group-id: billing              # one group per service, not per instance
      auto-offset-reset: earliest    # a new service replays history on first start
      enable-auto-commit: false
    listener:
      ack-mode: record
```

```java
@KafkaListener(topics = "esim.sim.events", groupId = "billing")
@Transactional
public void on(ConsumerRecord<String, byte[]> rec, Acknowledgment ack) {
    String type = header(rec, "type");              // "ProfileActivated.v1"
    UUID eventId = UUID.fromString(header(rec, "event-id"));
    if (!processed.insertIfAbsent(eventId)) { ack.acknowledge(); return; }   // idempotent
    switch (type) {
        case "ProfileActivated.v1" -> billing.startSubscription(codec.decode(rec.value(), ProfileActivatedV1.class));
        case "SimTerminated.v1"    -> billing.closeSubscription(codec.decode(rec.value(), SimTerminatedV1.class));
        default -> log.debug("ignoring {}", type);   // unknown types are not errors
    }
    ack.acknowledge();
}
```

## Pitfalls
- Events as a remote procedure call: producer publishes `X.requested`, waits for `X.completed` with a correlation id and a timeout. You have built a slow synchronous call with none of the tooling; use a sync API or a workflow engine.
- Shared event library with the producer's domain classes: every consumer now compiles against the producer's internals ([[coupling-cohesion-and-general-principles]]).
- No event catalogue: six months in, nobody knows which topics exist, what they mean, or who consumes them. Keep an AsyncAPI document or at least a README per topic.
- Debugging without correlation: put `traceparent` and the saga/aggregate id in headers from day one ([[distributed-tracing]]).
- Related: [[cqrs]], [[event-sourcing]], [[message-queues-and-streams]], [[kafka-architecture]], [[design-notification-service]].
