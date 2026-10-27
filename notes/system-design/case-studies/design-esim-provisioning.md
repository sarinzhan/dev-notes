---
title: Design an eSIM provisioning system
tags: [system-design, case-study, temporal, orchestration, telecom]
learned: 2026-10-27
---
## TL;DR
eSIM provisioning turns "subscriber taps Buy eSIM" into a profile downloaded onto the phone and a number active in the network: reserve a profile at the SM-DP+, create the subscriber in billing and HLR/HSS, hand the client an activation code, wait (possibly hours) for the download confirmation, then activate. It is a long-running, multi-step orchestration across external systems that time out, answer late, or succeed silently, and every step must be retried or compensated. The design is a thin REST API, a Temporal workflow per order holding the state machine, idempotent activities with keys derived from the order, an outbox feeding Kafka for everyone else, and a status query for support. It is the generalised version of [[esim-provisioning-platform]]; Temporal mechanics are in [[temporal-concepts]].

## Key points

### Requirements and estimates
- Functional: order an eSIM (new number or swap from a physical SIM), get a QR/activation code, poll status, cancel; notify on each step; operator view of stuck orders; bulk orders for business clients.
- Non-functional: no double profile reservation or double billing; an order must finish or roll back even after a deploy or a worker crash; visible state for support; external systems have different SLAs (SM-DP+ 200 ms–30 s, HLR seconds, billing can be minutes in batch windows).
- Numbers: 5 000 activations/day ≈ 0.06/s average, campaign peaks of a few per second; each order has 6–8 steps and lives minutes to days (waiting for the subscriber to scan). Throughput is tiny; the problem is duration and reliability, not QPS.

### API
```
POST /v1/esim/orders       Idempotency-Key   { msisdn | newNumber: true, tariffId, deviceEid? }
  -> 202 { orderId, status: CREATED }
GET  /v1/esim/orders/{id}  -> { status, step, activationCode?, lastError?, updatedAt }
POST /v1/esim/orders/{id}/cancel
POST /v1/callbacks/smdp    (SM-DP+ notifications: profile DOWNLOADED / INSTALLED / ENABLED)
```
- Status comes from a Temporal query on the running workflow (live) or from the order row after completion; the client polls `GET` or receives a push ([[design-notification-service]]).

### Data model
- `esim_order(id, msisdn, subscriber_id, tariff_id, status, iccid, matching_id, workflow_id, created_at, updated_at)` — the read model for lists and support screens; the workflow holds the authoritative in-flight state.
- `profile(iccid, eid?, state: AVAILABLE|RESERVED|DOWNLOADED|ACTIVE|RELEASED, order_id, reserved_at)` — mirror of the SM-DP+ pool for local checks and leak detection.
- `step_log(order_id, step, attempt, result, error, at)` — append-only, for support and reconciliation.
- `outbox` for `EsimOrderCreated`, `EsimActivated`, `EsimFailed` ([[outbox-pattern]]).

### High-level design
```
app / web ──▶ API (Spring) ──tx: order row + outbox──▶ Postgres ──relay──▶ Kafka esim.events
                 │ start workflow (workflowId = orderId)                       └─▶ notifications, CRM, analytics
                 ▼
           Temporal server ◀──poll──▶ workers (activities)
                                        ├─▶ SM-DP+ (reserve profile, confirm order)   idem key orderId:reserve
                                        ├─▶ Billing (create subscriber, assign tariff)  idem key orderId:billing
                                        ├─▶ HLR/HSS (provision IMSI/MSISDN)             idem key orderId:hlr
                                        └─▶ Notify (activation code, done)
SM-DP+ ──callback DOWNLOADED/ENABLED──▶ API ──signal──▶ workflow (waiting up to 72 h) ──▶ activate ──▶ done
```

### Deep dive: orchestration that survives everything
- **Workflow per order**, `workflowId = orderId` with reject-duplicate policy: starting the same order twice is impossible, which is the first idempotency layer. The workflow code is the state machine: reserve → billing → HLR → send code → await download signal (timer 72 h) → activate → notify.
- **Activities are idempotent by construction**: every external call carries `orderId:step`; the adapter first queries the external system by that key and only then acts. A retried `reserveProfile` after an SM-DP+ timeout finds the already reserved profile instead of reserving a second one ([[idempotency]]).
- **Timeouts and retries per activity**: `startToClose` 30 s for SM-DP+ with 5 retries and exponential backoff; billing with a longer `scheduleToClose` because of batch windows; non-retryable errors (`NoProfilesAvailable`, `InvalidTariff`) fail fast.
- **Compensation**: the workflow registers an undo for each completed step (`releaseProfile`, `deleteSubscriber`, `deprovisionHlr`) and runs them in reverse on permanent failure, a persistent orchestrated [[saga]]. Compensations are activities too, so they retry.
- **Waiting for the human**: the subscriber may scan the QR tomorrow. The workflow blocks on `Workflow.await(() -> downloaded || cancelled)` with a 72 h timer; no thread, no DB polling. The SM-DP+ callback arrives as a signal; cancellation is another signal. If the timer fires, the profile is released and the order expires ([[background-jobs-and-schedulers]]).
- **Determinism and versioning**: no `now()`, random or I/O inside the workflow; `Workflow.getVersion` when steps change while orders are in flight.
- **Visibility**: `@QueryMethod status()` returns the current step and last error; one trace id from the app into the activities ([[distributed-tracing]], [[observability-three-pillars]]).
- **Reconciliation**: a nightly job compares `profile` states with the SM-DP+ inventory and releases `RESERVED` profiles older than 72 h. Orchestration reduces drift; reconciliation catches the rest.

### Trade-offs
| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Orchestrator | Temporal workflow | Kafka choreography with a state table | timers, retries, history and "where is it stuck" come for free; choreography spreads the state machine across consumers ([[workflow-engines-vs-sagas]]) |
| Source of in-flight truth | workflow state, projected to `esim_order` | DB status column updated by each step | one place for transitions; DB row for queries ([[read-write-splitting-and-cqrs-lite]]) |
| Waiting for download | signal + 72 h timer | poll SM-DP+ every minute | zero load while waiting; callbacks are pushed anyway |
| Bulk orders | child workflow per SIM | one giant workflow | bounded history size, per-SIM retry and compensation |
| Scaling workers | more workers on the task queue; separate queue for SM-DP+ activities | one queue | isolate the slow external dependency ([[horizontal-vs-vertical-scaling]]) |

## Example
The workflow skeleton with compensation and the wait for the download signal:

```java
public class EsimOrderWorkflowImpl implements EsimOrderWorkflow {
    private final ProvisioningActivities acts = Workflow.newActivityStub(ProvisioningActivities.class,
        ActivityOptions.newBuilder()
            .setStartToCloseTimeout(Duration.ofSeconds(30))
            .setRetryOptions(RetryOptions.newBuilder()
                .setMaximumAttempts(5).setBackoffCoefficient(2.0)
                .setDoNotRetry("NoProfilesAvailable", "InvalidTariff").build())
            .build());
    private String step = "CREATED";
    private boolean downloaded, cancelled;

    @Override
    public void run(EsimOrder order) {
        Saga saga = new Saga(new Saga.Options.Builder().setParallelCompensation(false).build());
        try {
            step = "RESERVE";  Profile p = acts.reserveProfile(order.id());      // idem key orderId:reserve
            saga.addCompensation(acts::releaseProfile, order.id(), p.iccid());
            step = "BILLING";  acts.createSubscriber(order.id(), p.iccid());
            saga.addCompensation(acts::deleteSubscriber, order.id());
            step = "HLR";      acts.provisionHlr(order.id(), p.iccid());
            saga.addCompensation(acts::deprovisionHlr, order.id());
            step = "WAIT_DOWNLOAD"; acts.sendActivationCode(order.id(), p.matchingId());
            boolean ok = Workflow.await(Duration.ofHours(72), () -> downloaded || cancelled);
            if (!ok || cancelled) throw ApplicationFailure.newNonRetryableFailure("not downloaded", "Expired");
            step = "ACTIVATE"; acts.activate(order.id(), p.iccid());
            step = "DONE";     acts.notifyDone(order.id());
        } catch (ActivityFailure | ApplicationFailure e) {
            step = "COMPENSATING"; saga.compensate(); step = "FAILED"; throw e;
        }
    }
    @Override public void profileDownloaded() { downloaded = true; }   // @SignalMethod
    @Override public void cancel()            { cancelled = true; }    // @SignalMethod
    @Override public String status()          { return step; }         // @QueryMethod
}
```

## Pitfalls
- Interviewers probe: "SM-DP+ timed out after reserving — what now?" Query by the idempotency key before reserving again; compensation if a later step fails for good. "Worker dies mid-order?" Another worker replays the history and continues. "Deploy with 300 orders waiting for download?" `getVersion` branches; old runs finish on the old path.
- "Why not just a status column and a cron retry?" It works until timers, compensation and visibility are needed; then you have rewritten half of Temporal ([[workflow-engines-vs-sagas]]).
- Putting `LocalDateTime.now()` or a random id in the workflow breaks replay after the next deploy; the project story has the scar.
- Activities that write to the DB and publish to Kafka separately reintroduce the dual write; use the outbox inside the activity's transaction.
- Related: [[esim-provisioning-platform]], [[temporal-concepts]], [[saga]], [[async-processing-and-event-driven-scaling]], [[design-payment-processing]] for the same "unknown outcome" problem with money.
