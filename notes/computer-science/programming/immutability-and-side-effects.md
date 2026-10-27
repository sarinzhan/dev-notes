---
title: Immutability and side effects
tags: [programming, immutability, side-effects, pure-functions, concurrency]
learned: 2026-10-27
---
## TL;DR
An immutable value cannot change after construction; a pure function returns a result that depends only on its arguments and changes nothing else. Together they remove whole classes of bugs: no aliasing surprises, no "who changed this?", and thread safety for free because there is nothing to race on. The practical rule for a backend: keep data (DTOs, events, commands, config, domain values) immutable, keep side effects (database writes, HTTP calls, publishing, logging, clock, random) at the edges of a use case, and let the middle be pure functions over immutable inputs. The Java recipe (final fields, defensive copies, records, `List.copyOf`) is in [[immutable-objects]].

## Key points

### Definitions
- **Side effect**: any observable change beyond the return value: mutating an argument or a field, I/O, writing a log, reading the clock, throwing for control flow.
- **Pure function**: deterministic and side-effect free. `Math.max`, `String.toUpperCase`, `LocalDate.plusDays`, your price calculator. Not pure: `LocalDate.now()`, `random.nextInt()`, `repo.save`, anything that logs.
- **Referential transparency**: a call can be replaced by its result without changing the program; this is what makes caching, memoization ([[dynamic-programming]]) and parallel evaluation safe.
- **Immutable** vs **effectively immutable** (never mutated after publication) vs **unmodifiable view** (`Collections.unmodifiableList` over a list the owner still mutates).

### What immutability buys
| Problem | With mutable state | With immutable values |
|---|---|---|
| Thread safety | locks, volatile, happens-before reasoning ([[java-memory-model]]) | safe to share; `final` fields are safely published by the JMM |
| Aliasing | a method mutates the list you passed in | callers keep their data |
| Hash keys | key changes, entry lost ([[hash-tables]]) | stable `hashCode` |
| Caching | stale copies, invalidation | cache the value itself |
| Debugging | "who set this to null?" | state only changes by creating new values; history is explicit |
| Testing | setup of hidden state, order-dependent tests | call function, assert result |
| Undo / audit / replay | diff hard to reconstruct | keep old versions ([[event-sourcing]]) |

### The cost
- Allocation: `withX(...)` creates a new object per change. In Java this is cheap (TLAB bump allocation, young-gen GC, [[garbage-collection]]); it only matters in tight loops or for large collections, where a builder or a persistent collection (structural sharing, Vavr/PCollections) helps.
- Verbosity before records; records and Lombok `@Value`/`@With` fix most of it.
- Deep immutability needs discipline: a record with a `List` field is only as immutable as that list (`List.copyOf` in the compact constructor).

### Where side effects should live
- **Functional core, imperative shell**: parse and validate input → pure decision (what to charge, which state to move to, which events to emit) → effects (save, publish, call). The core is unit-testable without mocks; the shell is thin and tested with integration tests ([[use-cases-and-application-layer]], [[hexagonal-architecture]]).
- Inject the clock and randomness (`Clock`, `Supplier<UUID>`) so the core stays deterministic and tests can control time ([[testing-async-and-kafka-flows]]).
- Idempotent effects: when a side effect must happen at least once, make it safe to repeat ([[idempotency]]); Temporal activities are exactly "side effects isolated from deterministic workflow code" ([[temporal-concepts]]).
- Events and messages are immutable facts by definition; never mutate a consumed Kafka record or a domain event ([[domain-events-and-integration-events]]).

### Mutability where it belongs
- Local builders and accumulators inside one method (`StringBuilder`, a local `ArrayList`) are fine: not shared, not escaping.
- Entities with identity and lifecycle (JPA entities, aggregates) are mutable by design; keep the mutation inside the aggregate's methods and protect invariants ([[aggregates-and-invariants]], [[jpa-persistence-context-and-entity-lifecycle]]).
- Performance-critical buffers, ring buffers, `ByteBuffer` ([[stacks-and-queues]], [[java-nio]]).

### Language support
- Java: `final`, records (16), `List.of`/`Map.of`/`copyOf` (9–10), `String`, `java.time`, `BigDecimal`; no `const` for deep immutability; see [[immutable-objects]].
- Kotlin: `val`, data classes with `copy`, read-only `List` interface. Rust: everything immutable by default, mutability is explicit and single-owner. Go: no immutability, convention only.

## Example
Functional core (pure tariff decision over immutable inputs) and imperative shell (effects), with time injected.

```java
record Subscriber(String msisdn, Plan plan, long balanceMinor, Instant activatedAt) {
    Subscriber withBalance(long newBalanceMinor) {
        return new Subscriber(msisdn, plan, newBalanceMinor, activatedAt);
    }
}

sealed interface ChargeDecision permits Charged, Declined {}
record Charged(Subscriber updated, long amountMinor) implements ChargeDecision {}
record Declined(String reason) implements ChargeDecision {}

/** Pure: same inputs, same output; no I/O, no clock, no logging. */
static ChargeDecision decideCharge(Subscriber s, long amountMinor, Instant now) {
    if (now.isBefore(s.activatedAt().plus(Duration.ofDays(1))) && s.plan().freeFirstDay())
        return new Charged(s, 0);
    if (s.balanceMinor() < amountMinor)
        return new Declined("insufficient balance");
    return new Charged(s.withBalance(s.balanceMinor() - amountMinor), amountMinor);
}

/** Imperative shell: loads, decides, applies effects. */
@Service
@RequiredArgsConstructor
class ChargeService {
    private final SubscriberRepository repo;
    private final KafkaTemplate<String, ChargeEvent> kafka;
    private final Clock clock;                         // injected so tests control time

    @Transactional
    public ChargeDecision charge(String msisdn, long amountMinor) {
        Subscriber s = repo.findByMsisdn(msisdn).orElseThrow();
        ChargeDecision d = decideCharge(s, amountMinor, clock.instant());
        if (d instanceof Charged c) {
            repo.save(c.updated());                     // effect 1
            kafka.send("billing.charged", msisdn, new ChargeEvent(msisdn, c.amountMinor())); // effect 2 (outbox in real code)
        }
        return d;
    }
}
```

Testing `decideCharge` needs no Spring, no mocks and no database; the shell is covered once by an integration test.

## Pitfalls
- A record or `final` class holding a mutable `List`/`Date`/array is not immutable; copy on the way in and return unmodifiable views ([[immutable-objects]]).
- `Collections.unmodifiableList(list)` is a view: the owner can still mutate `list`, and readers see the change; `List.copyOf` makes a real copy.
- Hidden side effects in getters, `equals`, `hashCode` or `compareTo` (lazy loading a JPA relation inside `equals`, logging inside `hashCode`) cause N+1 queries and non-deterministic collections ([[jpa-n-plus-one]], [[equals-and-hashcode]]).
- Mutating a shared object inside a parallel stream or a `CompletableFuture` chain is a data race even if each step "looks" sequential ([[race-conditions-and-critical-sections]]).
- Related: [[immutable-objects]] for the Java recipe, [[java-memory-model]] for why `final` fields publish safely, [[programming-paradigms]] for the functional view.
