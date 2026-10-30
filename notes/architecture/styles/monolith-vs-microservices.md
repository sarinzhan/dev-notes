---
title: Monolith vs microservices — trade-offs, when to split, strangler fig
tags: [architecture, microservices, monolith, distributed-systems, migration]
learned: 2026-10-27
---
## TL;DR
A monolith is one deployable with local calls and one transaction; microservices are many deployables with network calls and no shared transaction. Microservices buy independent deployment, scaling and team ownership at the price of distributed data consistency, operational cost and latency. Split when the organisation needs it — several teams, different release cadences, uneven scaling — not because the architecture is fashionable. Split badly and you get a **distributed monolith**; split safely with the **strangler fig**: route one capability at a time to a new service behind a gateway until the old system is empty.

## The problem it solves
- **Without a deliberate choice:** a team splits into services because it is fashionable and gets a distributed monolith (a shared database, joint deploys, network latency, no ACID), or stays in one monolith where three teams block each other's releases and the SM-DP+ polling worker starves the admin API's connection pool.
- **What choosing well changes:** the cost of distribution is paid only where the organisation needs it (separate teams, release cadences, uneven scaling), and local calls and transactions stay everywhere else.
- **When it doesn't matter:** one team of 5–8 on one product with uniform load; a modular monolith is the answer and the debate can wait.

## Key points
### Trade-offs
| Concern | Monolith (incl. [[modular-monolith]]) | Microservices |
|---|---|---|
| Deploy | One artifact, one pipeline; any change ships everything | Each service ships on its own; many pipelines, versioned contracts |
| Scaling | Whole process scales as a unit; the hot path drags the rest | Scale the hot service alone (Kafka consumers, provisioning workers) |
| Team ownership | Shared codebase; conventions and reviews keep order | Service per team; clear ownership, harder cross-team changes |
| Data consistency | ACID in one database ([[transactions-acid-and-isolation]]) | Database per service; eventual consistency, [[saga]], [[outbox-pattern]], [[idempotency]] |
| Ops cost | One log, one JVM to tune, one DB to back up | Orchestration ([[kubernetes-basics]]), [[distributed-tracing]], [[logging-stacks]], per-service alerts, secrets, network policies |
| Latency | In-process method calls, microseconds | Network hops, serialisation, retries; tail latency adds up across a call chain |
| Failure modes | Process crash takes everything down | Partial failures, timeouts, cascading overload ([[circuit-breaker]]) |
| Testing | Fast unit tests, one integration suite | Contract tests, [[testcontainers]] per service, slow end-to-end environments |

### When to split
- **Team size**: one team of 5–8 rarely benefits; with three or more teams in one repository, merge conflicts and release coordination cost more than network calls do.
- **Independent scaling**: the SM-DP+ polling worker needs 20 instances while the admin API needs 2, and they compete for one connection pool ([[hikaricp-pool-sizing]]).
- **Different release cadence**: a billing module that changes quarterly and goes through audit, next to a notification module that ships daily.
- **Failure isolation or different runtime**: a third-party integration that must not take the core down, a CPU-heavy job, another language.
- Microservices do not make bad code good; extract a module only after its boundary already exists in the monolith.

### Distributed monolith anti-pattern
- Services that must deploy together, share a database, call each other synchronously in long chains, and share a `common` library whose every change forces a global release.
- Symptoms: one feature change touches three repositories; an outage in a minor service blocks login; integration tests need the whole system running.
- Causes: splitting by technical layer instead of by business capability ([[ddd-basics|bounded contexts]]), sharing tables, treating REST calls as method calls.
- Cure: merge back into a modular monolith, or redraw boundaries around capabilities with data ownership and asynchronous events.

### Strangler fig migration
- Put a gateway in front of the monolith ([[api-gateway-and-service-discovery]]) so all traffic enters through one facade.
- Pick one capability with a clear boundary and few dependencies (notifications, referral, reporting). Build it as a service, move its data, switch the gateway route for that path only, keep a way to route back.
- Writes during migration: let the new service own the data and have the monolith call it, or feed the new store from the monolith's outbox or change-data-capture stream; avoid naked dual writes.
- Repeat capability by capability; each step is reversible and the business keeps shipping. Set up [[distributed-tracing]] across gateway, monolith and new service before the first route switch.

## Example
Decision checklist, then the gateway route that sends one path to the new service.

| Question | Points towards split if |
|---|---|
| How many teams change this code? | More than two, with separate backlogs |
| Does one part need 5x the instances of the rest? | Yes, and it starves the shared pool |
| Do parts ship on different cadences or audit rules? | Yes |
| Is there a clear bounded context with its own data? | Yes, and no cross-module joins |
| Can the call be asynchronous or tolerate eventual consistency? | Yes |
| Is there tracing, central logging and a gateway already? | Yes; otherwise build these first |
| Is the pain real today? | Measured in merge conflicts, release delays or incidents |

```yaml
# Spring Cloud Gateway (Spring Boot 3.x): /api/referrals/** goes to the new service,
# everything else still hits the monolith.
spring:
  cloud:
    gateway:
      routes:
        - id: referral-service
          uri: lb://referral-service          # resolved through service discovery
          predicates:
            - Path=/api/referrals/**
        - id: monolith
          uri: http://esim-monolith:8080
          predicates:
            - Path=/**
```

Routes are evaluated in order, so the specific route sits first; rollback is deleting one route.

## Pitfalls
- Splitting before the boundary is clear: the new service reads the monolith's database "temporarily" and the coupling lasts for years.
- Synchronous chains (gateway → A → B → C) multiply latency and failure probability: four 99.9% services in a row give roughly 99.6%. Prefer events where the flow allows it.
- Dual writes without an outbox or reconciliation create data drift between the old and the new store that nobody notices until a customer complains ([[outbox-pattern]]).
- Shared libraries carrying domain models between services recreate the monolith's coupling plus a network; share contracts ([[openapi-swagger]], schemas), not entities.
- Related: [[modular-monolith]] (the usual right answer first), [[saga]], [[api-gateway-and-service-discovery]], [[distributed-tracing]], [[kubernetes-basics]], [[system-design-basics]].
