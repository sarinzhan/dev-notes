---
title: API gateway, service discovery and config server
tags: [microservices, spring-cloud, networking, kubernetes]
learned: 2026-10-27
---
## TL;DR
An **API gateway** is the single entry point for clients: it routes requests to internal services and handles the cross-cutting work (auth, rate limiting, TLS, aggregation) so services do not repeat it. **Service discovery** answers "where is `payment-service` right now" when instances come and go: either the client asks a registry (Eureka/Consul) or the platform hides it behind a stable DNS name (Kubernetes Service). A **config server** centralises configuration. All three are plumbing that a small team may not need — a modular monolith is often the right first answer.

## Key points

### Gateway responsibilities
- **Routing**: path/host/header → service (`/api/esim/**` → `esim-service`), with path rewriting and versioning.
- **Authentication and authorization at the edge**: validate the [[jwt-authentication|JWT]] once, pass a trusted header or the token downstream; services still check scopes.
- **Rate limiting and quotas** per client/API key (token bucket in Redis — see [[redis-data-structures]]).
- **TLS termination**: HTTPS to the outside, plain HTTP or mTLS inside the cluster.
- **Resilience**: timeouts, retries, [[circuit-breaker]] per route, so one slow service does not pull the gateway down.
- **Aggregation / BFF**: one mobile request fans out to several services and combines the result (backend-for-frontend, one gateway per client type).
- Also: request logging, [[distributed-tracing|trace header]] injection, CORS, response caching, API docs ([[openapi-swagger]]) exposure.
- Options: Spring Cloud Gateway (reactive, Java), Kong/Envoy/NGINX/Traefik (infra-level), cloud gateways. Do **not** put business logic in the gateway.

### Spring Cloud Gateway basics
- Built on Spring WebFlux + Netty (reactive, non-blocking); since Spring Cloud 2023/2024 there is also a Servlet variant (`spring-cloud-starter-gateway-mvc`) for teams on Spring MVC.
- A route = `id` + `uri` + **predicates** (Path, Method, Header, Host, Query, Weight) + **filters** (RewritePath, StripPrefix, AddRequestHeader, RequestRateLimiter, Retry, CircuitBreaker, TokenRelay).
- `uri: lb://esim-service` uses the Spring Cloud LoadBalancer with discovery; `uri: http://esim-service:8080` uses platform DNS.
- Global filters apply to all routes (auth check, correlation id).

### Service discovery
| | Client-side | Server-side |
|---|---|---|
| How | Client queries a registry (Eureka, Consul, ZooKeeper), picks an instance, calls it | Client calls a stable address; a proxy/DNS resolves to an instance |
| Load balancing | In the client (Spring Cloud LoadBalancer: round-robin, random, zone-aware) | In the platform (Kubernetes `Service` + kube-proxy/IPVS, cloud LB, Envoy sidecar) |
| Registration | Service self-registers + heartbeats | Platform registers from pod readiness |
| Fits | Non-Kubernetes deployments, JVM-only estates | Kubernetes, Swarm, service meshes |

- On Kubernetes, `http://payment-service.default.svc.cluster.local` is discovery; Eureka is redundant there ([[kubernetes-basics]]). Docker Swarm does the same with its internal DNS and routing mesh ([[docker-swarm]]).
- Health: a registry removes instances that miss heartbeats; Kubernetes removes pods failing the readiness probe. Both rely on the service exposing an honest health endpoint (Actuator `/actuator/health/readiness`).

### Config server
- Spring Cloud Config Server serves `application-{profile}.yml` from Git/Vault; clients fetch at startup (`spring.config.import=configserver:`) and can refresh with `@RefreshScope` + `/actuator/refresh` or Spring Cloud Bus.
- Kubernetes alternative: ConfigMap + Secret mounted as files or env vars, which Spring Boot reads via `spring.config.import=kubernetes:` or plain volumes ([[spring-boot-properties-and-profiles]]).
- Secrets belong in Vault / sealed secrets, never in Git, even private Git.

### When a monolith is the right answer
- Microservices pay off with many teams, independent deploy cadence, and different scaling profiles. With one team of 3–8 people, a **modular monolith** (clear module boundaries, one deployable, one DB with schemas per module — [[ddd-basics]]) avoids the network, discovery, tracing, saga and gateway tax.
- Signals that you need to split: independent release cycles blocked by one build, a hot module needing 10× the replicas, a different runtime requirement (ML in Python), compliance isolation (card data under PCI DSS).
- Extract one service at a time (strangler pattern) behind the gateway; the gateway hides the migration from clients.

## Example
Gateway routing with JWT relay, rate limiting backed by Redis, and a breaker on the provisioning route.

```yaml
spring:
  cloud:
    gateway:
      default-filters:
        - TokenRelay=
        - AddRequestHeader=X-Gateway, edge-1
      routes:
        - id: esim
          uri: lb://esim-service
          predicates:
            - Path=/api/esim/**
          filters:
            - StripPrefix=1
            - name: RequestRateLimiter
              args:
                redis-rate-limiter.replenishRate: 50
                redis-rate-limiter.burstCapacity: 100
                key-resolver: "#{@apiKeyResolver}"
            - name: CircuitBreaker
              args:
                name: esimCb
                fallbackUri: forward:/fallback/esim
        - id: payments
          uri: lb://payment-service
          predicates:
            - Path=/api/payments/**
            - Method=POST,GET
          filters:
            - RewritePath=/api/payments/(?<rest>.*), /v2/$\{rest}
            - name: Retry
              args:
                retries: 2
                methods: GET
                backoff: { firstBackoff: 100ms, maxBackoff: 1s, factor: 2 }
  security:
    oauth2:
      resourceserver:
        jwt:
          issuer-uri: https://sso.example.kg/realms/beeline
```

## Pitfalls
- Gateway as a single point of failure: run ≥ 2 replicas behind the platform load balancer and keep it stateless.
- Mixing blocking code (JDBC, `RestTemplate`) into a reactive gateway filter blocks Netty event loops and kills throughput; use `WebClient` ([[spring-reactive-webclient-sse]]) or the MVC gateway.
- Retrying POST at the gateway duplicates writes unless the service is [[idempotency|idempotent]].
- Eureka plus Kubernetes gives two sources of truth about instance health; pick one.
- A "microservice" architecture where every request crosses 6 services synchronously is a distributed monolith with worse latency; prefer async events ([[kafka-architecture]], [[outbox-pattern]]) between contexts.
- Related: [[rest-and-http]], [[spring-security-basics]], [[circuit-breaker]], [[kubernetes-basics]], [[system-design-basics]].
