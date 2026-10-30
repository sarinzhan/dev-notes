---
title: Domain events vs integration events
tags: [ddd, events, spring, kafka, outbox]
learned: 2026-10-27
---
## TL;DR
A **domain event** is a fact that happened inside one bounded context, named in the past tense (`ProfileActivated`), raised by an aggregate and handled in the same process. An **integration event** is the version of that fact that leaves the context: published to Kafka, with a stable schema, for other services. Domain events are an in-memory design tool (decoupling aggregates, side effects after commit); integration events are a contract, so they go through the [[outbox-pattern]] and are versioned like an API ([[message-schema-evolution]]).

## The problem it solves
- **Without it:** `ProfileService.activate()` also sends the SMS, updates the billing read model and sends to Kafka directly. Every new reaction edits the same method, a rollback still leaves the SMS sent, and other teams consume your JPA entity as JSON, so a column rename breaks them.
- **What it changes:** the aggregate only records a fact; handlers react to it after commit, and a separate versioned integration event, published through the outbox, is the only contract other services see.
- **When you don't need it:** one action with one side effect, or a context that nobody outside consumes; a direct method call is clearer.

## Key points

### The two kinds compared
| | Domain event | Integration event |
|---|---|---|
| Scope | Inside the bounded context | Between contexts / services |
| Transport | `ApplicationEventPublisher`, in-memory list on the aggregate | Kafka, RabbitMQ, webhooks |
| Shape | Rich domain types (`Iccid`, `Money`) | Flat, serialisable, versioned (JSON/Avro/Protobuf), only what consumers need |
| Who is coupled | Handlers in the same codebase | Unknown consumers, possibly other teams |
| Delivery | Same transaction or right after commit | At-least-once, async, may arrive late or twice |
| Name | `OrderConfirmed` | `esim.order.confirmed.v1` |
- One domain event often produces one integration event, but not always: internal events (`LineAdded`) stay inside; some integration events aggregate several domain facts.

### Raising and handling domain events in Spring
- Aggregate records events (`events.add(...)`), the application service publishes them after `save()` with `ApplicationEventPublisher.publishEvent(e)` ([[aggregates-and-invariants]]). Spring Data's `@DomainEvents` / `@AfterDomainEventPublication` on the aggregate automates this on `repository.save()` ([[spring-data-repositories]]).
- `@EventListener`: runs synchronously inside the publisher's transaction. Good for in-context reactions that must commit together (update a read model in the same DB).
- `@TransactionalEventListener(phase = AFTER_COMMIT)` (default phase): runs only if the transaction committed. Right place for side effects that must not happen on rollback: send an email, write the outbox row... except that the outbox row must be in the *same* transaction, so use `BEFORE_COMMIT` for it, or write it in the service. After-commit listeners run without a transaction unless `@Transactional(propagation = REQUIRES_NEW)` is added ([[spring-transactional]]).
- `@Async` on a listener moves it to another thread: no transaction, no MDC/trace context unless propagated ([[distributed-tracing]]), and exceptions are swallowed into the executor. Fine for notifications, wrong for anything that must not be lost.
- Event ordering between listeners: `@Order`; if order matters a lot, the design probably wants one handler.

### Turning a domain event into an integration event
1. A `BEFORE_COMMIT` listener (or the application service) maps `ProfileActivated` to an `ProfileActivatedV1` payload and inserts it into the outbox in the same transaction.
2. The relay publishes it to Kafka keyed by aggregate id.
3. Consumers in other contexts translate it into *their* model through an anti-corruption layer ([[bounded-contexts-and-context-mapping]]) and process it idempotently by event id ([[idempotency]]).
- The payload is a contract: include `eventId`, `occurredAt`, `aggregateId`, `version`, and the facts, never the whole JPA entity.

### Event-carried state vs notification
- **Notification**: `{"simId": "..."}`; consumers call back for details. Small, always fresh, but creates a synchronous dependency.
- **Event-carried state transfer**: the event contains what consumers need (`msisdn`, `iccid`, `activatedAt`). Consumers stay independent; the producer must decide what is public. Prefer this for integration events ([[event-driven-architecture]]).

### Commands vs events
- A command is a request to do something (`ActivateProfile`), addressed to one handler, may be rejected. An event is a fact, broadcast, cannot be rejected. Kafka topics that carry commands (`esim.activation.requested`) are fine, but name them honestly and expect one consumer ([[cqrs]]).

## Example
A domain event raised by the aggregate, an in-process listener that writes the outbox row before commit, and an after-commit listener for a side effect.

```java
public record ProfileActivated(UUID simId, Iccid iccid, Instant occurredAt) implements DomainEvent {}

@Service
@RequiredArgsConstructor
public class ActivateProfileHandler {
    private final SimRepository sims;
    private final ApplicationEventPublisher events;

    @Transactional
    public void handle(ActivateProfile cmd) {
        Sim sim = sims.findById(cmd.simId()).orElseThrow();
        sim.activate(cmd.iccid());
        sims.save(sim);
        sim.pullEvents().forEach(events::publishEvent);   // still inside the transaction
    }
}

@Component
@RequiredArgsConstructor
class ProfileActivatedListeners {
    private final OutboxRepository outbox;
    private final SmsGateway sms;

    // same transaction: the integration event exists iff the activation commits
    @TransactionalEventListener(phase = TransactionPhase.BEFORE_COMMIT)
    public void toOutbox(ProfileActivated e) {
        var payload = new ProfileActivatedV1(e.simId(), e.iccid().value(), e.occurredAt());
        outbox.save(OutboxRow.of(UUID.randomUUID(), "esim.profile.activated", e.simId().toString(), payload, 1));
    }

    // after commit: a lost SMS is acceptable, a phantom SMS on rollback is not
    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    @Async
    public void notifySubscriber(ProfileActivated e) {
        sms.send(e.simId(), "Your eSIM profile is active");
    }
}
```

## Pitfalls
- Publishing to Kafka from an `AFTER_COMMIT` listener: the commit is done, the send can fail, nobody retries. That is the dual write the outbox exists to remove.
- `@TransactionalEventListener` on a method called when no transaction is active: the event is silently dropped (unless `fallbackExecution = true`).
- Putting JPA entities in the event: lazy collections explode in the async listener after the session closed ([[jpa-persistence-context-and-entity-lifecycle]]).
- Events named in the imperative (`ActivateProfile`) are commands in disguise; a handler that can "fail" an event has a design problem.
- Related: [[outbox-pattern]], [[event-driven-architecture]], [[event-sourcing]] (events as the source of truth, not just notifications), [[spring-kafka-consumer]].
