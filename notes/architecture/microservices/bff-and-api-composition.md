---
title: Backend for frontend and API composition
tags: [microservices, api-design, gateway, bff]
learned: 2026-10-27
---
## TL;DR
When a screen needs data from five services, somebody must make five calls and merge the results. **API composition** does it in a composer service or the gateway; a **backend for frontend (BFF)** is a composer owned by one client team (web, iOS, partner API) that returns exactly the shape that client needs. Both keep aggregation out of the clients and out of the domain services. The risks are the same: the composer becomes a chatty, slow fan-out with N+1 calls, a dumping ground for business logic, or a bottleneck owned by nobody. Composition works for reads; writes across services belong to a [[saga]] or workflow, not to a BFF.

## Key points

### The problem it solves
- Mobile app shows the subscriber page: SIM status (provisioning), balance (billing), tariff (catalogue), last tickets (support). Options: the app calls four APIs (slow on mobile, four auth flows, four error cases, every client re-implements the merge); one service calls the others (which one? now it depends on all of them); or a composer.
- Different clients want different shapes: the web dashboard wants 40 fields and pagination, the mobile app wants 8 fields and small payloads, the partner API wants a stable versioned contract. One "general" API serves none of them well.

### API gateway vs API composition vs BFF
| | API gateway | Composition service | BFF |
|---|---|---|---|
| Does | Routing, auth, rate limit, TLS, maybe simple aggregation | Fan-out + merge for a use case | Fan-out + merge + client-specific shaping |
| Owned by | Platform team | Backend team | The client's team (web, mobile) |
| Logic | None (config) | Read orchestration | Read orchestration + presentation |
| Count | One (or per zone) | Per domain/use case | One per client type |
- The gateway stays dumb ([[api-gateway-and-service-discovery]]); GraphQL gateways (Apollo Federation, Spring for GraphQL) are a composition layer where the client writes the merge query.

### Composition patterns
- **Parallel fan-out**: call independent services concurrently (`CompletableFuture.allOf`, `WebClient` + `Mono.zip`, or [[virtual-threads]] with structured concurrency), then merge. Latency = max, not sum ([[completable-future]], [[spring-reactive-webclient-sse]]).
- **Partial results**: each part has its own timeout and fallback; the page renders with "balance unavailable" rather than a 502 ([[circuit-breaker]]).
- **Caching** of slow or stable parts (tariff catalogue) in the composer ([[caching]]).
- **Batch endpoints**: `GET /subscribers?ids=1,2,3` on the domain services, so composing a list is one call per service, not N ([[jpa-n-plus-one]] at the network level).
- **Pre-computed views**: when fan-out is too slow or too frequent, build a read model via events instead ([[cqrs]]); the BFF then reads one table. Composition is the sync answer, CQRS the async one.

### BFF rules of thumb
- One BFF per client *type* with real differences (mobile vs web vs partner), not per screen and not per team fashion. Two clients with the same needs share one.
- The BFF holds presentation logic (field selection, formatting, i18n hints, pagination shape) and read orchestration only. Business rules in the BFF are duplicated across BFFs within a month; push them down into the domain services.
- Auth: the BFF terminates the client session (cookie or token) and calls services with service credentials or a propagated JWT ([[authentication-vs-authorization]], [[mtls-and-service-to-service-auth]]). It is also where response-size limits and field masking (no PII to the mobile app) live.
- Writes: a BFF may forward a single command to one service; it must not orchestrate multi-service writes. That is a saga/workflow concern with persistence and compensation.

### Interview framing
- "How does the mobile app get a subscriber page from six microservices?" → BFF with parallel fan-out, per-part timeouts and fallbacks, batch endpoints, caching of static parts; if the page is hot, a projection.
- "Where does the aggregation logic live and who owns it?" → the client team that needs it; domain services expose clean, client-agnostic APIs.

## Example
Mobile BFF endpoint composing three services in parallel with per-part fallbacks, on virtual threads.

```java
@RestController
@RequestMapping("/mobile/v1")
@RequiredArgsConstructor
public class SubscriberPageController {
    private final ProvisioningClient provisioning;   // RestClient wrappers with timeouts + breakers
    private final BillingClient billing;
    private final CatalogueClient catalogue;
    private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();

    public record SubscriberPage(String msisdn, String simStatus, String balance, String tariffName, List<String> warnings) {}

    @GetMapping("/subscribers/{msisdn}")
    public SubscriberPage page(@PathVariable String msisdn) throws InterruptedException, ExecutionException {
        var sim     = executor.submit(() -> provisioning.simStatus(msisdn));
        var balance = executor.submit(() -> billing.balance(msisdn));
        var tariff  = executor.submit(() -> catalogue.tariffFor(msisdn));   // cached 10 min in the client

        List<String> warnings = new ArrayList<>();
        String simStatus = sim.get(500, MILLISECONDS).status();                  // required part: propagate failure
        String balanceText = part(balance, 300, b -> b.formatted(), warnings, "balance");
        String tariffName  = part(tariff, 300, Tariff::name, warnings, "tariff");
        return new SubscriberPage(msisdn, simStatus, balanceText, tariffName, warnings);
    }

    private <T> String part(Future<T> f, long ms, Function<T, String> map, List<String> warnings, String name) {
        try {
            return map.apply(f.get(ms, MILLISECONDS));
        } catch (Exception e) {                       // optional part: degrade, record, continue
            f.cancel(true);
            warnings.add(name + " unavailable");
            return null;
        }
    }
}
```

## Pitfalls
- Sequential fan-out: `sim = ...; balance = ...; tariff = ...;` turns three 100 ms calls into 300 ms; always parallelise independent calls.
- A BFF that grows a database: it is now a service with data ownership problems; a BFF should be stateless apart from caches.
- Composition over paginated lists: merging "page 2" from two services with different sort orders is not possible correctly; pre-compute a read model instead.
- Timeout stacking: the BFF's 500 ms budget plus the gateway's 5 s plus retries inside clients; agree one budget end-to-end ([[service-communication-sync-vs-async]]).
- Related: [[api-gateway-and-service-discovery]], [[cqrs]], [[api-design-for-system-design]], [[design-news-feed]].
