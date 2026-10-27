---
title: Aggregates, invariants and the transactional boundary
tags: [ddd, aggregates, consistency, jpa]
learned: 2026-10-27
---
## TL;DR
An **aggregate** is the cluster of objects that must be consistent *together*; its **root** is the only entry point, and its **invariants** are the rules that must hold at the end of every transaction. The practical rule set: one aggregate per transaction, reference other aggregates by id, keep aggregates small, and let anything that spans aggregates become eventually consistent through [[domain-events-and-integration-events|domain events]]. The aggregate id is also your unit of locking, of Kafka partitioning and of the outbox key.

## Key points

### Designing the boundary
- Start from the invariants, not from the tables. "A SIM has at most one active profile" is an invariant between `Sim` and its `Profile`s, so they live in one aggregate. "A subscriber has at most 5 SIMs" sounds like an invariant on `Subscriber`, but loading all SIMs to add one is a scalability trap; it is usually a *policy* checked with a count query and enforced eventually.
- Ask: must these objects change in the same transaction to keep the business rule true? Yes: same aggregate. No: separate aggregates, id reference, event.
- Small aggregates win: `Order` with its lines is fine, `Customer` with all orders is not. An aggregate is what you can load, check and save in one short transaction without blocking other users.
- Everything inside is reachable only through the root. Internal entities have local identity (`OrderLine` number inside the order), not global ids used elsewhere.

### Invariants and where they live
- Invariants are enforced by the root's methods, not by a service and not by the database alone. A `confirm()` that throws when the order has no lines is the invariant; the `CHECK` constraint is defence in depth ([[constraints-and-relationships]]).
- Constructor and factory methods make an invalid aggregate unrepresentable: `Order.create(customerId, currency)` instead of `new Order()` + setters ([[creational-patterns]], [[value-objects-and-entities]]).
- Pull events, do not push: the root records what happened (`events.add(new ProfileActivated(...))`); the application layer publishes after commit ([[use-cases-and-application-layer]]).

### One aggregate per transaction
- A use case loads one aggregate, calls one method, saves it. Changing two aggregates in one transaction means the transaction (and its locks) spans two consistency units; it works on one database but breaks the moment the aggregates land in two services ([[saga]]).
- Cross-aggregate consistency: the first aggregate's domain event triggers a handler that changes the second. The delay is milliseconds in-process, seconds through Kafka; the business must accept that window.
- Exceptions exist (both aggregates are tiny and always co-located), but state them explicitly.

### Concurrency
- Optimistic locking with a `@Version` column on the root is the default; two users editing the same aggregate get `OptimisticLockException` and one retries ([[jpa-locking]]).
- Pessimistic `SELECT ... FOR UPDATE` on the root row when contention is expected (balance debit) ([[postgres-locks]]).
- Because the root row is the lock, a big aggregate serialises unrelated users: another reason to keep them small.

### Aggregates and infrastructure
| Concern | Aggregate id is used as |
|---|---|
| Kafka | Record key, so all events of one aggregate stay ordered on one partition ([[kafka-producer]]) |
| Outbox | `aggregate_id` column, ordering key for the relay ([[outbox-pattern]]) |
| Idempotency | Part of the dedup key for commands ([[idempotency]]) |
| Sharding | Natural shard key: an aggregate never spans shards ([[sharding-strategies]]) |
| Temporal | `workflowId` for one long-running flow per aggregate ([[temporal-concepts]]) |

## Example
`Sim` aggregate with the "one active profile" invariant, a version for optimistic locking and recorded domain events.

```java
@Entity
@Table(name = "sim")
public class Sim {
    @Id private UUID id;
    @Version private long version;
    private String msisdn;

    @ElementCollection(fetch = FetchType.EAGER)      // small, owned by the root
    @CollectionTable(name = "sim_profile", joinColumns = @JoinColumn(name = "sim_id"))
    private List<Profile> profiles = new ArrayList<>();

    @Transient private final List<DomainEvent> events = new ArrayList<>();

    protected Sim() {}                               // JPA only

    public static Sim register(UUID id, Msisdn msisdn) {
        var sim = new Sim();
        sim.id = id;
        sim.msisdn = msisdn.value();
        return sim;
    }

    public void activate(Iccid iccid) {
        Profile target = profiles.stream().filter(p -> p.iccid().equals(iccid.value())).findFirst()
                .orElseThrow(() -> new ProfileNotFoundException(iccid));
        boolean anotherActive = profiles.stream()
                .anyMatch(p -> p.state() == ProfileState.ACTIVE && !p.iccid().equals(iccid.value()));
        if (anotherActive) throw new InvariantViolation("a SIM can have only one active profile");  // invariant
        target.markActive();
        events.add(new ProfileActivated(id, iccid, Instant.now()));
    }

    public List<DomainEvent> pullEvents() {
        var copy = List.copyOf(events);
        events.clear();
        return copy;
    }
}
```

The application service: `Sim sim = sims.findById(id).orElseThrow(); sim.activate(iccid); sims.save(sim); publisher.publish(sim.pullEvents());` inside one `@Transactional` method ([[spring-transactional]]).

## Pitfalls
- Anemic aggregate: public setters on the root, invariant checked in `SimService.activate()`. The next caller forgets the check.
- `@OneToMany` to another aggregate's entities with `cascade = ALL`: the ORM graph, not the business rule, now defines the transaction boundary and loads half the database ([[jpa-entities-and-relationships]], [[jpa-n-plus-one]]).
- Checking an invariant with a query (`countActiveProfiles(simId) == 0`) outside the aggregate's lock is a race: two concurrent activations both see zero ([[race-conditions-and-critical-sections]]).
- Mutable collections exposed through getters (`getProfiles().add(...)`) bypass the root; return unmodifiable views.
- Related: [[ddd-basics]], [[value-objects-and-entities]], [[domain-events-and-integration-events]], [[event-sourcing]] (aggregates rebuilt from events).
