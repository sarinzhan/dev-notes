---
title: Workflow engines vs hand-rolled sagas — Temporal, Camunda, state machines
tags: [workflows, temporal, camunda, saga, architecture]
learned: 2026-10-27
---
## TL;DR
A long-running business flow (eSIM order: reserve → pay → download → wait up to 24 h → activate) needs persisted state, retries, timers, compensation and a way to ask "where is order 42". You can build that yourself (a **state machine** in Postgres driven by Kafka events and a scheduler), use a **BPMN engine** (Camunda 7/8, Flowable) where the flow is a diagram executed by the engine, or use a **durable execution engine** (Temporal, and the same model in Cadence, Restate, DBOS) where the flow is ordinary code replayed from history. Hand-rolled wins for 2–4 steps with no waits; Temporal wins when flows have timers, signals and compensation; Camunda wins when business analysts must see and change the process.

## Key points

### What every option must provide
- **Durable state**: survives a pod restart mid-flow.
- **Retries with backoff** per step, with non-retryable error classes.
- **Timers**: "wait 24 h for the subscriber to install the profile", "remind after 1 h".
- **External signals**: the webhook or Kafka event that says "profile installed".
- **Compensation** in reverse order when a later step fails ([[saga]]).
- **Visibility**: list stuck flows, see which step failed and why, retry or cancel manually.
- **Versioning**: deploy a changed flow while thousands of old runs are in flight.
- **Idempotency** of every step, because every option re-executes steps after a crash ([[idempotency]]).

### The three approaches
| | Hand-rolled state machine | BPMN engine (Camunda 8, Flowable) | Durable execution (Temporal) |
|---|---|---|---|
| Flow definition | `status` column + `switch`/enum transitions, Kafka consumers, `@Scheduled` timers | BPMN XML diagram, service tasks bound to workers | Plain Java/Go/TS code in a workflow method ([[temporal-concepts]]) |
| State storage | Your Postgres table | Engine DB (Zeebe log for Camunda 8) | Temporal server (Postgres/Cassandra) as event history |
| Retries/timers | You write them (outbox, scheduler, delayed topics) | Built in, configured on tasks | Built in, `RetryOptions`, `Workflow.sleep` |
| Compensation | You order and persist it | Compensation events in BPMN | `Saga` helper, code |
| Visibility | Your admin page / SQL | Operate / Cockpit UI, process instance view | Web UI, search attributes, history per run |
| Versioning | Column `flow_version` + branches | Process definition versions; old instances finish on old diagram | `getVersion` / worker versioning |
| Testing | Unit tests of transitions | Engine test harness, slower | `TestWorkflowEnvironment` with time skipping |
| Ops cost | None extra | Engine cluster (Zeebe brokers + Elasticsearch for Camunda 8) | Temporal cluster (server + DB + visibility store) |
| Who reads the flow | Developers | Analysts and developers | Developers |
| Fits | Short, linear, few steps | Regulated or analyst-owned processes, human tasks, approvals | Code-heavy integrations, many branches, timers, signals |

### When hand-rolled is right
- The flow is 2–4 steps, each one event, no waits longer than a retry, and the team already has [[outbox-pattern]] + idempotent consumers. A `provisioning_order(status, step_data, updated_at)` table with `CHECK` on allowed statuses, a Kafka consumer per step, and a reaper job for stuck rows.
- Hidden costs show up later: a timer table, a "stuck orders" dashboard, backoff logic in three places, and the question "why did order 42 go from PAID to FAILED" answered by grepping logs ([[logging-stacks]]). Each is small; together they are a workflow engine with worse tests.
- Spring StateMachine or a hand-written enum machine helps with transitions but gives no durability, timers or retries by itself.

### When a BPMN engine is right
- The process *is* the product (loan approval, KYC, onboarding with manual review), changes are negotiated with non-developers, and audit wants to see the diagram that ran. Human tasks (forms, assignments, escalations) are first-class.
- Camunda 7 embeds in Spring Boot (shared DB); Camunda 8 is a separate Zeebe cluster with gRPC job workers and a commercial licence for production since 8.6. Flowable stays embeddable and open source.
- Cost: BPMN diagrams become code you cannot refactor with an IDE, expressions (FEEL/JUEL) hide logic, and engine upgrades are projects.

### When Temporal is right
- Flows with real waits (hours/days), many compensations, parallel branches, retries with different policies per step, and the need to query live state from a UI. The eSIM provisioning flow is the textbook case ([[esim-provisioning-platform]], [[design-esim-provisioning]]).
- Also good for "cron jobs that must not run twice" and for batch orchestration (child workflow per item).
- Cost: determinism discipline, another cluster (or Temporal Cloud), and the temptation to put business logic in workflows that should live in domain services ([[use-cases-and-application-layer]]).

### Combining with Kafka
- Events trigger workflows (`WorkflowClient.start` from a listener), activities publish events through the outbox. Kafka is the integration bus, the engine is the orchestrator ([[event-driven-architecture]]).
- Do not use Kafka as the workflow state: consumer offsets are not business state and reprocessing a topic is not resuming a flow.

## Example
The minimal hand-rolled version, to see what the engines replace: a status table, a step runner that advances one transition per call under a row lock, and a reaper that retries stuck orders with backoff.

```java
@Service
@RequiredArgsConstructor
public class ProvisioningStateMachine {
    private final JdbcClient jdbc;
    private final ProfileClient profiles;
    private final SmdpGateway smdp;
    private final OutboxRepository outbox;

    enum Status { NEW, PROFILE_RESERVED, DOWNLOAD_ORDERED, WAITING_INSTALL, ACTIVE, FAILED }

    /** Advance one step; safe to call concurrently and repeatedly (row lock + idempotent steps). */
    @Transactional
    public void advance(UUID orderId) {
        var row = jdbc.sql("SELECT status, iccid, attempts FROM provisioning_order WHERE id = :id FOR UPDATE SKIP LOCKED")
                .param("id", orderId).query(OrderRow.class).optional().orElse(null);
        if (row == null) return;                                   // another worker holds it
        try {
            switch (Status.valueOf(row.status())) {
                case NEW -> {
                    String iccid = profiles.reserve(orderId);                      // idempotent by orderId
                    move(orderId, Status.PROFILE_RESERVED, iccid, Duration.ZERO);
                }
                case PROFILE_RESERVED -> {
                    smdp.downloadOrder(row.iccid(), orderId.toString());
                    outbox.save(OutboxRow.of("esim.notifications", orderId, new SendQrV1(orderId, row.iccid())));
                    move(orderId, Status.WAITING_INSTALL, row.iccid(), Duration.ofHours(24));   // timer = next_check_at
                }
                case WAITING_INSTALL -> {                                        // reached only by the reaper after 24 h
                    profiles.release(row.iccid());                               // compensation
                    move(orderId, Status.FAILED, row.iccid(), Duration.ZERO);
                }
                default -> {}
            }
        } catch (TransientException e) {
            long backoffSec = Math.min(3600, (long) Math.pow(2, row.attempts()) * 30);
            jdbc.sql("UPDATE provisioning_order SET attempts = attempts + 1, next_check_at = now() + make_interval(secs => :s), last_error = :err WHERE id = :id")
                .param("s", backoffSec).param("err", e.getMessage()).param("id", orderId).update();
        }
    }

    @Scheduled(fixedDelay = 10_000)
    public void reaper() {                                                       // the "timer service"
        jdbc.sql("SELECT id FROM provisioning_order WHERE status NOT IN ('ACTIVE','FAILED') AND next_check_at <= now() LIMIT 100")
            .query(UUID.class).list().forEach(this::advance);
    }

    private void move(UUID id, Status to, String iccid, Duration nextCheck) {
        jdbc.sql("UPDATE provisioning_order SET status = :s, iccid = :i, attempts = 0, next_check_at = now() + :d WHERE id = :id")
            .param("s", to.name()).param("i", iccid).param("d", nextCheck).param("id", id).update();
    }
}
```

The signal "profile installed" is a webhook handler that sets `status = ACTIVE` directly. Compare the 60 lines above (no versioning, no history, no UI) with the Temporal workflow in [[temporal-concepts]] that covers all of it.

## Pitfalls
- Choosing Temporal for a two-step flow: a cluster, determinism rules and a new mental model for what an outbox and one consumer would do.
- Choosing a hand-rolled machine for a flow with human waits and five compensations: the "temporary" timer table and admin page outlive the project.
- Business logic in the orchestrator (any of the three): the workflow decides prices and eligibility instead of calling domain services; it becomes untestable and unversionable.
- Steps that are not idempotent: every engine re-runs a step after a crash between "done" and "recorded" ([[saga]] pivot discussion).
- Related: [[temporal-concepts]], [[saga]], [[background-jobs-and-schedulers]], [[outbox-pattern]], [[design-esim-provisioning]].
