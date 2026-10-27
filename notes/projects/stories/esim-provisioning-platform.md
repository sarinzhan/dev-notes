---
title: Project story — eSIM provisioning platform at Beeline
tags: [project-story, interview, temporal, kafka, esim]
learned: 2026-10-27
---
## TL;DR
At Beeline Kyrgyzstan I build the backend that lets a subscriber get an eSIM without visiting a shop: order → reserve a profile → talk to the SM-DP+ and entitlement server → activate in billing and HLR → notify the client. It is Java 21 / Spring Boot 3 on PostgreSQL and Kafka, with Temporal orchestrating the long-running flow. My main contribution was making a multi-step flow across unreliable external systems reliable, idempotent and observable.

## Context and goal
- Before: eSIM issuance was manual and operator-driven, and nobody could tell where a stuck activation was.
- Goal: self-service from a Flutter mobile app and web client, every step retried or compensated automatically, full visibility of each order.
- Team: [~4 backend, 1 Flutter/web developer, 1 QA] <!-- fill in real number -->; I write the API tasks for the client side.
- Load: [~5k activations/day] <!-- fill in real number --> with campaign peaks; each activation touches 3–4 external systems with latencies from 200 ms to tens of seconds.

## Architecture
- Spring Boot 3 REST API (orders, status, QR/activation code) behind the corporate gateway; JWT auth ([[jwt-authentication]]).
- PostgreSQL for orders, profiles, and the outbox table; Kafka for domain events consumed by notifications and analytics ([[outbox-pattern]]).
- Temporal workflow per order: activities for `reserveProfile`, `callSmDpPlus`, `entitlement`, `activateBilling`, `activateHlr`, `notify`; each activity has its own retry policy and timeout ([[temporal-concepts]]).
- Hexagonal layout: domain + application services in the core, adapters for SM-DP+, billing, HLR, Kafka and JPA at the edge ([[ddd-basics]]).
- OpenTelemetry tracing across the Flutter app, the API and the Temporal workers through W3C `traceparent` ([[distributed-tracing]]); Grafana/Tempo for lookup by order id.
- Deployed with Docker Compose on company VMs ([[docker-fundamentals]]).

```
Flutter / web ──HTTP──▶ API (Spring) ──start──▶ Temporal server
                           │                         │
                      Postgres (orders,       Worker (activities)
                       outbox) ──relay──▶ Kafka   ├─▶ SM-DP+ (profile)
                                                  ├─▶ Entitlement server
                                                  ├─▶ Billing
                                                  ├─▶ HLR
                                                  └─▶ Notify (push/SMS)
```

## My role and decisions
- Chose Temporal over a hand-written state machine on Kafka: retries, timers, timeouts and history come for free, and "where is order X stuck" is answered by the Temporal UI instead of log archaeology. Evaluated a choreographed [[saga]] first and rejected it for the amount of bookkeeping code.
- Introduced the transactional outbox so order state and Kafka events cannot diverge; wrote the relay with `FOR UPDATE SKIP LOCKED` for two instances.
- Defined idempotency keys for every external call (order id + step), so a retried activity cannot reserve two profiles or bill twice ([[idempotency]]).
- Wrote the API contract and tasks for the Flutter client, including tracing header propagation.
- Set up the testing layers: Mockito for domain rules, Testcontainers for Postgres/Kafka, Temporal's `TestWorkflowEnvironment` with time skipping for workflow logic ([[junit-and-mockito]], [[testcontainers]]).

## Hardest problem and how I solved it
The SM-DP+ call could time out after it had actually reserved the profile. A naive retry reserved a second profile; a naive "give up" left the subscriber with nothing and a leaked profile in the pool. The fix had three parts:
1. Every reserve request carries a deterministic idempotency key (`orderId:reserve`); the adapter queries the profile state by that key before issuing a new reserve.
2. The reserve activity got a short timeout with a bounded retry policy, and a compensating `releaseProfile` activity registered in a saga-style list inside the workflow; if a later step (billing, HLR) fails permanently, compensations run in reverse order.
3. For stuck orders I added heartbeats to long activities and a workflow query `status()` exposed by the API; support sees the current step and last error without reading logs, and one trace id links the mobile request to the HLR call.
A second lesson was determinism: I had put `LocalDateTime.now()` and a random id generator inside workflow code, which broke replay after a deploy. I moved them to activities / `Workflow.sideEffect` and added a replay test to CI.

## What I would do differently
- Start with the hexagonal boundaries from day one; the first version mixed JPA entities into the workflow activities and the refactor was painful.
- Agree on error contracts with the SM-DP+ and billing teams earlier; half of the compensation logic exists because "timeout" and "already done" were indistinguishable.
- Add load tests before the first campaign instead of after it ([~2x] <!-- fill in real number --> peak was found in production).
- Use Kubernetes instead of Compose for the workers; scaling Temporal workers by hand is annoying ([[kubernetes-basics]]).

## Likely interview questions
- Why Temporal and not Kafka plus a state machine? Durable execution, retries, timers and history out of the box; code reads as a sequential flow; the UI answers "where is it stuck".
- How do you guarantee an event is published when the order is saved? Transactional outbox in the same Postgres transaction, relay to Kafka, at-least-once plus idempotent consumers.
- What does idempotency mean in your flow? Each external call has a key derived from order id and step; a retry queries or replays instead of acting twice.
- What is determinism in Temporal and how did you break it? Workflow code must produce the same commands on replay; I used `now()` and random inside the workflow; fixed with activities and `sideEffect`.
- How do you compensate a failed step? Register undo actions as steps succeed; on permanent failure run them in reverse (release profile, cancel billing line).
- How do you trace one activation end to end? W3C `traceparent` from Flutter through the API into Temporal headers; OTel Collector to Tempo; search by order id attribute.
- How do you test a workflow? `TestWorkflowEnvironment` with mocked activities and time skipping; Testcontainers for adapters; replay test against production histories.
- What was the biggest production incident? Duplicate profile reservation after SM-DP+ timeouts; fixed by idempotency keys and compensation.

## Related notes
- [[temporal-concepts]], [[saga]], [[outbox-pattern]], [[idempotency]]
- [[distributed-tracing]], [[otel-metric-types]], [[circuit-breaker]]
- [[ddd-basics]], [[kafka-producer]], [[spring-kafka-consumer]], [[testcontainers]]
