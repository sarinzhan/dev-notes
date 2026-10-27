---
title: Failure modes and why timeouts are the only detector
tags: [distributed-systems, reliability, timeouts, theory]
learned: 2026-10-27
---
## TL;DR
In a distributed system a request can fail in four places: it never arrived, the remote crashed while working, the remote did its job but the reply was lost, or the remote is just slow. From the caller's side these are **indistinguishable**: all you observe is "no reply yet". The only tool for detecting failure over an asynchronous network is a **timeout**, and a timeout is a guess, not a fact. Everything else, retries, idempotency, leases, fencing, exists to make acting on that guess safe.

## Key points

### Failure taxonomy
| Mode | What happens | Example |
|---|---|---|
| **Crash-stop** | Node halts and stays down | pod OOM-killed, `kill -9` |
| **Crash-recovery** | Node halts, later restarts, possibly with old state | Kafka broker restart, Postgres crash recovery from WAL |
| **Omission** | Messages are lost or not sent | packet drop, full socket buffer, `send()` succeeded but the process died before flush |
| **Timing / gray** | Node responds, but too late or inconsistently | 30 s GC pause, disk at 100% utilisation, one AZ with 10% packet loss |
| **Network partition** | Groups of nodes cannot reach each other but each group is healthy | switch failure, misapplied firewall rule, cloud AZ isolation |
| **Byzantine** | Node sends wrong or malicious data | out of scope for almost all internal systems; blockchains care |

- **Gray failures** are the expensive ones: a node that passes its health check but serves 1% of requests at 20 s latency poisons connection pools and retry budgets everywhere ([[circuit-breaker]]).
- A **partition** looks exactly like a crash of the other side. Both sides may conclude "they are down, I am the leader" — that is split brain ([[quorums-and-leader-election]]).

### The two-generals argument, in one line
Over a network that can lose messages, no finite exchange lets both sides be certain the other knows the outcome. So "did my write happen?" after a timeout has three answers: no, yes, or in progress. Your code must be correct under all three.

### Timeouts are guesses with two wrong answers
- **Too short**: you declare a healthy-but-slow node dead, retry elsewhere, and now the work happens twice (duplicate payment, duplicate eSIM download order). You also shed load onto survivors during the exact moment the system is slow, amplifying the overload.
- **Too long**: threads and connections sit blocked, the pool drains ([[hikaricp-pool-sizing]]), the caller's caller times out first, and the failure cascades upward.
- There is no correct value; there is a budget. Set timeouts from the **SLO of the caller** downwards: if the API promises 2 s, the sum of downstream timeouts plus retries must fit in 2 s ([[service-communication-sync-vs-async]]).
- Separate **connect** timeout (fail fast: ~1 s) from **read** timeout (depends on the operation). A TCP connect that hangs means the host or network is gone; a read that hangs may mean a slow query ([[tcp-handshake-and-connection-states]]).

### Making it safe to act on a timeout
- **Idempotent operations**: a retry after an unknown outcome must not double the effect. Use an idempotency key per logical request ([[idempotency]]).
- **Retries with backoff and jitter**, and a **retry budget** (e.g. retries may be at most 10% of base traffic) so a slow dependency does not get a retry storm ([[dead-letter-queues-and-retries]]).
- **Leases and fencing tokens**: when a timeout leads to a new leader or lock holder, the old one may still be alive; a monotonic token lets the storage reject its late writes ([[distributed-locks-and-fencing]], [[leases-heartbeats-and-health]]).
- **Circuit breakers** turn repeated timeouts into fast failures so threads stop piling up ([[circuit-breaker]]).
- **Deadlines propagate**: pass the remaining budget downstream (gRPC deadlines, `Context` cancellation) instead of each hop choosing its own number ([[grpc-and-protobuf]]).

### Failure detectors
- Heartbeats over a timeout window are a failure detector with tunable false positives. φ-accrual detectors (Cassandra, Akka) output a suspicion level instead of a boolean so the threshold adapts to observed latency.
- Kubernetes liveness vs readiness probes are exactly this: readiness removes the pod from the load balancer on suspicion, liveness restarts it on stronger evidence ([[kubernetes-basics]]).

### Interview framing
- "A call timed out, what do you do?" Not "retry": first check that the operation is idempotent and a retry budget remains, then retry with jitter against another instance, and surface a correlated error when the budget is spent ([[distributed-tracing]]).

## Example
A Spring `RestClient` call to the eSIM entitlement server with explicit connect/read timeouts, a bounded retry with jitter, and an idempotency key so the retry cannot double-activate.
```java
@Configuration
public class EntitlementClientConfig {
    @Bean
    RestClient entitlementClient(RestClient.Builder builder) {
        var factory = new JdkClientHttpRequestFactory(
            HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(1)).build());
        factory.setReadTimeout(Duration.ofSeconds(3));          // activation is a fast call
        return builder.baseUrl("https://es.internal/v1").requestFactory(factory).build();
    }
}

@Service
@RequiredArgsConstructor
public class ActivationClient {
    private final RestClient client;

    @Retryable(retryFor = {ResourceAccessException.class, HttpServerErrorException.class},
               maxAttempts = 3, backoff = @Backoff(delay = 200, multiplier = 2, random = true))
    public ActivationResult activate(UUID orderId, String iccid) {
        return client.post().uri("/activations")
            .header("Idempotency-Key", orderId.toString())  // same key on every retry
            .body(new ActivationRequest(iccid))
            .retrieve().body(ActivationResult.class);
    }
}
```
The three attempts with 200/400/800 ms (randomised) fit the caller's budget; without the key, the second attempt after a lost reply would create a second activation ([[rest-clients]]).

## Pitfalls
- No timeout at all: the JDK `HttpClient` and many Kafka/JDBC settings default to "wait forever" for reads; one hung dependency can take down the service.
- Retrying non-idempotent `POST`s because "the network is flaky": the duplicate shows up as a double charge or a second SMS.
- Health checks that only test "process is up" and miss gray failure; check a real dependency path with a short timeout.
- Retrying synchronously inside a Kafka listener on a slow dependency: the whole partition stalls, consumer lag grows, and a rebalance kicks in ([[spring-kafka-consumer]], [[backpressure-and-flow-control]]).
- Related: [[cap-and-pacelc]] for why a partition forces a choice, [[exactly-once-myth]] for what retries do to delivery guarantees, [[sockets-and-connection-pooling]] for what a hung connection costs.
