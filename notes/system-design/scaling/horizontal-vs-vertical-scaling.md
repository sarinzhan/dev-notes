---
title: Horizontal vs vertical scaling and stateless services
tags: [system-design, scaling, stateless, kubernetes]
learned: 2026-10-27
---
## TL;DR
Vertical scaling means a bigger machine: simple, no code changes, but there is a ceiling, a price curve that bends upward, and a single box that can fail. Horizontal scaling means more machines behind a [[load-balancing|load balancer]]: unbounded in theory, cheaper per unit of capacity, fault tolerant, but only if the service is **stateless**, so that any replica can serve any request. The real work of "scaling out" is moving state (sessions, uploads, caches, locks, scheduled jobs) out of the process into shared infrastructure.

## Key points

### The two directions compared
| | Vertical (scale up) | Horizontal (scale out) |
|---|---|---|
| How | more CPU / RAM / faster disk on one node | more replicas of the same node |
| Code changes | none | service must be stateless; shared state externalised |
| Limit | biggest instance you can buy | coordination overhead, shared DB becomes the bottleneck |
| Availability | single point of failure, restart = downtime | N-1 replicas keep serving; rolling deploys |
| Cost curve | super-linear at the high end | roughly linear, plus LB and orchestration |
| Typical first use | databases, Redis, Kafka brokers (stateful) | API services, workers, consumers |

- The honest first step is almost always vertical for the database and horizontal for the application tier. Postgres scales up a long way (dozens of cores, hundreds of GB RAM, NVMe) before read replicas or sharding are justified ([[database-scaling-path]]).
- Horizontal scaling of stateful components is a different problem: replication and partitioning ([[leader-follower-replication]], [[sharding-strategies]]), not "add a pod".

### What makes a service stateless
- No request-scoped data survives in the process between requests: no `HashMap<SessionId, …>` singletons, no in-memory rate-limit counters, no "already seen" sets.
- Every replica can be killed at any moment without losing work: in-flight requests fail fast and the client or queue retries ([[idempotency]]).
- Deployments are interchangeable: configuration via environment and config server, not files edited on the box ([[spring-boot-properties-and-profiles]]).

### Session externalisation
- Classic Java web apps kept `HttpSession` in the servlet container, so a second instance needed sticky sessions or session replication. Both are fragile: sticky sessions break on scale-in, replication is chatty.
- Options today:
  - **Stateless tokens**: a signed [[jwt-authentication|JWT]] carries identity; the server verifies the signature and stores nothing. Logout and revocation need a short TTL plus a denylist.
  - **Shared session store**: Spring Session with Redis stores the session by id; the cookie holds only the id ([[redis-data-structures]]). Any replica can load it.
- Everything else that used to live "in the app" moves out the same way: uploaded files to object storage ([[file-and-blob-storage]]), caches to Redis ([[caching]]), locks to the database or a coordination service ([[distributed-locks-and-fencing]]), scheduled jobs to a single leader or a queue ([[background-jobs-and-schedulers]]).

### Scaling the application tier in practice
- Behind the LB, replicas register through service discovery ([[api-gateway-and-service-discovery]]); Kubernetes does this with a `Service` and scales with an HPA on CPU or a custom metric such as queue lag ([[kubernetes-basics]]).
- Thread-per-request services are bounded by threads × latency; Little's law says concurrency = throughput × latency ([[amdahls-law-and-parallel-speedup]]). Cutting downstream latency is often cheaper than adding replicas, and [[virtual-threads]] remove the thread ceiling for I/O-bound services.
- Each replica opens its own DB pool. 20 replicas × 10 connections = 200 server connections; size pools with the total in mind or add PgBouncer ([[hikaricp-pool-sizing]]).
- Consumers scale horizontally only up to the number of Kafka partitions ([[kafka-consumer]]); more replicas than partitions sit idle.

### Interview angles
- "Your service is slow at peak, what do you do?" Measure first (where is the time: CPU, DB, external call?). If CPU-bound and stateless: scale out. If DB-bound: scaling the app tier makes it worse, because more replicas mean more connections hammering the same primary.
- "Why not just scale vertically?" Ceiling, cost curve, downtime for resize, single point of failure; but it is the right answer for a stateful component below its ceiling.
- "What breaks when you go from one instance to two?" In-memory caches diverge, `@Scheduled` jobs run twice, local file uploads disappear, rate limits are per-instance, sessions are lost on the other instance.

## Example
Spring Session on Redis turns a session-based service into a stateless one without touching controllers:

```yaml
# application.yml
spring:
  session:
    store-type: redis
    timeout: 30m
  data:
    redis:
      host: redis
      port: 6379
server:
  servlet:
    session:
      cookie:
        http-only: true
        secure: true
        same-site: lax
```

```java
@Configuration
@EnableRedisHttpSession
public class SessionConfig {
    // the cookie keeps only the session id; attributes live in Redis as a hash,
    // so a request can land on any replica behind the load balancer
}
```

With this in place, `kubectl scale deployment api --replicas=6` is a safe operation: the LB spreads traffic, each pod loads the session from Redis, and a pod restart logs nobody out.

## Pitfalls
- "Stateless" services that still use `@Scheduled` without a distributed lock (ShedLock, DB advisory lock) run the job once per replica; the outbox relay note shows the `SKIP LOCKED` way to make that safe ([[outbox-pattern]]).
- A local in-process cache (Caffeine) on N replicas gives N different views of the data; acceptable for reference data with a TTL, wrong for anything users can edit ([[distributed-cache-coherence]]).
- Scaling out the app tier before the database can take the load: the first symptom is pool exhaustion and `too many connections`.
- Sticky sessions hide the problem until the first scale-in or deploy.
- Related: [[load-balancing]], [[database-scaling-path]], [[read-write-splitting-and-cqrs-lite]].
