---
title: Hexagonal architecture — ports and adapters
tags: [architecture, hexagonal, ddd, spring, testing]
learned: 2026-10-27
---
## TL;DR
Hexagonal architecture (Alistair Cockburn, 2005, also "ports and adapters") puts the domain and application logic in the centre and lets it talk to the outside world only through **ports** — interfaces the core defines. **Adapters** implement those ports for concrete technology: REST, Kafka, JPA, Temporal, an SM-DP+ client. Because the core owns the interfaces, every dependency points inwards and the core can be compiled and tested without Spring, a database or a broker. The cost is more interfaces and mapping code, which pays off only when the domain has real rules.

## The problem it solves
- **Without it:** the eSIM provisioning logic calls `JpaRepository`, `KafkaTemplate` and the SM-DP+ HTTP client directly, so a unit test needs a Spring context, a database and a broker, and adding a second entry point (a Temporal activity next to REST) or changing the profile vendor means editing business code.
- **What it changes:** the core defines ports in domain language and adapters implement them, so the core is plain Java, tested in milliseconds with in-memory fakes, and an adapter can be replaced without touching the rules.
- **When you don't need it:** when the domain has few real rules (a lookup table, a settings API); the extra interfaces and entity-to-domain mapping then cost more than they save.

## Key points
### Ports and adapters
- **Port**: an interface in the core that describes a need or a capability in domain language (`ReserveProfile`, `OrderRepository`), never in technology language (`JpaOrderRepository`, `KafkaSender`).
- **Driving (primary, inbound) side**: something outside calls the application. Ports are the use-case interfaces; adapters are REST controllers, Kafka listeners, Temporal workflows and activities, CLI, scheduled jobs.
- **Driven (secondary, outbound) side**: the application calls something outside. Ports are repository and gateway interfaces; adapters are JPA repositories, HTTP clients, Kafka producers, mail senders.

### Dependency inversion in practice
- The core defines `OrderRepository`; the JPA adapter implements it. The compile-time dependency runs adapter → core even though runtime calls flow core → adapter: the "D" of [[solid]] at module scale.
- The core is plain Java: no `@Entity`, `@Service`, `@Transactional`. Keep it in a separate Gradle/Maven module so the compiler enforces the rule ([[maven]]).
- Wiring happens at the edge: a Spring `@Configuration` creates the core services and injects adapter beans into them ([[spring-ioc-and-di]], [[spring-bean-scopes-and-configuration]]).

### Mapping to a Spring Boot service
| Hexagonal concept | Spring implementation |
|---|---|
| Domain model | Plain classes/records with invariants ([[ddd-basics]]) |
| Application service (use case) | Plain class, exposed as a bean from `@Configuration`; transaction boundary |
| Inbound port | Java interface the application service implements |
| Inbound adapter | `@RestController`, `@KafkaListener` ([[spring-kafka-consumer]]), Temporal `@WorkflowMethod` / `@ActivityMethod` ([[temporal-concepts]]) |
| Outbound port | Interface in the core (`ProfileGateway`, `OrderRepository`) |
| Outbound adapter | `@Repository` with Spring Data ([[spring-data-repositories]]), `RestClient` ([[rest-clients]]), `KafkaTemplate` ([[kafka-producer]]) |
| Transactions | `@Transactional` on the adapter-side bean or via `TransactionTemplate` passed through a port ([[spring-transactional]]) |

- JPA entities live in the adapter and are mapped to and from domain objects; two models for one table is the price of a domain that does not know Hibernate ([[jpa-entities-and-relationships]]).
- With Temporal, the workflow is an inbound adapter that orchestrates; each activity is a thin adapter around a use case or an outbound port. Business rules stay in the core.

### Testing the core without infrastructure
- Unit tests call the application service with in-memory fakes of the outbound ports; no Spring context, milliseconds per test ([[junit-and-mockito]]).
- Adapter tests are separate: `@DataJpaTest` with [[testcontainers]] for JPA, `@WebMvcTest` for the controller, Testcontainers Kafka for the listener.
- A few end-to-end tests wire everything; the pyramid follows from the architecture ([[testing-types-and-dev-cycles]]).

### How it differs from the neighbours
- Versus [[layered-architecture]]: there the service depends on the repository implementation; hexagonal flips that dependency.
- Versus [[clean-architecture]] and Onion: same idea with more rings and an explicit use-case layer; hexagonal is deliberately minimal.
- Pairs with DDD: aggregates in the centre, repositories as ports, domain events through an outbound port backed by the [[outbox-pattern]].

## Example
An eSIM provisioning core with one inbound port, one domain service and a JPA adapter.

```java
// core module: no Spring here
public interface ProvisionEsim {                                   // inbound port
    EsimOrderId provision(Msisdn msisdn, TariffCode tariff);
}
public interface EsimOrderRepository {                             // outbound port
    EsimOrder save(EsimOrder order);
    Optional<EsimOrder> findPendingFor(Msisdn msisdn);
}
public interface ProfileGateway { Iccid reserve(EsimOrderId id, TariffCode tariff); }

public class ProvisionEsimService implements ProvisionEsim {
    private final EsimOrderRepository orders;
    private final ProfileGateway profiles;
    public ProvisionEsimService(EsimOrderRepository orders, ProfileGateway profiles) { this.orders = orders; this.profiles = profiles; }

    @Override
    public EsimOrderId provision(Msisdn msisdn, TariffCode tariff) {
        orders.findPendingFor(msisdn).ifPresent(o -> { throw new PendingOrderExists(msisdn); });
        EsimOrder order = EsimOrder.create(msisdn, tariff);         // invariants inside the aggregate
        Iccid iccid = profiles.reserve(order.id(), tariff);
        order.attachProfile(iccid);
        return orders.save(order).id();
    }
}
```

```java
// adapter module: Spring + JPA
@Component
@RequiredArgsConstructor
class JpaEsimOrderRepository implements EsimOrderRepository {
    private final SpringDataEsimOrderRepository jpa;               // extends JpaRepository<EsimOrderEntity, UUID>
    private final EsimOrderMapper mapper;                          // entity <-> domain

    @Override public EsimOrder save(EsimOrder order) { return mapper.toDomain(jpa.save(mapper.toEntity(order))); }
    @Override public Optional<EsimOrder> findPendingFor(Msisdn msisdn) {
        return jpa.findByMsisdnAndStatus(msisdn.value(), "PENDING").map(mapper::toDomain);
    }
}

@Configuration
class CoreConfig {
    @Bean ProvisionEsim provisionEsim(EsimOrderRepository orders, ProfileGateway profiles) {
        return new ProvisionEsimService(orders, profiles);
    }
}
```

The REST controller and the Temporal activity both call `provisionEsim.provision(...)`; the unit test passes a `HashMap`-backed repository and a fake gateway.

## Pitfalls
- Ports named after technology (`KafkaPublisher`, `JpaOrderPort`) leak the adapter into the core and make a replacement impossible; name them after what the domain needs.
- One generic `Repository<T>` port with `findAll`/`save`/`delete` drags ORM semantics into the domain; keep each port to the queries the use cases need.
- Mapping fatigue: DTO → command → domain → entity → domain → response. Accept it for rich aggregates; for a lookup table, plain layers are fine.
- Transactions that span an outbound HTTP call: the core does not know it is inside `@Transactional`, so the adapter layer must decide where the boundary is, and remote calls belong after commit or in the [[outbox-pattern]].
- Treating Temporal workflow code as the domain: it is an adapter with replay constraints, and rules that live only there need the Temporal test server to test.
- Related: [[ddd-basics]], [[clean-architecture]], [[modular-monolith]], [[esim-provisioning-platform]] (where this shape was tried in practice), [[spring-ioc-and-di]], [[temporal-concepts]].
