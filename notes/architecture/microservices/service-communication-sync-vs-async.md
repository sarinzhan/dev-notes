---
title: Service communication — synchronous vs asynchronous
tags: [microservices, rest, grpc, kafka, resilience]
learned: 2026-10-27
---
## TL;DR
Services talk either **synchronously** (REST/gRPC: the caller blocks for an answer and shares the callee's fate) or **asynchronously** (messages/events: the caller continues and the work happens later, at-least-once). Use sync for questions that need an answer now and async for facts and work that can be deferred. Every sync call needs a **timeout**, a bounded **retry budget** with backoff and jitter, and a [[circuit-breaker]]; every async hop needs an [[outbox-pattern]] on the producer and [[idempotency]] on the consumer. The usual mistake is chaining sync calls until availability multiplies down to nothing.

## Key points

### The comparison
| | Synchronous (REST, gRPC) | Asynchronous (Kafka, RabbitMQ) |
|---|---|---|
| Coupling | Temporal: callee must be up now | Only to the contract (schema) |
| Latency | Request latency = sum of the chain | Caller returns fast; end-to-end seconds |
| Consistency | Caller sees the result immediately | Eventual |
| Failure handling | Timeout, retry, breaker, fallback | Retry topic, DLQ, lag alerts ([[dead-letter-queues-and-retries]]) |
| Debugging | Stack of calls in one trace | Trace links across time ([[distributed-tracing]]) |
| Fits | Queries, validations, auth, anything the user waits for | Side effects, fan-out, bursts, long work |
- Availability math: five sync hops at 99.9 % each give 99.5 % for the request. Async removes a hop from the critical path; it does not remove the work.
- Request/response over a queue (reply topics, correlation id) is still synchronous from the caller's point of view, only slower and harder to debug; use it rarely.

### REST vs gRPC for the sync part
- REST/JSON: universal, cacheable ([[http-caching]]), readable, OpenAPI tooling ([[openapi-swagger]]). gRPC: binary, typed contract, streaming, 5–10x less CPU on serialisation, HTTP/2 multiplexing; weaker browser story, needs a gateway for external clients ([[grpc-and-protobuf]], [[http-1-1-vs-2-vs-3]]).
- Internal service-to-service with many small calls: gRPC pays off. Public API and partner integrations: REST.

### Timeouts and the retry budget
- Set **connect** and **read** timeouts on every client ([[rest-clients]]); no timeout means a hung thread per request until the pool is gone.
- Timeouts must shrink down the chain: if the gateway waits 5 s, the service behind it must give up at 4 s, its database at 3 s (`statement_timeout`), otherwise the caller has left and the work is wasted ([[postgres-performance-checklist]]).
- Retry budget: retries as a percentage of traffic (10 %) rather than "3 per call", or at least 3 attempts × short timeout with exponential backoff + jitter, only on idempotent operations and only on transient errors. Retries at every layer multiply (3 × 3 × 3 = 27 calls for one failure): retry at one layer.
- Deadline propagation: gRPC carries deadlines natively; for REST pass a `X-Deadline` header or rely on shrinking timeouts.

### Failure containment
- Bulkheads: separate thread pools or semaphores per dependency so one slow partner cannot exhaust Tomcat ([[circuit-breaker]] covers bulkhead and breaker config).
- Fallbacks: cached last-known value, default, degraded response (`"status": "unknown"`), or queue the work and answer 202 Accepted. Decide per call what "partial" means for the user.
- Graceful degradation beats a 500 for optional dependencies (recommendations, enrichment).

### Choosing per interaction
- "Is this MSISDN eligible for an eSIM?" → sync query to the subscriber service (user is waiting), with a 300 ms timeout and a cached fallback.
- "Profile activated, start billing" → async event; billing may be down for an hour and nothing is lost.
- "Charge the card" → sync to the PSP with an idempotency key and a long-running fallback to a [[saga]] step if the PSP times out ambiguously.
- "Order placed, provision over the next 24 h" → workflow engine ([[temporal-concepts]]), which is async orchestration with sync activities inside.

## Example
A `RestClient` with explicit timeouts and a deadline header, wrapped by Resilience4j, and the async alternative for the same business step.

```java
@Configuration
class ClientConfig {
    @Bean
    RestClient subscriberClient(RestClient.Builder builder) {
        var factory = new JdkClientHttpRequestFactory(HttpClient.newBuilder()
                .connectTimeout(Duration.ofMillis(200)).build());
        factory.setReadTimeout(Duration.ofMillis(500));                  // user-facing: fail fast
        return builder.baseUrl("http://subscriber-service").requestFactory(factory).build();
    }
}

@Service
@RequiredArgsConstructor
public class EligibilityGateway {
    private final RestClient subscriberClient;

    @Retry(name = "subscriber")                       // 2 attempts, 100 ms jittered backoff, only IOException/5xx
    @CircuitBreaker(name = "subscriber", fallbackMethod = "unknown")
    public Eligibility check(Msisdn msisdn, Instant deadline) {
        return subscriberClient.get()
                .uri("/subscribers/{msisdn}/esim-eligibility", msisdn.value())
                .header("X-Deadline", deadline.toString())               // callee can stop early
                .retrieve()
                .body(Eligibility.class);
    }

    Eligibility unknown(Msisdn msisdn, Instant deadline, Throwable t) {
        return Eligibility.unknown("subscriber-service unavailable");    // degrade, do not 500
    }
}

// The async side of the same flow: the fact is recorded in the outbox, billing reacts later.
@Transactional
public void activate(ActivateProfile cmd) {
    Sim sim = sims.findById(cmd.simId()).orElseThrow();
    sim.activate(cmd.iccid());
    sims.save(sim);
    outbox.save(OutboxRow.of("esim.sim.events", sim.id(), new ProfileActivatedV1(sim.id(), cmd.iccid().value())));
}
```

## Pitfalls
- Default timeouts: `RestClient` with the JDK client has no read timeout, Apache HttpClient defaults to infinite in some versions; a single slow dependency then takes the whole service down.
- Retrying non-idempotent POSTs on timeout: double charges, double SMS. Add an `Idempotency-Key` or do not retry.
- Sync call inside a Kafka listener to a service that is down: the partition stalls, lag grows, and the retry storm hits the dependency when it recovers. Use a breaker and a retry topic.
- Treating the message broker as "always up": producers need the outbox precisely because `send()` can fail too.
- Related: [[circuit-breaker]], [[event-driven-architecture]], [[api-gateway-and-service-discovery]], [[failure-modes-and-timeouts]], [[rest-and-http]].
