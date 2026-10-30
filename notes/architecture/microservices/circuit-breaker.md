---
title: Circuit breaker, retry, bulkhead — Resilience4j in Spring Boot
tags: [microservices, resilience, resilience4j, spring-boot]
learned: 2026-10-27
---
## TL;DR
A **circuit breaker** wraps a remote call and tracks its failure rate in a sliding window. When the rate exceeds a threshold, the breaker **opens** and fails calls instantly instead of waiting on a dead dependency; after a pause it lets a few probe calls through (**half-open**) and closes again if they succeed. Resilience4j 2.x provides breaker, retry, time limiter, bulkhead and rate limiter as annotations in Spring Boot 3. Order of defence: **timeout first, then retry with backoff, then breaker**, and only retry what is [[idempotency|idempotent]].

## The problem it solves
- **Circuit breaker:** without it, when the SM-DP+ hangs, every provisioning request waits for the full timeout, threads pile up and the service goes down together with its dependency → it fails fast (or returns a fallback such as "queued") and gives the dependency time to recover.
- **Retry:** without it, one dropped connection or a 503 fails an eSIM download that would succeed a second later → it repeats transient failures with backoff and jitter.
- **Bulkhead:** without it, one slow partner takes all Tomcat threads and unrelated endpoints stop answering too → it caps concurrent calls per dependency.
- **When you don't need it:** calls that cannot hang for long (local DB with pool and query timeouts) or low-traffic internal tools, where a timeout alone is enough. Retry is wrong for non-idempotent writes without an idempotency key.

## Key points

### States
- **CLOSED** — calls pass; the breaker records success/failure/slow in a sliding window.
- **OPEN** — every call fails immediately with `CallNotPermittedException` (or runs the fallback). Lasts `waitDurationInOpenState`.
- **HALF_OPEN** — `permittedNumberOfCallsInHalfOpenState` probe calls go through; if their failure rate is below the threshold → CLOSED, otherwise → OPEN again.
- Sliding window: `COUNT_BASED` (last N calls) or `TIME_BASED` (last N seconds). `minimumNumberOfCalls` prevents one failure out of two calls from opening the breaker. `slowCallRateThreshold` + `slowCallDurationThreshold` treat slow responses as failures — a dependency that answers in 10 s is as bad as one that is down.
- `recordExceptions` / `ignoreExceptions`: a 4xx validation error from the dependency is not a dependency failure and should not count.

### Resilience4j annotations (Spring Boot 3, `resilience4j-spring-boot3`)
| Annotation | Purpose | Key config |
|---|---|---|
| `@CircuitBreaker(name, fallbackMethod)` | Fail fast on a sick dependency | window, thresholds, wait in open |
| `@Retry(name, fallbackMethod)` | Re-run transient failures | `maxAttempts`, `waitDuration`, `enableExponentialBackoff`, `enableRandomizedWait` |
| `@TimeLimiter(name)` | Cap call duration; method must return `CompletableFuture` | `timeoutDuration`, `cancelRunningFuture` |
| `@Bulkhead(name, type)` | Limit concurrent calls (`SEMAPHORE`) or use a separate thread pool (`THREADPOOL`) | `maxConcurrentCalls`, `maxWaitDuration` |
| `@RateLimiter(name)` | Cap calls per period toward a dependency | `limitForPeriod`, `limitRefreshPeriod` |

- Default aspect order when several annotations sit on one method: `Retry` outermost, then `CircuitBreaker`, `RateLimiter`, `TimeLimiter`, `Bulkhead` innermost. Change it with `resilience4j.retry.retryAspectOrder` etc. Retry *outside* the breaker means each retry is a breaker call — usually what you want.
- Annotations are implemented with [[spring-aop]] proxies: self-invocation inside the same bean bypasses them.
- Fallback method must have the same signature plus an exception parameter; it can return a cached/default value or rethrow a domain exception.
- Metrics: every instance exposes `resilience4j_circuitbreaker_state`, `..._calls`, etc. via Micrometer → Prometheus; alert on `state == open` ([[otel-metric-types]]).

### Retry done right
- Retry only transient errors: timeouts, 503, connection reset, Kafka `RetriableException`. Never retry 400/404/409 or business failures.
- **Exponential backoff + jitter** (`enableRandomizedWait`): without jitter all clients retry at the same instant and create a **retry storm** that keeps the dependency down.
- Budget retries: 3 attempts × 2 s timeout = 6 s of blocked thread; make sure the caller's own timeout is longer, or the retries are wasted.
- A retried request may have already succeeded on the server (the response got lost). The operation must be idempotent or carry an idempotency key — see [[idempotency]].

### Bulkhead and timeouts
- Timeouts protect threads; without a timeout the breaker never sees a failure, just hung threads. Set connect and read timeouts on the HTTP client ([[rest-clients]]) and on JDBC ([[hikaricp-pool-sizing|HikariCP]]) before adding any resilience annotation.
- With [[virtual-threads]] a semaphore bulkhead is still useful to bound load on the partner.

## Example
Calling the SM-DP+ (eSIM profile server) with timeout, retry with jittered backoff, and a breaker that falls back to a "queued" status.

```yaml
resilience4j:
  circuitbreaker:
    instances:
      smdp:
        slidingWindowType: COUNT_BASED
        slidingWindowSize: 20
        minimumNumberOfCalls: 10
        failureRateThreshold: 50
        slowCallDurationThreshold: 2s
        slowCallRateThreshold: 80
        waitDurationInOpenState: 30s
        permittedNumberOfCallsInHalfOpenState: 3
        automaticTransitionFromOpenToHalfOpenEnabled: true
        ignoreExceptions: [com.example.esim.ProfileNotFoundException]
  retry:
    instances:
      smdp:
        maxAttempts: 3
        waitDuration: 500ms
        enableExponentialBackoff: true
        exponentialBackoffMultiplier: 2
        enableRandomizedWait: true
        randomizedWaitFactor: 0.5
        retryExceptions: [java.io.IOException, org.springframework.web.client.HttpServerErrorException]
  timelimiter:
    instances:
      smdp:
        timeoutDuration: 3s
```

```java
@Service
public class SmdpGateway {
    private final RestClient rest;

    @Retry(name = "smdp")
    @CircuitBreaker(name = "smdp", fallbackMethod = "queueForLater")
    @TimeLimiter(name = "smdp")
    public CompletableFuture<DownloadOrder> downloadOrder(String iccid, String idemKey) {
        return CompletableFuture.supplyAsync(() -> rest.post()
                .uri("/es2plus/downloadOrder")
                .header("Idempotency-Key", idemKey)
                .body(new DownloadOrderRequest(iccid))
                .retrieve()
                .body(DownloadOrder.class));
    }

    private CompletableFuture<DownloadOrder> queueForLater(String iccid, String idemKey, Throwable t) {
        return CompletableFuture.completedFuture(DownloadOrder.queued(iccid));
    }
}
```

## Pitfalls
- A breaker per dependency *instance*, not one global breaker: a dead SMS provider should not open the breaker for the payment gateway.
- `minimumNumberOfCalls` too low in low-traffic services: two failures at night open the breaker for 30 s with no real outage; too high and it never opens.
- Fallback that calls another remote service needs its own breaker, otherwise the fallback fails the same way.
- Retrying inside a `@Transactional` method keeps the DB connection and locks held during backoff; retry around the transaction, not inside it ([[spring-transactional]]).
- Resilience4j 2.x requires Java 17+ and dropped Vavr; copy-pasted 1.x configs with `vavr` types will not compile.
- Related: [[idempotency]], [[rest-clients]], [[saga]] (what to do when a step fails for real), [[api-gateway-and-service-discovery]] (gateway-level rate limiting), [[system-design-basics]].
