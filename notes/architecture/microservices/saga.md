---
title: Saga pattern — orchestration vs choreography, compensation
tags: [microservices, distributed-systems, transactions, temporal, kafka]
learned: 2026-10-27
---
## TL;DR
A saga is a business transaction that spans several services, implemented as a **sequence of local transactions**, each committed in its own database. If a later step fails, the saga runs **compensating transactions** that semantically undo the earlier steps. Sagas replace distributed 2PC transactions with eventual consistency plus explicit "undo" logic; the two ways to drive them are a central **orchestrator** or event-driven **choreography**.

## Key points

### Why not a distributed transaction
- 2PC (XA) needs a coordinator that holds locks in every participant until the global commit. It is slow, blocks on coordinator failure, and does not exist over Kafka, HTTP or a third-party API (card processor, SM-DP+ server).
- Microservices own their data ([[ddd-basics|bounded contexts]]), so cross-service ACID is off the table. Sagas give **atomicity across services** (all steps or all compensated) without isolation.

### Orchestration vs choreography
| | Orchestration | Choreography |
|---|---|---|
| Who decides the next step | A central saga orchestrator (state machine) | Each service reacts to events and emits new ones |
| Coupling | Services know only the orchestrator | Services know the event contracts of their neighbours |
| Visibility | One place to see "where is order 42" | Spread over logs/topics; needs tracing |
| Risk | Orchestrator becomes a god service | Cyclic event dependencies, hard to change |
| Fits | Long flows with many steps, compensation, timeouts | 2–4 steps, simple fan-out |

- Orchestrator implementations: a hand-written state machine persisted in Postgres, Axon/Eventuate, or a workflow engine such as Temporal. **Temporal is a saga orchestrator**: a workflow is the orchestrator, activities are the steps, `Saga` helper registers compensations, and event history gives persistence and replay for free ([[temporal-concepts]]).
- Choreography lives on Kafka: `OrderCreated` → payment service emits `PaymentAuthorized` → provisioning emits `ProfileReserved`, with the [[outbox-pattern]] making every event publish atomic with its local commit.

### Transaction types inside a saga
- **Compensatable** transactions can be undone (reserve stock → release stock; authorize card → void authorization).
- The **pivot transaction** is the point of no return: once it commits, the saga must complete, never roll back (capture the payment, send the eSIM download to the subscriber). Everything after the pivot must be **retriable** until success.
- Compensation is semantic, not a DB rollback: a refund is a new transaction, the original payment stays in history.

### Isolation problems and countermeasures
- Sagas lack the "I" of ACID: other transactions see intermediate state (lost update, dirty read on a reserved-but-not-paid order).
- **Semantic lock**: mark the record (`status = PENDING`) so other flows refuse or wait; clear it in the final step or compensation.
- **Commutative updates** (increments instead of set), **pessimistic view** (reorder steps so the risky one happens last), **re-read value** before the pivot, **version file** (store the sequence of operations to apply them in order).

### State and reliability
- Persist saga state (id, current step, step results, compensations) in a table or in Temporal's history; after a crash the orchestrator resumes from the last persisted step.
- Every step is called at least once, so each step must be [[idempotency|idempotent]] (saga id as the idempotency key) and compensations must tolerate "nothing to undo".
- Use timeouts per step; a stuck step turns into a compensation or into a manual "needs attention" state, never a silent hang.

## Example
Hand-written orchestrator for an eSIM order: reserve profile → charge card (pivot) → activate on HLR. Each step is a local transaction in another service called over HTTP/Kafka.

```java
@Service
@RequiredArgsConstructor
public class EsimOrderSaga {
    private final ProfileClient profiles;
    private final PaymentClient payments;
    private final HlrClient hlr;
    private final SagaStateRepository states;

    @Transactional
    public void run(OrderId orderId, Msisdn msisdn, Money price) {
        Deque<Runnable> compensations = new ArrayDeque<>();
        SagaState state = states.start(orderId);
        try {
            String iccid = profiles.reserve(orderId.value());           // idempotent by orderId
            compensations.push(() -> profiles.release(iccid));
            state.step("PROFILE_RESERVED", iccid);

            String authId = payments.authorize(orderId.value(), price);
            compensations.push(() -> payments.voidAuth(authId));
            state.step("PAYMENT_AUTHORIZED", authId);

            payments.capture(authId);                                   // pivot: no rollback after this
            state.step("PAYMENT_CAPTURED");

            retryUntilDone(() -> hlr.activate(msisdn, iccid));          // retriable tail
            state.complete();
        } catch (RuntimeException e) {
            while (!compensations.isEmpty()) compensations.pop().run();  // reverse order
            state.compensated(e.getMessage());
            throw e;
        }
    }
}
```

## Pitfalls
- Compensations that fail: queue them for retry (outbox table, scheduler, or Temporal retry policy); a compensation that is silently dropped leaves money charged and nothing delivered.
- Compensation order matters: undo in reverse, because later steps may depend on earlier ones.
- Choreography without an explicit saga id and correlation id is undebuggable — add [[distributed-tracing]] and put the saga id in every event header.
- Forgetting the pivot: retrying a card capture after a refund, or releasing a profile the subscriber already downloaded.
- Related: [[outbox-pattern]] (atomic publish of each step's event), [[idempotency]] (safe retries), [[temporal-concepts]] (orchestrator engine), [[circuit-breaker]] (calling flaky participants), [[system-design-basics]].
