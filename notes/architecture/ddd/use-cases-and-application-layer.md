---
title: Use cases and the application layer
tags: [ddd, architecture, spring, cqrs]
learned: 2026-10-27
---
## TL;DR
The **application layer** is the thin layer between transport (controller, Kafka listener, scheduler) and the domain. Each **use case** (application service, command handler) does orchestration only: open a transaction, load an aggregate through a repository, call one domain method, save, publish events, map the result. Business rules live in the domain ([[aggregates-and-invariants]]); HTTP and JSON live in the adapters ([[hexagonal-architecture]]). Splitting use cases into **commands** (change state) and **queries** (read, no side effects) keeps them small and is the first step toward [[cqrs]].

## The problem it solves
- **Without it:** business rules sit in the `@RestController` and get copied into a `@KafkaListener` and a scheduler, or one `SimService` grows to 30 methods with no clear transaction boundary.
- **What it changes:** each use case is one handler that runs one transaction around one aggregate, and REST, Kafka and the scheduler call the same handler. Split into commands and queries, the 30-method `SimService` becomes thirty one-method classes that each depend only on what they use ([[solid|ISP, SRP]]); testing one use case needs one fake port ([[testing-types-and-dev-cycles]]).
- **When you don't need it:** a small CRUD service where controller → repository is the whole logic; a handler and a command object per endpoint is only ceremony there.

## Key points

### What belongs where
| Layer | Knows about | Typical code |
|---|---|---|
| Transport / adapter | HTTP, Kafka, JSON, auth principal | `@RestController`, `@KafkaListener`, request DTO validation ([[spring-mvc-validation-and-uploads]]) |
| Application | Use cases, transactions, ports, events | `ActivateProfileHandler`, `@Transactional`, `ApplicationEventPublisher` |
| Domain | Business rules, invariants, value objects | `Sim.activate()`, `Money`, domain exceptions |
| Infrastructure | Database, external APIs, clock | JPA repositories, `RestClient` gateways, outbox relay |
- Test of the layering: the application service must compile with the controller and the JPA classes deleted.

### Orchestration vs domain logic
- Orchestration: "load, call, save, publish, map". If an application service contains `if (sim.status() == ACTIVE && profile.isReleased())`, that `if` is a rule that belongs on `Sim`.
- Cross-aggregate coordination is legitimate application logic: load `Order`, ask `PricingPolicy` (a domain service) for a price, call `order.applyPrice(...)`. **Domain services** hold rules that do not fit one aggregate (currency conversion, allocation across SIMs); they are stateless and take domain objects, never DTOs.
- The application layer owns the **transaction boundary**: one use case = one transaction = one aggregate ([[spring-transactional]]). Long flows that span several transactions become a [[saga]] or a Temporal workflow ([[temporal-concepts]]), which is itself an application-layer concern.

### Commands and queries
- **Command**: an immutable object naming an intent, `ActivateProfile(simId, iccid, requestedBy)`. One handler, returns nothing or an id. Validated syntactically at the edge (`@Valid`), semantically in the domain.
- **Query**: `GetSimStatus(simId)`; the handler reads straight through a read-optimised path (projection, `JdbcClient`, read replica) and returns a DTO. No aggregate loading, no `@Transactional` beyond `readOnly = true`.
- Naming: handlers after the use case (`SuspendSimHandler`), not after the entity (`SimService`).

### Cross-cutting concerns
- Idempotency key check, authorisation ("can this user suspend this SIM") and auditing wrap the handler: a decorator, an aspect ([[spring-aop]]) or a small `CommandBus` that runs middleware before `handle()`.
- Logging and tracing: the handler name is the natural span name ([[distributed-tracing]]).
- Output mapping to the API model happens in the adapter, so the same handler serves REST, gRPC and a Kafka command topic ([[bff-and-api-composition]]).

### Where DTOs and mappers go
- Request DTO (adapter) → command (application) → domain types. Three shapes feel redundant for CRUD, but they are what lets the API change without touching the domain. For trivial read endpoints skip the middle: controller → query handler → DTO.
- Mapping is boring code; MapStruct or hand-written static methods, never reflection-based deep copies on domain objects.

## Example
Command and query handlers for the same aggregate; the controller is a thin adapter.

```java
public record ActivateProfile(UUID simId, Iccid iccid, String requestedBy) {}

@Service
@RequiredArgsConstructor
public class ActivateProfileHandler {
    private final SimRepository sims;              // port, implemented by JPA in infrastructure
    private final ApplicationEventPublisher events;

    @Transactional
    public void handle(ActivateProfile cmd) {
        Sim sim = sims.findById(cmd.simId()).orElseThrow(() -> new SimNotFoundException(cmd.simId()));
        sim.activate(cmd.iccid());                 // the rule lives here, not in the handler
        sims.save(sim);
        sim.pullEvents().forEach(events::publishEvent);
    }
}

public record SimStatusView(UUID simId, String msisdn, String activeIccid, Instant updatedAt) {}

@Service
@RequiredArgsConstructor
public class GetSimStatusHandler {
    private final JdbcClient jdbc;                 // read side: no aggregate, no lazy loading

    @Transactional(readOnly = true)
    public SimStatusView handle(UUID simId) {
        return jdbc.sql("""
                SELECT s.id, s.msisdn, p.iccid, s.updated_at
                FROM sim s LEFT JOIN sim_profile p ON p.sim_id = s.id AND p.state = 'ACTIVE'
                WHERE s.id = :id""")
            .param("id", simId)
            .query(SimStatusView.class).single();
    }
}

@RestController
@RequestMapping("/sims")
@RequiredArgsConstructor
class SimController {
    private final ActivateProfileHandler activate;
    private final GetSimStatusHandler status;

    @PostMapping("/{id}/activate")
    @ResponseStatus(HttpStatus.ACCEPTED)
    void activate(@PathVariable UUID id, @Valid @RequestBody ActivateRequest body, Principal user) {
        activate.handle(new ActivateProfile(id, new Iccid(body.iccid()), user.getName()));
    }

    @GetMapping("/{id}")
    SimStatusView get(@PathVariable UUID id) { return status.handle(id); }
}
```

## Pitfalls
- `@Transactional` on the controller or on the domain object: the transaction boundary must be the use case, otherwise you hold connections during JSON serialisation or split one use case into several transactions.
- Application services calling each other: `OrderHandler` calling `PaymentHandler` nests transactions and hides the flow; raise a domain event or make an explicit saga.
- Returning JPA entities from handlers to controllers: lazy-loading in the serialiser, `LazyInitializationException`, and the entity becomes the API ([[jpa-persistence-context-and-entity-lifecycle]]).
- A `GenericCrudService<T>` as the application layer: it has no use cases, so rules end up in controllers.
- Related: [[clean-architecture]] (use cases as the inner ring), [[layered-architecture]] (what this replaces), [[cqrs]], [[ddd-basics]].
