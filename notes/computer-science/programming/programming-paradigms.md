---
title: Programming paradigms
tags: [programming, paradigms, oop, functional, declarative]
learned: 2026-10-27
---
## TL;DR
A paradigm is a way of structuring programs: **imperative** code says how (statements that mutate state), **declarative** code says what (SQL, Spring config, Kubernetes manifests), **object-oriented** code groups state with the operations on it behind interfaces, and **functional** code builds results from pure functions and immutable values. Modern languages are multi-paradigm: idiomatic Java since 8 is OOP for structure, functional for data transformation (streams, lambdas, records, sealed types and pattern matching), and declarative wherever a framework does the work (annotations, SQL, YAML).

## Key points

### The map
| Paradigm | Unit of composition | State | Control flow | Typical languages / places |
|---|---|---|---|---|
| Imperative / procedural | procedure | mutable, shared | loops, conditionals, explicit sequence | C, Go, scripts, the body of most Java methods |
| Object-oriented | object (state + behaviour) | encapsulated, mutable or immutable | message passing / method calls, polymorphism | Java, C#, Kotlin; domain models, Spring beans |
| Functional | pure function | immutable values | expressions, recursion, higher-order functions | Haskell, Clojure, Scala; Java streams, Kotlin collections |
| Declarative | rule / query / spec | none visible | engine decides | SQL, regex, Prolog, HTML, Spring Data query methods, K8s manifests |
| Logic | predicate + facts | — | unification, search | Prolog, Datalog, some rule engines |
| Event-driven / reactive | handler, stream | per handler | callbacks, pipelines, backpressure | Node.js, Project Reactor, Kafka consumers ([[event-driven-architecture]]) |
| Concurrent models (actors, CSP) | actor / goroutine | isolated per actor | messages, channels | Erlang/Akka, Go ([[actor-and-message-passing-models]]) |

### Imperative vs declarative
- Imperative: `for (Order o : orders) if (o.status() == PAID) total += o.amount();`.
- Declarative: `SELECT sum(amount) FROM orders WHERE status = 'PAID'` or `orders.stream().filter(...).mapToLong(...).sum()`. The planner or the stream pipeline chooses the execution ([[query-planning-and-execution]]).
- Declarative wins when the engine can optimize (SQL, Kubernetes reconciliation) or when there is one obvious execution; imperative wins when you need control over order, memory and side effects.

### Object-oriented
- Pillars: encapsulation, inheritance, polymorphism, abstraction; covered in [[oop-principles]], with [[solid]] as the design rules.
- Prefer composition over inheritance; model behaviour behind interfaces so that implementations (Kafka vs in-memory, Postgres vs test double) can swap ([[hexagonal-architecture]]).
- Where OOP shines: long-lived domain state with invariants ([[aggregates-and-invariants]]), plugin points, frameworks. Where it hurts: deep hierarchies, anemic models that are just structs plus a service full of procedures.

### Functional
- **Pure function**: output depends only on inputs, no side effects; trivially testable, cacheable, parallelizable ([[immutability-and-side-effects]]).
- **Higher-order functions** take or return functions: `map`, `filter`, `reduce`, `Comparator.comparing`.
- **Immutability** and **referential transparency** make concurrency reasoning simple.
- **Algebraic data types** (sum and product types): Java 17 sealed interfaces + records + `switch` pattern matching give exhaustive handling of a closed set of cases, which is the FP way to model events and results ([[error-handling-strategies]], [[type-systems]]).
- In Java: lambdas and method references (Java 8), `Stream`, `Optional`, `CompletableFuture` as a monad-like pipeline, records (16), sealed types (17), pattern matching for `switch` (21). See [[functional-interfaces]], [[stream-api]].
- Limits in Java: no tail-call optimization (deep recursion overflows, [[recursion-and-backtracking]]), checked exceptions do not compose with lambdas, no persistent collections in the JDK.

### Where Java sits
- Class-based, statically typed OOP at the core; everything is in a class, but `static` utility classes and records are procedural and data-oriented in practice.
- Functional style for data transformation and callbacks; imperative loops still preferred for hot paths and when a loop reads clearer than a long stream chain.
- Declarative through annotations and frameworks: `@Transactional`, `@Cacheable`, Spring Data query derivation, Bean Validation; the framework turns the declaration into imperative code via proxies ([[spring-aop]]).
- "Data-oriented programming" (Brian Goetz, Java 21 era): model data as immutable records and sealed hierarchies, keep behaviour in functions that switch over them; a middle ground between OOP and FP that fits services well.

### Interview angles
- "OOP vs FP?" Not a contest: OOP organizes state and extension points, FP organizes transformations; a Spring service uses both.
- "Is SQL a programming language?" Declarative, Turing-complete with recursive CTEs, executed by a planner.
- "Why immutability?" Thread safety and local reasoning ([[immutability-and-side-effects]]).

## Example
The same business rule in three styles; in a real service the sealed-type version is the one to keep.

```java
// 1. Imperative: how, step by step, mutable accumulator
static long paidTotal(List<Order> orders) {
    long total = 0;
    for (Order o : orders) {
        if (o.status() == Status.PAID) total += o.amountMinor();
    }
    return total;
}

// 2. Functional: what, as a pipeline of pure steps
static long paidTotalFp(List<Order> orders) {
    return orders.stream()
            .filter(o -> o.status() == Status.PAID)
            .mapToLong(Order::amountMinor)
            .sum();
}

// 3. Data-oriented: closed set of cases, exhaustive switch (Java 21)
sealed interface PaymentEvent permits Authorized, Captured, Refunded {}
record Authorized(String id, long amountMinor) implements PaymentEvent {}
record Captured(String id, long amountMinor) implements PaymentEvent {}
record Refunded(String id, long amountMinor, String reason) implements PaymentEvent {}

static long ledgerDelta(PaymentEvent e) {
    return switch (e) {                       // compiler enforces all cases
        case Authorized a -> 0;
        case Captured c   -> c.amountMinor();
        case Refunded r   -> -r.amountMinor();
    };
}
```

## Pitfalls
- "Functional" Java that mutates captured state inside `forEach` or `map` combines the worst of both styles: hidden side effects and no exhaustiveness; keep lambdas pure.
- Inheritance for code reuse (`BaseService` with 20 protected helpers) instead of composition; the hierarchy becomes the coupling point ([[coupling-cohesion-and-general-principles]]).
- Over-declarative magic: a Spring Data method name with six `And` clauses or a 200-line annotation stack is harder to debug than the SQL it hides.
- Checked exceptions inside stream lambdas force wrapper boilerplate; design APIs at the boundary with unchecked exceptions or result types ([[error-handling-strategies]]).
- Related: [[oop-principles]] and [[solid]] for the OOP side, [[stream-api]] and [[functional-interfaces]] for the functional toolkit, [[type-systems]] for how sealed types and generics fit.
