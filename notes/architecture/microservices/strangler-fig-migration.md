---
title: Strangler fig migration — decomposing a monolith incrementally
tags: [microservices, migration, monolith, architecture]
learned: 2026-10-27
---
## TL;DR
The strangler fig pattern replaces a legacy system **one capability at a time**: put a routing facade (gateway, proxy) in front of the monolith, build the new implementation of one capability beside it, switch the route, repeat until the old system is empty and can be deleted. Nothing is rewritten big-bang, every step is reversible by flipping the route back, and the business keeps shipping. The hard parts are not the code but the **data**: who owns the table during the transition, and how both sides stay consistent.

## Key points

### Why not a rewrite
- A full rewrite freezes features for months, re-implements undocumented behaviour from memory, and goes live with one giant cutover that cannot be rolled back. Most rewrites of systems older than a few years are abandoned or ship late.
- Strangling delivers value per slice, keeps the old system as the oracle for behaviour (compare outputs), and lets the team learn the new platform on a low-risk capability first.

### The loop per capability
1. **Pick a seam**: a capability with clear inputs/outputs and few shared tables, ideally a [[bounded-contexts-and-context-mapping|bounded context]] already visible in the code (notifications, PDF export, tariff lookup). Start with something valuable but not the most critical.
2. **Intercept**: route the traffic through a facade: an [[api-gateway-and-service-discovery|API gateway]], nginx ([[reverse-proxy-nginx-basics]]), or a feature flag inside the monolith for internal calls. Events from the monolith can be captured with CDC (Debezium on its tables) when it has no outbox.
3. **Build and shadow**: implement the new service; run it in shadow mode (same request to both, compare results, serve the old one) or dark-launch for internal users.
4. **Switch** by route, by percentage, by tenant. Keep the old path alive and the switch instant.
5. **Retire**: delete the old code and tables once nothing calls them; measure with access logs and [[grafana-dashboards-and-red-use|dashboards]], not with beliefs.

### Data during the transition
| Strategy | How | Trade-off |
|---|---|---|
| **Database-per-capability from day one** | New service owns its tables; the monolith calls it or consumes its events | Cleanest; requires finding every monolith read of those tables first |
| **Shared database, temporary** | New service reads/writes the monolith's tables | Fast start; the schema is now a contract between two deployables ([[coupling-cohesion-and-general-principles|common coupling]]) |
| **Dual write with reconciliation** | Monolith writes to its table and publishes; new service keeps its copy | Classic dual-write hazard; needs the [[outbox-pattern]] or CDC plus a reconciliation job |
| **Change data capture** | Debezium streams the monolith's table changes to the new service | No monolith code changes; the new service is downstream and eventually consistent |
- Direction of ownership: pick a moment when the **new service becomes the writer** and the monolith becomes a reader (through the API or a replicated view). Before that moment the monolith is the source of truth; never have two writers for one fact.
- Expand/contract on the schema for columns both sides touch ([[schema-migrations-flyway-liquibase]]).

### Inside the monolith first
- Many "monolith to microservices" projects should stop at a [[modular-monolith]]: enforce module boundaries (Spring Modulith, ArchUnit), replace direct calls with interfaces/events, and only then extract modules that need independent scaling or ownership ([[monolith-vs-microservices]]).
- The branch-by-abstraction technique is the in-process strangler: introduce an interface, keep the old implementation, add the new one, switch with a flag, delete the old.

### Interview talking points
- Order of extraction: by business value and by coupling (least coupled first), not by "the thing I dislike most".
- Rollback story: every switch is a config change; the old path is deleted only after a soak period.
- Observability before migration: you need baseline latency and error rates to know the new path is not worse ([[slo-sli-and-alerting]]).
- Avoiding the distributed monolith: if the extracted service needs a synchronous call back into the monolith for every request, the seam was wrong.

## Example
Gateway-level strangling with Spring Cloud Gateway: the notification endpoints move to the new service for 10 % of traffic, everything else still goes to the monolith; a header forces the new path for testers.

```yaml
spring:
  cloud:
    gateway:
      routes:
        - id: notifications-new-forced          # testers and canary clients
          uri: http://notification-service:8080
          predicates:
            - Path=/api/notifications/**
            - Header=X-Strangler, new
        - id: notifications-new-weighted
          uri: http://notification-service:8080
          predicates:
            - Path=/api/notifications/**
            - Weight=notifications, 10         # 10 % of the remaining traffic
        - id: notifications-legacy
          uri: http://legacy-monolith:8080
          predicates:
            - Path=/api/notifications/**
            - Weight=notifications, 90
        - id: everything-else
          uri: http://legacy-monolith:8080
          predicates:
            - Path=/**
      default-filters:
        - AddRequestHeader=X-Via-Gateway, true
```

Raising the weight to 100 and later deleting the legacy routes is the "strangle"; each step is a config deploy and reversible in seconds. Compare both paths in Grafana by the `route_id` tag Spring Cloud Gateway adds to its metrics.

## Pitfalls
- Shared database "temporarily" for two years: nobody can change the schema because the other side might read it. Set a date and a plan for the handover of ownership.
- Extracting by technical layer (an "entity service" that exposes CRUD on the monolith's tables): every business flow now makes ten network calls. Extract by capability.
- No facade: callers hard-code the monolith's URL, so switching means touching every client. Put the gateway in place before extracting anything.
- Dual-writing from the monolith with a plain `kafka.send()`: lost or phantom events ([[outbox-pattern]]), and the new service's copy drifts; reconcile daily until the old writer is retired.
- Related: [[monolith-vs-microservices]], [[modular-monolith]], [[api-gateway-and-service-discovery]], [[event-driven-architecture]].
