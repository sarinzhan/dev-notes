---
title: Error handling strategies
tags: [programming, exceptions, result-types, fail-fast, retries, resilience]
learned: 2026-10-27
---
## TL;DR
Separate **expected outcomes** (validation failed, insufficient balance, not found) from **failures** (database down, bug, timeout). Model expected outcomes in the type system or as domain exceptions the caller must handle; let failures propagate as unchecked exceptions to a boundary that logs once, maps to a response and decides whether to retry. **Fail fast** inside a process (bad config, broken invariant: crash early and loudly); **be resilient at the edges** (timeouts, bounded retries with backoff, circuit breakers, dead-letter queues). The Java mechanics (checked vs unchecked, try-with-resources, chaining) are in [[exceptions]]; this note is about choosing a strategy.

## Key points

### Classify first
| Category | Example | Who handles it | Mechanism |
|---|---|---|---|
| Expected domain outcome | card declined, eSIM already activated, validation error | the immediate caller, as business logic | result/sealed type, or a specific domain exception |
| Programming error | `NullPointerException`, `IllegalArgumentException` from a wrong call | nobody at the call site; fix the bug | unchecked, let it reach the boundary, 500, alert |
| Transient infrastructure failure | timeout, connection reset, 503, deadlock | a retry policy at the boundary | retry with backoff, circuit breaker ([[circuit-breaker]]) |
| Permanent infrastructure failure | wrong credentials, missing table, unknown host | operator | fail fast at startup, alert |
| Resource exhaustion | OOM, pool exhausted, disk full | process/platform | crash and restart (Kubernetes), backpressure ([[backpressure-and-flow-control]]) |

### Exceptions vs result types
| | Exceptions | Result / sealed types (`Either`, `Result<T, E>`, `Optional`) |
|---|---|---|
| Visibility in signature | checked only; unchecked invisible | always visible |
| Compiler forces handling | checked: yes, but people wrap and rethrow | exhaustive `switch` over sealed types: yes |
| Composition with lambdas/streams | poor (checked), fine (unchecked) | good |
| Cost | stack trace capture is expensive (microseconds, allocation) | a small object |
| Control flow | non-local jump, skips frames | explicit at every call |
| Best for | failures you cannot handle locally | expected alternative outcomes |

- Go (`err` return), Rust (`Result`), Kotlin Arrow and functional Java all push expected outcomes into return values. Java 21 makes this ergonomic: `sealed interface Outcome permits Success, Declined, NotFound` + pattern-matching `switch` ([[type-systems]]).
- `Optional` is a result type for exactly one case: absent. Do not use it for fields or parameters; do not use it to signal errors with a cause.
- Checked exceptions in Java were this idea in 1995 form; the industry mostly moved to unchecked (Spring, Hibernate, Kotlin) because checked ones leak implementation details and do not compose ([[exceptions]]).

### Fail fast
- Validate at the boundary (request DTOs, Bean Validation, config at startup via `@ConfigurationProperties` + `@Validated`) so bad input never reaches the domain ([[spring-mvc-validation-and-uploads]]).
- Constructors enforce invariants; an object that exists is valid ([[value-objects-and-entities]]).
- `Objects.requireNonNull`, assertions on impossible states, `throw new IllegalStateException` instead of silently continuing: the sooner a bug stops the program, the closer the stack trace is to the cause.
- Startup checks: fail the pod if Kafka topics, DB migrations ([[schema-migrations-flyway-liquibase]]) or required secrets are missing; a crash-looping pod is more visible than a half-working one.

### Resilience at boundaries
- **Timeouts everywhere**: connect, read, pool checkout, statement timeout; a missing timeout is an infinite one ([[failure-modes-and-timeouts]], [[postgres-performance-checklist]]).
- **Retries** only for idempotent operations and transient errors, with exponential backoff and jitter, a bounded attempt count and a total budget; retries amplify load during an outage ([[service-communication-sync-vs-async]], [[idempotency]]).
- **Circuit breaker** stops calling a dependency that keeps failing ([[circuit-breaker]]); **bulkhead** isolates thread pools per dependency.
- **Async consumers**: catch per message, classify (retryable vs poison), retry with delay topics, then dead-letter with the failure reason ([[dead-letter-queues-and-retries]], [[spring-kafka-consumer]]).
- **Workflows**: Temporal retries activities by policy and keeps the failure history; business failures are returned as results, not thrown ([[temporal-concepts]]).
- **Compensation** when a multi-step process fails halfway ([[saga]]).

### Observability of errors
- Log once, at the boundary, with context (ids, operation), at the right level: expected outcomes are `INFO`/`WARN` at most, failures are `ERROR` with the stack trace. Logging and rethrowing at every layer produces five traces per incident ([[observability-three-pillars]]).
- Map to HTTP consistently: 400/422 validation, 404 not found, 409 conflict/idempotency, 429 rate limit, 500 bug, 502/503/504 dependency; RFC 9457 `application/problem+json` bodies; one `@RestControllerAdvice` ([[rest-and-http]]).
- Error tracking groups by exception type and location, so wrap with a meaningful type rather than `RuntimeException("failed")` ([[error-tracking-with-sentry]]).

## Example
Expected outcomes as a sealed result, failures as unchecked exceptions, one boundary that maps both; retries left to the HTTP client/resilience layer.

```java
sealed interface ActivationResult permits Activated, AlreadyActive, ProfileNotFound, SmdpUnavailable {}
record Activated(String iccid, Instant at) implements ActivationResult {}
record AlreadyActive(String iccid) implements ActivationResult {}
record ProfileNotFound(String iccid) implements ActivationResult {}
record SmdpUnavailable(String iccid, String detail) implements ActivationResult {}

@Service
@RequiredArgsConstructor
class ActivationService {
    private final ProfileRepository profiles;
    private final SmdpClient smdp;                  // has its own timeouts + bounded retry + circuit breaker

    @Transactional
    public ActivationResult activate(String iccid) {
        Profile p = profiles.findByIccid(iccid).orElse(null);
        if (p == null) return new ProfileNotFound(iccid);
        if (p.isActive()) return new AlreadyActive(iccid);         // expected, not exceptional
        try {
            smdp.enable(iccid);                                    // may throw SmdpException after retries
        } catch (SmdpException e) {
            return new SmdpUnavailable(iccid, e.getMessage());     // expected transient outcome for the caller
        }
        p.activate(Instant.now());                                 // invariant checks inside; IllegalStateException = bug
        return new Activated(iccid, p.activatedAt());
    }
}

@RestController
@RequiredArgsConstructor
class ActivationController {
    private final ActivationService service;

    @PostMapping("/profiles/{iccid}/activate")
    ResponseEntity<?> activate(@PathVariable String iccid) {
        return switch (service.activate(iccid)) {                  // compiler-checked exhaustive mapping
            case Activated a        -> ResponseEntity.ok(a);
            case AlreadyActive a    -> ResponseEntity.status(409).body(a);
            case ProfileNotFound n  -> ResponseEntity.status(404).body(n);
            case SmdpUnavailable u  -> ResponseEntity.status(503).header("Retry-After", "30").body(u);
        };
    }
}
// Unexpected exceptions (bugs, DB down) skip all of this and reach a @RestControllerAdvice that logs once and returns 500.
```

## Pitfalls
- `catch (Exception e) { log.error(...); }` and continue: swallows bugs, turns data corruption into silence; catch narrowly or rethrow.
- Retrying a non-idempotent call (payment capture, `INSERT` without a key) on timeout: the first attempt may have succeeded ([[idempotency]]).
- Using exceptions for expected flow in hot paths (`NumberFormatException` to test if a string is numeric, `NoSuchElementException` for a lookup): stack-trace capture costs microseconds each and pollutes error dashboards.
- Wrapping every exception into one `ServiceException` loses the type the boundary needs to decide 4xx vs 5xx vs retry; keep a small hierarchy.
- Related: [[exceptions]] for the Java mechanics, [[circuit-breaker]] and [[dead-letter-queues-and-retries]] for the resilience side, [[type-systems]] for sealed result types.
