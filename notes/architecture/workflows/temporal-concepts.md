---
title: Temporal concepts — workflows, activities, replay, Spring Boot
tags: [temporal, workflows, distributed-systems, spring-boot, java]
learned: 2026-10-27
---
## TL;DR
Temporal is a durable execution engine: you write a long-running business flow as ordinary Java code (a **workflow**), the side-effecting steps as **activities**, and the Temporal server persists every event (activity scheduled, completed, timer fired) in an **event history**. If the worker dies, another worker **replays** the history and the workflow code continues from where it was — which is why workflow code must be **deterministic**. Timers, retries, signals, queries and compensation come built in, making Temporal a persistent [[saga]] orchestrator.

## The problem it solves
- **Without it:** an eSIM order is reserve a profile → order the download at SM-DP+ → wait up to 24 h for the subscriber to install → activate on the network. If the pod restarts during the 24-hour wait, or SM-DP+ times out on the third attempt, the current step, the retry count and the timer must already be stored somewhere, and the code must know which compensations to run. By hand that is a status table, a scheduler and backoff code around every call.
- **What it changes:** the flow is one Java method; the server records every step in the event history, so after a crash another worker replays it and continues. Retries, timers, signals and compensation are built in.
- **When you don't need it:** short, linear flows of two or three steps with no long waits, where Kafka + outbox and idempotent consumers are enough, or when the team cannot run another stateful cluster.

## Key points

### Workflow vs activity
- **Workflow**: orchestration logic (`@WorkflowInterface` + `@WorkflowMethod`). Must be deterministic; may run for months. Receives inputs, calls activities via stubs, sleeps, waits for signals.
- **Activity**: one unit of real work with side effects (HTTP to SM-DP+, DB write, Kafka publish). Non-deterministic code is fine here. Activities have **timeouts** (`startToClose`, `scheduleToClose`, `heartbeat`) and a **retry policy** (initial interval, backoff coefficient, max attempts, non-retryable error types). Long activities send heartbeats so the server can detect a dead worker.
- **Worker**: a process that polls a **task queue** for workflow tasks and activity tasks and runs the registered implementations. Scale by adding workers; route special activities (GPU, on-prem network) to their own task queue.
- Each activity call may execute more than once (retry, worker crash after completion but before reporting) → activities must be [[idempotency|idempotent]]; use `workflowId + activity name` or a business key as the idempotency key.

### Event history and deterministic replay
- The server records commands and results. On replay, the SDK re-executes the workflow code and matches each command to the history; results of completed activities are returned from history instead of being re-run.
- **Forbidden in workflow code**: `System.currentTimeMillis()`, `new Random()`, `UUID.randomUUID()`, `Thread.sleep`, direct I/O (DB, HTTP, files), threads/`CompletableFuture`, static mutable state, iterating a `HashMap` whose order can change. Any of these make the replay diverge → `NonDeterministicException`.
- **Use instead**: `Workflow.currentTimeMillis()`, `Workflow.randomUUID()`, `Workflow.sleep(Duration)`, `Workflow.sideEffect(() -> …)` (recorded once, replayed from history), `Workflow.newActivityStub`, `Async.function` + `Promise` for parallelism, `Workflow.await(condition)`.
- Logging in workflows: `Workflow.getLogger` suppresses output during replay.

### Interaction and composition
- **Signal** (`@SignalMethod`): async message into a running workflow ("payment confirmed", "subscriber scanned the QR"); the workflow waits with `Workflow.await(() -> confirmed)`.
- **Query** (`@QueryMethod`): synchronous read of workflow state without changing it (status for the UI).
- **Update** (`@UpdateMethod`, GA in SDK 1.2x / server 1.24+): a signal that returns a result and can be validated with `@UpdateValidatorMethod`; replaces the signal + poll-query pattern.
- **Child workflows**: split large flows (one workflow per SIM in a bulk order); the parent can wait or fire-and-forget (`ParentClosePolicy`).
- **Continue-as-new**: a workflow that loops forever (daily reconciliation) grows an unbounded history; call `Workflow.continueAsNew(state)` to start a fresh run with trimmed history. Keep histories under ~10k events / a few MB.
- **Versioning**: changing workflow code while runs are in flight breaks replay. `int v = Workflow.getVersion("add-kyc-step", Workflow.DEFAULT_VERSION, 1);` branches old runs onto the old path and new runs onto the new one. SDK 1.2x also offers **Worker Versioning** (deployment-based: old workers finish old runs).
- **Saga helper**: `io.temporal.workflow.Saga` collects compensations; `saga.compensate()` runs them in reverse — the orchestrated saga with persistence for free.

### Spring Boot integration
- `temporal-spring-boot-starter`: configure `spring.temporal.connection.target`, `namespace`, `workers`. Annotate implementations with `@WorkflowImpl(taskQueues = "esim")` and `@ActivityImpl(taskQueues = "esim")`; the starter registers them and starts workers. Activities are Spring beans, so they can inject repositories and clients.
- Inject `WorkflowClient` to start workflows: `WorkflowClient.start(stub::provision, order)`; `WorkflowOptions.workflowId` gives idempotent start (`WorkflowIdReusePolicy`, `WorkflowIdConflictPolicy`).
- Testing: `temporal-testing` provides `TestWorkflowEnvironment` with time-skipping, so a 24-hour `Workflow.sleep` runs instantly; `WorkflowReplayer` replays production histories against new code in CI.

### Temporal vs Kafka + outbox
- Kafka + [[outbox-pattern]] + hand-written state machine is enough for short, mostly linear flows where each step is one event. You own: retries, timers ("wait 24 h for the QR scan"), compensation order, status queries, versioning of in-flight flows.
- Temporal is worth it when flows have waits, human steps, many branches and compensations, or when "where is order 42 and why is it stuck" must be answerable from a UI. Cost: another stateful cluster (server + Postgres/Cassandra + Elasticsearch for visibility) and the determinism discipline.
- They combine well: Kafka event triggers a workflow; activities publish Kafka events through the outbox.

## Example
eSIM provisioning: reserve a profile, order the download at SM-DP+, wait up to 24 h for the subscriber to install, then activate on the network; compensate if the subscriber never installs.

```java
@WorkflowInterface
public interface EsimProvisioningWorkflow {
    @WorkflowMethod
    ProvisioningResult provision(ProvisionOrder order);
    @SignalMethod
    void profileInstalled(String eid);
    @QueryMethod
    String status();
}

@WorkflowImpl(taskQueues = "esim")
public class EsimProvisioningWorkflowImpl implements EsimProvisioningWorkflow {
    private final EsimActivities acts = Workflow.newActivityStub(EsimActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofSeconds(30))
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setInitialInterval(Duration.ofSeconds(2))
                            .setBackoffCoefficient(2.0)
                            .setMaximumAttempts(5)
                            .setDoNotRetry(ProfileNotFoundException.class.getName())
                            .build())
                    .build());
    private String status = "STARTED";
    private boolean installed;

    @Override
    public ProvisioningResult provision(ProvisionOrder order) {
        Saga saga = new Saga(new Saga.Options.Builder().setParallelCompensation(false).build());
        try {
            String iccid = acts.reserveProfile(order.orderId());
            saga.addCompensation(acts::releaseProfile, iccid);
            status = "RESERVED";

            String matchingId = acts.createDownloadOrder(order.orderId(), iccid);
            saga.addCompensation(acts::cancelDownloadOrder, matchingId);
            acts.sendQrCode(order.msisdn(), matchingId);
            status = "WAITING_INSTALL";

            boolean ok = Workflow.await(Duration.ofHours(24), () -> installed);
            if (!ok) { saga.compensate(); status = "EXPIRED"; return ProvisioningResult.expired(iccid); }

            acts.activateOnHlr(order.msisdn(), iccid);   // retriable tail, no compensation
            status = "ACTIVE";
            return ProvisioningResult.active(iccid);
        } catch (ActivityFailure e) {
            saga.compensate();
            status = "FAILED";
            throw e;
        }
    }

    @Override public void profileInstalled(String eid) { installed = true; }
    @Override public String status() { return status; }
}

@ActivityInterface
public interface EsimActivities {
    String reserveProfile(String orderId);
    void releaseProfile(String iccid);
    String createDownloadOrder(String orderId, String iccid);
    void cancelDownloadOrder(String matchingId);
    void sendQrCode(String msisdn, String matchingId);
    void activateOnHlr(String msisdn, String iccid);
}

@Component
@ActivityImpl(taskQueues = "esim")
@RequiredArgsConstructor
public class EsimActivitiesImpl implements EsimActivities {
    private final ProfileRepository profiles;
    private final SmdpGateway smdp;
    // each method is idempotent by orderId/iccid; retried by Temporal on exception
    ...
}
```

## Pitfalls
- Putting a DB call or `RestClient` into the workflow class "just for a lookup": works until the first replay, then `NonDeterministicException`.
- Default activity options have **no** `startToCloseTimeout` → the SDK rejects the stub; and default retry is **infinite** — an activity throwing a business error retries forever unless the error is in `doNotRetry` or thrown as `ApplicationFailure.newNonRetryableFailure`.
- Large payloads in workflow arguments/results bloat the history (2 MB per payload limit, 50 MB per history); pass ids, not documents.
- Deploying changed workflow code without `getVersion` or worker versioning silently breaks every in-flight run on replay. Run `WorkflowReplayer` on sample histories in CI.
- Signals delivered before the workflow reaches `Workflow.await` are not lost (they are buffered in history), but a signal handler that calls activities needs `Workflow.await(() -> allHandlersFinished())` before the workflow returns.
- Related: [[saga]], [[idempotency]], [[outbox-pattern]], [[esim-provisioning-platform]], [[kafka-consumer]] (the alternative), [[system-design-basics]].
