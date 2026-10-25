---
title: Idempotent consumers and APIs
tags: [reliability, kafka, api-design, redis]
learned: 2026-09-05
---
## TL;DR
An operation is idempotent when applying it twice has the same effect as once. Every at-least-once channel ([[spring-kafka-consumer|Kafka]], retries, the [[outbox-pattern]]) needs it on the receiving side.

## Key points
- **Idempotency key**: client-generated id stored with the result; a repeat returns the stored result.
- Storage: a unique constraint in Postgres (`processed_events(event_id)`) inserted in the same transaction as the effect, or a Redis `SET NX` with TTL ([[redis-data-structures]]).
- Natural idempotency beats bookkeeping: `SET status = 'ACTIVE'` is idempotent, `balance = balance - 100` is not.
- For HTTP: `PUT` and `DELETE` are idempotent by contract; `POST` needs an `Idempotency-Key` header.

## Example
```java
@Transactional
public void handle(ActivationRequested evt) {
    try { processed.insert(evt.id()); }          // unique constraint
    catch (DataIntegrityViolationException dup) { return; }
    sim.activate(evt.iccid());
}
```

## Pitfalls
- Checking "already processed?" and then doing the work in separate transactions reopens the race. Insert the key in the same transaction.
- Under READ COMMITTED a concurrent duplicate blocks on the unique index until the first commits; see [[postgres-isolation-levels]].
