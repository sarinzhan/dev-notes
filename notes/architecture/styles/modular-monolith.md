---
title: Modular monolith — enforced boundaries, Spring Modulith, ArchUnit
tags: [architecture, modular-monolith, spring-modulith, archunit, postgres]
learned: 2026-10-27
---
## TL;DR
A modular monolith is one deployable unit whose code is split into **modules with enforced boundaries**: each module owns its data and exposes a small public API (Java types or events), and the build fails when another module reaches into its internals. It keeps the operational simplicity of a monolith — one process, one database, local transactions, one deploy — while preserving the option to cut a module out into a service later. Spring Modulith supplies the module convention, verification and asynchronous events; ArchUnit enforces any rule Modulith does not.

## The problem it solves
- **Without it:** in a package-by-layer monolith every class is public and every table is reachable, so billing code queries the profile tables directly and order code calls profile internals; extracting a service later means untangling these joins and calls first.
- **What it changes:** each module owns its data and exposes a small API or events, and Spring Modulith or ArchUnit tests fail the build on forbidden access, while the system stays one process with one database and local transactions.
- **When you don't need it:** a small service with one team and one or two features has no boundaries worth enforcing; parts that already need separate scaling or release cadence may fit better as real services.

## Key points
### Package-by-layer vs package-by-feature
- **Package-by-layer** (`controller/`, `service/`, `repository/`) groups by technical role; every feature is spread across all packages and everything is public ([[layered-architecture]]).
- **Package-by-feature** (`order/`, `profile/`, `billing/`) groups by business capability; one feature is one folder, and Java package-private visibility becomes a real tool: only the types a module exports are `public`.
- Modules usually map to bounded contexts ([[ddd-basics]]); inside a module, layers or [[hexagonal-architecture]] still apply.

### Rules of a module
- A module has a public API (a few interfaces, commands, DTOs, events) and internals (entities, repositories, implementation classes) that other modules may not touch.
- Modules talk through another module's API (direct call, same transaction) or through **events** (decoupled, eventually consistent). No cycles between modules.
- Each module owns its tables. Other modules never query them; they ask the owning module or subscribe to its events.

### Spring Modulith (1.x, Spring Boot 3.x)
- Convention: every direct sub-package of the main application package is a module (`com.beeline.esim.order`, `com.beeline.esim.profile`). Types in the module root package are its API; nested packages are internal unless exposed with `@NamedInterface` in a `package-info.java`.
- `@ApplicationModule(allowedDependencies = "profile")` restricts which modules may be used; `ApplicationModules.of(App.class).verify()` in a test fails on cycles, access to internals and undeclared dependencies.
- `@ApplicationModuleListener` on a method = `@Async` + `@Transactional(propagation = REQUIRES_NEW)` + `@TransactionalEventListener(phase = AFTER_COMMIT)`: the listener runs after the publisher commits, in its own transaction and thread.
- **Event publication registry** (`spring-modulith-starter-jpa` or `-jdbc`): every event published through `ApplicationEventPublisher` is stored in an `event_publication` table in the same transaction as the business change and marked completed when each listener finishes. Incomplete publications are resubmitted on restart, which is an in-process [[outbox-pattern]] without Kafka.
- `@Externalized("esim.profile.activated")` with `spring-modulith-events-kafka` publishes the same event to a topic, so a module that later becomes a service keeps the same contract ([[kafka-producer]]).
- `@ApplicationModuleTest` boots one module (plus declared dependencies) for integration tests; `Documenter` generates C4 diagrams from the code.

### ArchUnit (1.x) for everything else
- Reads bytecode and asserts rules as JUnit tests: layered rules inside a module, "no `@Transactional` on controllers", "domain package must not depend on `jakarta.persistence`", "repositories are package-private".
- Works without Spring Modulith; it is the tool for enforcing [[clean-architecture]] rings or hexagonal dependency direction in any project.

### One database, schema-per-module
- One Postgres instance, one connection pool ([[hikaricp-pool-sizing]]), but each module gets its own schema (`order.*`, `profile.*`) or at least its own table prefix and its own Flyway/Liquibase migration folder.
- No foreign keys or joins across module schemas; cross-module references are plain ids. When a module leaves, its schema is already separable.

### When it is enough, and how it splits
- Enough for most teams up to roughly 10–20 developers when release cadence is shared and scaling needs are uniform; local calls and local transactions are features, not debts.
- Split a module into a service when it needs independent scaling, a different release cadence, a separate team, or a different runtime ([[monolith-vs-microservices]]).
- The path: events already flow through the registry → externalize them to Kafka → move the module's schema to its own database → deploy the module as its own Spring Boot application with the same package layout → route traffic through the gateway ([[api-gateway-and-service-discovery]]). The in-process cross-module calls become REST/gRPC calls or a [[saga]].

## Example
Module layout for an eSIM platform and the tests that keep it honest.

```text
com.beeline.esim
├── EsimApplication.java
├── order/                       module "order"
│   ├── OrderService.java        public API (interface + commands)
│   ├── OrderPlaced.java         public event
│   ├── package-info.java        @ApplicationModule(allowedDependencies = {"profile", "billing::api"})
│   └── internal/                entities, JPA repositories, OrderServiceImpl (package-private)
├── profile/                     module "profile"
│   ├── ProfileActivation.java   listens to OrderPlaced
│   └── internal/
└── billing/
    ├── api/                     @NamedInterface("api") in package-info.java
    └── internal/
```

```java
@Component
@RequiredArgsConstructor
class ProfileActivation {
    private final ProfileRepository profiles;

    @ApplicationModuleListener                      // async, after the order transaction commits, own tx
    void on(OrderPlaced event) {
        profiles.reserveFor(event.orderId(), event.tariff());
    }
}
```

```java
class ModularityTests {
    ApplicationModules modules = ApplicationModules.of(EsimApplication.class);

    @Test void modulesRespectBoundaries() { modules.verify(); }

    @Test void domainDoesNotDependOnJpa() {
        JavaClasses classes = new ClassFileImporter().importPackages("com.beeline.esim");
        noClasses().that().resideInAPackage("..domain..")
            .should().dependOnClassesThat().resideInAnyPackage("jakarta.persistence..", "org.springframework..")
            .check(classes);
    }
}
```

## Pitfalls
- A "modular" monolith with one shared `common` module that every module depends on is a layered monolith with extra folders; keep shared code to value objects and nothing stateful.
- Synchronous calls between modules in one transaction are convenient, but every such call is a future network hop; prefer events for anything that is not a query.
- `@ApplicationModuleListener` is asynchronous: the HTTP response returns before the listener finishes and its exceptions never reach the caller; monitor `event_publication` for stuck entries and clean completed ones (Modulith 1.1+ has completion modes and a cleanup API).
- Cross-schema foreign keys and joins quietly rebuild the coupling the module split was meant to remove; forbid them in migrations and in ArchUnit-style SQL review.
- Related: [[monolith-vs-microservices]], [[outbox-pattern]], [[layered-architecture]], [[ddd-basics]], [[testcontainers]].
