---
title: Layered architecture — controller, service, repository
tags: [architecture, layered, spring, design-principles]
learned: 2026-10-27
---
## TL;DR
Layered architecture splits an application into horizontal layers — presentation (controllers), business (services), data access (repositories) — and lets dependencies point only downwards. It is the default shape of almost every Spring Boot service and is perfectly fine for CRUD-heavy, small or medium codebases. It breaks when business rules spread across controllers, services and SQL, when services grow into god classes, and when the domain model degrades into bags of getters and setters.

## The problem it solves
- **Without it:** with no separation at all, one controller method parses JSON, checks the subscriber's tariff, writes SQL through `JdbcTemplate` and calls SM-DP+. The "one pending order" check gets copied into the Kafka listener, and any change to the API or the schema touches business logic.
- **What it changes:** each kind of code gets one home (controllers handle transport, services own rules and transactions, repositories own queries), and dependencies go only downwards, so everyone knows where to look.
- **When you don't need it:** a tiny tool with one or two endpoints, where layers are just extra files. It also stops being enough once the rules grow complex (see "Where layered architecture breaks").

## Key points
### The layers and the dependency direction
- **Presentation**: REST controllers, Kafka listeners, schedulers. Translate transport (HTTP, JSON, headers) into method calls and back ([[spring-mvc-request-lifecycle]]).
- **Business / service**: use cases, validation, orchestration of repositories and external clients, transaction boundaries ([[spring-transactional]]).
- **Data access**: repositories, JPA entities, JDBC, external HTTP clients ([[spring-data-repositories]]).
- Dependencies go **top to bottom only**: controller → service → repository. A repository never calls a service; a service never touches `HttpServletRequest`.
- *Strict* layering lets a layer call only the layer directly below; *relaxed* layering lets a controller call a repository directly. Most Spring projects are relaxed in practice, which is where leaks start.
- The domain model (entities) is usually shared by every layer, so it sits "beside" the stack rather than inside one layer.

### Anemic domain model
- Entities contain only fields and accessors; every rule lives in `@Service` methods. Martin Fowler called this an anti-pattern because it is procedural code wearing an object-oriented costume.
- Consequence: the same invariant ("a profile cannot be activated twice") is checked in three services or in none. Compare the rich aggregate in [[ddd-basics]].
- It is still the honest choice when the "domain" really is CRUD with little logic: a settings page does not need an aggregate.

### Where layered architecture breaks
- **Logic leaks upward**: controllers start doing `if (order.getStatus() == PAID)` before calling the service, and the Kafka listener duplicates the check in its own way.
- **Logic leaks downward**: business rules hide in JPQL queries, database triggers or `@PrePersist` callbacks, invisible to tests.
- **Transaction boundaries get fuzzy**: `@Transactional` on a controller method, or a service calling another `@Transactional` service inside a loop, so one failure rolls back unrelated work or nothing at all.
- **God services**: `SubscriberService` with 60 methods and 15 injected dependencies; every feature touches it, merge conflicts are constant, tests need a dozen mocks ([[junit-and-mockito]]).
- **Infrastructure in the centre**: services depend on `JpaRepository` and `RestTemplate` directly, so the "business layer" cannot be compiled or tested without Spring and a database.
- **Package-by-layer** (`controller/`, `service/`, `repository/`) hides features: to understand "referral bonus" you open six packages. See [[modular-monolith]] for package-by-feature.

### When it is perfectly fine
- Small teams, small services, mostly CRUD, short lifetime, or an internal tool: the extra interfaces of [[hexagonal-architecture]] or [[clean-architecture]] cost more than they save.
- The rules stay cheap to follow: controllers thin, services own transactions, repositories own queries, entities carry the obvious invariants.
- It is also the natural starting point: most hexagonal services were layered services whose core got extracted once the logic grew.

## Example
Typical Spring layout and the usual shape of a service method:

```text
com.beeline.esim
├── controller/   EsimOrderController
├── service/      EsimOrderService, PricingService
├── repository/   EsimOrderRepository (JpaRepository)
├── entity/       EsimOrder, Subscriber
└── dto/          CreateOrderRequest, OrderResponse
```

```java
@Service
@RequiredArgsConstructor
public class EsimOrderService {
    private final EsimOrderRepository orders;
    private final SubscriberRepository subscribers;
    private final PricingService pricing;
    private final ProfileClient profiles;                 // HTTP client to SM-DP+

    @Transactional                                        // the service owns the boundary
    public OrderResponse create(CreateOrderRequest req) {
        Subscriber sub = subscribers.findByMsisdn(req.msisdn())
                .orElseThrow(() -> new NotFoundException("subscriber " + req.msisdn()));
        if (orders.existsBySubscriberAndStatus(sub, OrderStatus.PENDING)) {
            throw new ConflictException("pending order already exists");   // rule lives here, not in Subscriber
        }
        Money price = pricing.priceFor(sub.getTariff());
        EsimOrder order = orders.save(new EsimOrder(sub, price, OrderStatus.PENDING));
        profiles.reserve(order.getId());                  // remote call inside the transaction: see pitfalls
        return OrderResponse.from(order);
    }
}
```

The method is readable and testable with mocks, and for a service of this size it is the right amount of structure. The warning signs are the remote call inside `@Transactional` and the "one pending order" rule that only this method knows about.

## Pitfalls
- Remote calls (HTTP, Kafka send) inside a `@Transactional` method hold a DB connection during network latency ([[hikaricp-pool-sizing]]) and publish events that are lost on rollback; move them after commit or use the [[outbox-pattern]].
- Returning JPA entities from controllers couples the API contract to the schema and triggers lazy-loading surprises ([[jpa-n-plus-one]]); map to DTOs at the presentation layer.
- `@Transactional` self-invocation inside one service silently has no effect because the proxy is bypassed ([[spring-aop]]).
- "Service calls service calls service" chains with no owner of the use case; when you cannot say which class implements "cancel order", extract an application service per use case.
- Related: [[hexagonal-architecture]] and [[clean-architecture]] (what to do when the core outgrows the layers), [[modular-monolith]] (enforcing boundaries), [[solid]], [[ddd-basics]].
