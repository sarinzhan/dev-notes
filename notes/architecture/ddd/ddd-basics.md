---
title: DDD basics and the architecture vocabulary
tags: [ddd, architecture, aggregates, design-principles]
learned: 2026-10-22
---
## TL;DR
Domain-driven design (DDD) models code after the business: the **domain** is the subject area, the **model** is its representation in code, an **entity** is a model object with identity, and an **aggregate** is a cluster of entities and value objects that is loaded, changed and saved as one consistency unit through its **root**. Around DDD sits a vocabulary worth keeping straight: architecture *styles* (layered, hexagonal, event-driven), design *principles* (SOLID, DRY, KISS) and design *patterns* (Factory, Observer) answer different questions.

## Key points
### Core DDD terms
- **Domain**: the subject area the software serves (billing, eSIM provisioning, referral programme).
- **Model**: the representation of the domain in code, shared with domain experts through a *ubiquitous language* — the same words in conversation, code and database.
- **Entity**: a concrete domain object with an identity (`Subscriber #123`); two entities with equal fields but different ids are different objects.
- **Value object**: defined only by its attributes, immutable, no id (`Money(100, KGS)`, `PhoneNumber`). Java records fit well ([[immutable-objects]]).
- **Invariants**: conditions that must always hold for a valid state (an order total equals the sum of its lines; an eSIM profile cannot be activated twice).
- **Bounded context**: a boundary inside which a model and its language are consistent. "Customer" in billing and "customer" in support are different models; the context map describes how they translate.

### Aggregate
- A cluster of related objects (entities + value objects) treated as a single unit to guarantee data consistency; a logical consistency unit in the domain.
- **Aggregate root**: the main entity through which all internal objects are managed. External code holds a reference only to the root and changes internals only through it.
- Invariants are enforced inside the aggregate; one transaction changes one aggregate.
- Each aggregate is loaded and saved as a whole by a **repository** ([[spring-data-repositories]]); it is the natural unit for the `aggregate_id` key in the [[outbox-pattern]] and for Kafka partitioning ([[kafka-architecture]]).
- Cross-aggregate changes go through **domain events** and eventual consistency, not through one big transaction.

### Application-level architectures (structural: how the system is built)
- Layered (controller / service / repository), Clean, Onion, Hexagonal (ports and adapters), Microservices, Modular monolith. Goal: manageability and separation of responsibilities.
- Clean and Onion are nearly identical conceptually; Hexagonal focuses on integration with the outside world through ports and adapters; Layered is just a scheme for splitting code into layers.

### System-level approaches (behavioural: how modules talk)
- Event-driven architecture, CQRS, message queue / pub-sub ([[rabbitmq-basics]], [[kafka-architecture]]), actor model. Goal: reactivity, asynchrony, flexibility.
- Data and integration: data-centric, data mesh, data lake / lambda, shared database vs database-per-service. Goal: consistency and data scaling.
- Infrastructure: cloud-native, serverless, SOA, container-based / Kubernetes ([[docker-swarm]]). Goal: scaling, fault tolerance, deployment.

| Category | Answers the question | Examples |
|---|---|---|
| Structural | How is the system built | Layered, Hexagonal |
| Behavioural | How control and events flow | EDA, CQRS |
| Data | How data is stored and managed | Data mesh, Lambda |
| Infrastructure | How it runs and scales | Cloud-native, serverless |
| Domain-oriented | How code maps to the business | DDD |

### Principles vs patterns vs styles
- **Principles** are a goal or rule: [[solid]], DRY, KISS, YAGNI, loose coupling / high cohesion (components focus on one task and are as independent of each other as possible).
- **Patterns** are a ready solution that achieves a goal: "to get loose coupling we use a message queue"; [[creational-patterns]] create objects, [[structural-patterns]] compose them, [[behavioral-patterns]] teach them to talk.
- **Styles** are the overall shape that follows a set of principles: microservices, event-driven architecture.
- **Methodologies** are ways of working: TDD, DDD.

## Example
```java
// Aggregate root: all changes go through Order; invariants live here, not in the service.
public class Order {
    private final OrderId id;
    private final List<OrderLine> lines = new ArrayList<>();   // internal entities
    private OrderStatus status = OrderStatus.NEW;
    private final List<DomainEvent> events = new ArrayList<>();

    public void addLine(ProductId product, Quantity qty, Money price) {
        if (status != OrderStatus.NEW) throw new IllegalStateException("order already " + status);
        lines.add(new OrderLine(product, qty, price));
    }

    public void confirm() {
        if (lines.isEmpty()) throw new IllegalStateException("empty order");   // invariant
        status = OrderStatus.CONFIRMED;
        events.add(new OrderConfirmed(id, total()));   // other aggregates react asynchronously
    }

    public Money total() { return lines.stream().map(OrderLine::subtotal).reduce(Money.ZERO, Money::plus); }
    public List<DomainEvent> pullEvents() { var copy = List.copyOf(events); events.clear(); return copy; }
}
```

## Pitfalls
- Anemic model: entities are getters and setters and all logic sits in `@Service` classes; invariants then get enforced in several places or not at all.
- Aggregates that are too big (`Customer` with all its orders) lock and load far too much; keep them small and reference other aggregates by id, not by object ([[jpa-entities-and-relationships]], [[jpa-n-plus-one]]).
- Mapping an aggregate 1:1 to a JPA entity graph with cascades makes the ORM, not the domain, define the consistency boundary ([[jpa-persistence-context-and-entity-lifecycle]]).
- Publishing domain events directly from the service before commit loses them on rollback; use the [[outbox-pattern]] and make consumers [[idempotency|idempotent]].
- Related: [[solid]], [[architecture-resources]].
