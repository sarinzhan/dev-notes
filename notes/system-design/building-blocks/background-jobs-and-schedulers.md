---
title: Background jobs and schedulers — cron vs queue vs workflow engine
tags: [system-design, scheduling, async, temporal, reliability]
learned: 2026-10-27
---
## TL;DR
Work that does not belong in a request has three homes. A **scheduler** (cron, `@Scheduled`, Kubernetes CronJob) runs something at a time: nightly reports, partition rotation, outbox purges. A **queue with workers** runs something soon and reliably in response to an event: send this SMS, resize this image, retry until it works. A **workflow engine** (Temporal, or a saga orchestrator) runs a multi-step, long-running process with state, timers, retries per step and compensation: provision this eSIM, onboard this subscriber over three days. The choice follows from duration, number of steps, and what must survive a crash. The common failure in all three is **running twice**: on several pods, after a crash, after a redeploy; the fix is a lock or a lease plus idempotent steps.

## Key points

### The three models
| | Scheduler (cron) | Queue + workers | Workflow engine |
|---|---|---|---|
| Trigger | time | event / message | event or API, then its own clock |
| Duration | seconds–minutes, bounded | one unit of work, seconds | minutes to months |
| State between steps | none (re-derive from DB) | per message | durable, in the engine (event history) |
| Retry | next run, or your own loop | redelivery, backoff, DLQ ([[dead-letter-queues-and-retries]]) | per activity policy, timers survive restarts |
| Multi-instance safety | needs a lock (ShedLock, DB `pg_advisory_lock`, leader election) | partitions / visibility timeout | engine's task queues |
| Observability | logs, last-run metric | lag, DLQ size | per-workflow history and UI |
| Examples | partition drop, report, reconciliation, token refresh | SMS sending, thumbnails, webhook fan-out | eSIM provisioning, order fulfilment, payment with 3-DS, subscriber onboarding |

### Schedulers
- `@Scheduled(cron = "0 5 2 * * *")` in Spring is the simplest; with two pods it runs twice. Options: ShedLock (a lock row in Postgres with a lease), `SELECT pg_try_advisory_lock(hashtext('nightly-report'))`, or run the job as a Kubernetes **CronJob** with `concurrencyPolicy: Forbid` in its own pod so the API deployment stays stateless ([[kubernetes-basics]], [[horizontal-vs-vertical-scaling]]).
- Make every scheduled job **idempotent and resumable**: it will be run twice (lock expiry, manual re-run) and it will be killed halfway (deploy). Process in batches with a watermark (`WHERE created_at > :last_processed`) and commit the watermark with the batch.
- Jitter and spread: 50 services with `0 0 * * *` hit the database at midnight together. Offset minutes, or derive the schedule from a hash of the job name.
- Missed runs: cron does not catch up after downtime; if the job must not be skipped (billing), record "last successful run" and make the job compute what is overdue.
- Time zones and DST: schedule in UTC unless the business requirement is local time ("send at 09:00 Bishkek"), and say which ([[time-and-clocks]]).

### Queues and workers
- Every unit of work is a message; workers scale horizontally by partition or by competing consumers ([[message-queues-and-streams]], [[async-processing-and-event-driven-scaling]]).
- Delayed jobs ("retry in 10 min", "remind in 24 h"): native in RabbitMQ (delayed exchange), SQS (delay seconds, max 15 min), awkward in Kafka (retry topics with a pause-until-due consumer); for long delays a DB table of due jobs polled by a scheduler (`SELECT ... WHERE run_at <= now() FOR UPDATE SKIP LOCKED`) is honest and simple ([[postgres-locks]]).
- Priority: separate queues per priority with separate worker pools; one queue with priorities starves the low ones under load.
- A job that calls an external provider needs a timeout, bounded retries, a [[circuit-breaker]] and a [[rate-limiting|rate limit]]; otherwise the worker pool becomes a retry storm generator.

### Workflow engines
- When the process has **many steps with state, waits and compensation**, hand-rolled "job + status column + cron that re-drives stuck rows" grows into an unmaintainable state machine. Temporal gives durable execution: the workflow code is replayed from its event history after any crash; `sleep(3 days)` survives a redeploy; each activity has its own retry policy and timeout ([[temporal-concepts]], [[workflow-engines-vs-sagas]]).
- Cost: a cluster to run (or a managed service), deterministic workflow code, versioning of running workflows. Worth it from roughly "three steps, two external systems, needs to survive restarts" upwards ([[design-esim-provisioning]], [[esim-provisioning-platform]]).
- A **saga** via events ([[saga]]) covers the same ground without an engine but spreads the process logic across consumers; fine for 2–3 steps, hard to see and debug beyond that.

### Choosing in an interview
- "Delete expired sessions nightly" → cron with a lock.
- "Send the OTP SMS the user just requested" → queue, p95 under 5 s, retries, DLQ.
- "After payment, activate the eSIM, wait for the device to download the profile (up to 24 h), then notify, else roll back the charge" → workflow engine.
- "Resend the invoice email in a week if unpaid" → due-jobs table or a workflow timer; not cron scanning every invoice hourly.

## Example
A due-jobs table in Postgres and a worker that claims a batch with `SKIP LOCKED`: safe with any number of pods, supports arbitrary delays, and needs no extra infrastructure. Used for "retry provider webhook delivery with backoff" in the notification service; the heavy, multi-step flows go to Temporal.

```sql
CREATE TABLE job (
    id          bigserial PRIMARY KEY,
    type        text        NOT NULL,                  -- 'webhook.deliver', 'invoice.remind'
    payload     jsonb       NOT NULL,
    run_at      timestamptz NOT NULL,
    attempts    int         NOT NULL DEFAULT 0,
    max_attempts int        NOT NULL DEFAULT 8,
    locked_until timestamptz,                           -- lease, survives a worker crash
    status      text        NOT NULL DEFAULT 'PENDING'  -- PENDING | DONE | FAILED
);
CREATE INDEX job_due_idx ON job (run_at) WHERE status = 'PENDING';   -- partial: only the hot set
```

```java
@Component
public class JobWorker {
    private final JdbcClient db; private final Map<String, JobHandler> handlers;
    public JobWorker(JdbcClient db, List<JobHandler> hs) {
        this.db = db; this.handlers = hs.stream().collect(toMap(JobHandler::type, h -> h));
    }

    @Scheduled(fixedDelay = 1000)                       // every pod polls; SKIP LOCKED makes it safe
    public void runBatch() {
        List<Job> batch = db.sql("""
                UPDATE job SET locked_until = now() + interval '2 minutes', attempts = attempts + 1
                WHERE id IN (
                    SELECT id FROM job
                    WHERE status = 'PENDING' AND run_at <= now()
                      AND (locked_until IS NULL OR locked_until < now())
                    ORDER BY run_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 50)
                RETURNING id, type, payload, attempts, max_attempts
                """).query(Job.class).list();
        for (Job job : batch) execute(job);
    }

    private void execute(Job job) {
        try {
            handlers.get(job.type()).handle(job.payload());                      // must be idempotent
            db.sql("UPDATE job SET status = 'DONE', locked_until = NULL WHERE id = :id").param("id", job.id()).update();
        } catch (Exception e) {
            if (job.attempts() >= job.maxAttempts()) {
                db.sql("UPDATE job SET status = 'FAILED', locked_until = NULL WHERE id = :id").param("id", job.id()).update();
                return;                                                          // alert on FAILED count
            }
            long backoffSec = (long) Math.min(3600, Math.pow(2, job.attempts()) * 30) + ThreadLocalRandom.current().nextLong(0, 30);
            db.sql("UPDATE job SET run_at = now() + make_interval(secs => :s), locked_until = NULL WHERE id = :id")
              .param("s", backoffSec).param("id", job.id()).update();
        }
    }
}
```

The lease (`locked_until`) handles a worker dying mid-job: after two minutes another pod picks it up, which is why the handler must tolerate a second execution ([[idempotency]]).

## Pitfalls
- `@Scheduled` on a replicated service without a lock: every report is generated N times and every reminder sent N times.
- Jobs that hold a DB transaction open while calling an external API: the connection pool drains and the API latency becomes the pool's latency ([[hikaricp-pool-sizing]]).
- Retrying forever with a fixed delay: a dead provider gets hammered every 30 s by thousands of jobs; use exponential backoff with jitter and a cap, then fail and alert.
- Building a workflow engine by accident: `status` columns, "stuck job" cron sweepers and manual re-drives are the signs; move the flow to Temporal before it grows ([[workflow-engines-vs-sagas]]).
- Scheduling in the application clock across time zones and DST changes; a 02:30 job vanishes or runs twice once a year.
- Related: [[temporal-concepts]] for durable execution; [[outbox-pattern]] for the relay job that most services run first.
