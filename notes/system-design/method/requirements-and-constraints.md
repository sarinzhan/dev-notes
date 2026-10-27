---
title: Requirements and constraints — what to ask before drawing boxes
tags: [system-design, interview, slo, requirements]
learned: 2026-10-27
---
## TL;DR
The first five minutes of a design decide which design is correct. Separate **functional** requirements (what the system does) from **non-functional** ones (how well: scale, latency, availability, durability, consistency, cost) and write both down as numbers, not adjectives. Turn vague words into SLOs ("p99 < 300 ms for `POST /notifications`", "99.9 % monthly availability"), then explicitly list what is **out of scope**. Every later trade-off (cache or not, sync or async, one region or two) is justified by pointing back at this list.

## Key points

### Functional requirements
- Core use cases as verbs with actors: "a backend service sends a notification to a subscriber", "a subscriber reads their history". Three to five of them, no more; the rest goes to "out of scope".
- Ask the clarifying questions interviewers expect:
  - Who are the clients: mobile app, internal services, partners? (decides auth, [[api-design-for-system-design|API style]], rate limits)
  - Read/write ratio and the typical payload size. A feed is 100:1 reads; an audit log is 1000:1 writes.
  - Is ordering required, and per what key (per user, per account, global)?
  - Are duplicates acceptable? "Never bill twice" and "may show a post twice" lead to different designs ([[idempotency]]).
  - How long is data kept, and who can delete it (GDPR, data residency)?

### Non-functional requirements as numbers
| Vague word | Ask for | Example answer |
|---|---|---|
| "fast" | latency percentile per endpoint | p50 50 ms, p99 300 ms |
| "scalable" | DAU, QPS average and peak, growth per year | 5 M DAU, 1 000 rps peak, ×2/year |
| "reliable" | availability SLO and error budget | 99.9 % → 43 min downtime/month |
| "consistent" | which reads must see the latest write | balance: strong; history: eventual |
| "durable" | acceptable data loss (RPO) and recovery time (RTO) | RPO 0 for payments, RTO 15 min |
| "cheap" | budget per month, infra constraints | one cloud region, managed Postgres |

- **Availability vs consistency**: ask which one wins under failure ([[cap-and-pacelc]], [[consistency-models]]). A system that returns a stale balance is "available"; a bank will not accept that.
- **Latency budget**: split the p99 across hops. 300 ms total for a request that calls two services and a DB leaves ~80 ms per hop; that already rules out a cross-region call ([[latency-numbers]]).
- **Tenancy and isolation**: a noisy tenant must not starve others; this is where [[rate-limiting]] and per-tenant quotas appear ([[multi-tenancy-strategies]]).

### SLOs, SLIs and error budgets
- An **SLI** is what you measure (fraction of requests under 300 ms and non-5xx), the **SLO** is the target (99.9 % over 30 days), the **error budget** is `1 − SLO` spent on deploys, incidents and experiments. Details live in [[slo-sli-and-alerting]].
- Pick SLOs that the architecture can actually meet. Four nines (52 min/year) with a single Postgres and manual failover is a lie; it needs automated failover ([[postgres-replication-and-failover]]) and probably two availability zones.
- State consistency requirements per operation, not per system: "write path strong, analytics eventual" is normal.

### Constraints
- **Existing landscape**: what the team already runs (Kafka, Postgres, Temporal, Redis) is a constraint and a hint; introducing Cassandra for one table is a smell.
- **Team size and ops maturity**: two engineers cannot operate five stateful systems. Prefer managed services or fewer moving parts ([[monolith-vs-microservices]]).
- **Regulation**: PII location, retention, audit trail; payments add PCI-DSS style rules ([[design-payment-processing]]).
- **Compatibility**: external providers with fixed SLAs and rate limits (an SMS gateway allowing 100 msg/s caps your throughput no matter how you scale).

### Writing it down
Spend 30 seconds producing a visible checklist. It shows the interviewer that the rest of the design is deliberate, and it becomes the test for every box you add later: "which requirement does this cache serve?" If none, remove it. The next step is [[back-of-envelope-estimation]].

## Example
A requirements block for a notification service, as it would appear in a design doc or at the top of an interview whiteboard.

```yaml
functional:
  - internal services send a notification to a subscriber via push, sms, email
  - subscriber reads last 90 days of history
  - subscriber sets per-channel preferences and quiet hours
out_of_scope:
  - marketing campaigns to millions at once (separate batch system)
  - rich content editor, A/B testing of templates

non_functional:
  scale:        { dau: 5_000_000, notifications_per_day: 20_000_000, peak_rps: 1_000 }
  latency:      { api_accept_p99_ms: 200, delivery_p95_s: 5, history_read_p99_ms: 300 }
  availability: { api_slo: "99.9%", error_budget_min_per_month: 43 }
  durability:   { accepted_notification_lost: never, rpo: 0, rto_min: 15 }
  consistency:  { status_read_after_write: eventual (<= 2 s), preferences: read-your-writes }
  ordering:     per subscriber, per channel
  duplicates:   at most one delivery per (notificationId, channel) despite retries
  retention:    { history_days: 90, pii_region: "KG only" }

constraints:
  - existing stack: Spring Boot, Postgres 17, Kafka 4, Redis, Temporal
  - sms provider: 100 msg/s hard limit, 2 s p99, HTTP webhooks for receipts
  - two engineers on call, one cloud region, two availability zones
```

Every line maps to a later decision: "accepted notification lost: never" forces a durable write before `202` and the [[outbox-pattern]]; "100 msg/s" forces a queue and per-provider [[rate-limiting]]; "per subscriber ordering" picks `userId` as the Kafka key ([[message-queues-and-streams]]).

## Pitfalls
- Designing for the imagined scale, not the stated one. Ask the number; a 50 rps service does not need sharding ([[database-scaling-path]]).
- Treating "highly available" as a checkbox instead of a number with a monthly minute budget; the budget decides whether multi-AZ or [[multi-region-basics|multi-region]] is needed.
- Forgetting the external dependency's limits: your p99 cannot be lower than the provider's p99 plus a retry ([[failure-modes-and-timeouts]]).
- Mixing consistency levels: declaring the whole system "strongly consistent" then adding a cache and read replicas that break read-your-writes ([[read-replicas-and-stale-reads]]).
- Skipping "out of scope": the interviewer will pull you into feature creep and the deep dive never happens.
- Related: [[system-design-basics]] for the full framework; [[slo-sli-and-alerting]] for turning SLOs into alerts.
