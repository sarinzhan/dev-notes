---
title: SOLID principles
tags: [solid, design-principles, oop, architecture]
learned: 2026-10-22
---
## TL;DR
SOLID is five object-oriented design rules that keep code easy to change: one reason to change per unit (**S**), extend without editing (**O**), subtypes must honour the base contract (**L**), small client-specific interfaces (**I**), and depend on abstractions, not implementations (**D**). They are *principles* (goals and rules), not patterns; patterns such as [[structural-patterns|Decorator]] or [[behavioral-patterns|Strategy]] are concrete ways to satisfy them.

## Key points
### Single responsibility (SRP)
- Each unit (class, module, service) has exactly one area of responsibility and therefore one reason to change.
- A `UserService` that validates input, writes to the database and sends emails changes for three reasons. Split it into a validator, a repository and a notifier.
- At service level the same idea gives bounded contexts ([[ddd-basics]]).

### Open/closed (OCP)
- Code should be open for extension but closed for modification: add behaviour by adding a new class, not by editing a working one.
- The usual tool is an interface plus polymorphism: new payment provider = new `PaymentProvider` implementation, the checkout code does not change.
- In Spring this is what makes `@Bean` lists, `List<Handler>` injection and auto-configuration work ([[spring-ioc-and-di]]).

### Liskov substitution (LSP)
- Anywhere the base type is used, any subtype must be usable without the program noticing. Subclasses must keep the parent's contract: no stronger preconditions, no weaker postconditions, no new exceptions the parent did not promise, no broken invariants.
- Classic violations: `Square extends Rectangle` where `setWidth` silently changes the height; a `ReadOnlyList` that throws from `add()`; a repository subclass that returns `null` where the parent promises an empty list.
- Test for it: run the parent's test suite against the child.

### Interface segregation (ISP)
- Clients must not depend on methods they do not use. Prefer several narrow interfaces to one wide one.
- A `Printer` with `print`, `scan`, `fax` forces a simple printer to throw `UnsupportedOperationException`; split it into `Printer`, `Scanner`, `Fax`.
- In the JDK the `Collection` family mostly follows this (`Iterable` -> `Collection` -> `List`); `java.sql.ResultSet` with ~190 methods does not ([[collections-framework]]).

### Dependency inversion (DIP)
- High-level modules must not depend on low-level modules; both depend on abstractions. Abstractions must not depend on details; details depend on abstractions.
- Practically: business code depends on the `OrderRepository` interface, the JPA implementation depends on that interface too. This makes test doubles trivial and enables TDD ([[testing-types-and-dev-cycles]]).
- **Dependency injection** is the technique that implements DIP: dependencies are passed in from outside (constructor, container) instead of being `new`-ed inside the object. Spring's container is a DI implementation ([[spring-ioc-and-di]], [[spring-bean-lifecycle]]).
- DIP is the principle, IoC is the general idea of giving control to a framework, DI is the mechanism. Do not use them as synonyms in an interview.

## Example
```java
// DIP + OCP: the use case depends on an abstraction; providers are added without editing it.
public interface PaymentProvider {
    boolean supports(Currency currency);
    PaymentResult charge(Money amount, CardToken token);
}

@Service
public class CheckoutService {
    private final List<PaymentProvider> providers;   // all implementations injected by Spring

    public CheckoutService(List<PaymentProvider> providers) {
        this.providers = providers;
    }

    public PaymentResult pay(Order order, CardToken token) {
        return providers.stream()
                .filter(p -> p.supports(order.currency()))
                .findFirst()
                .orElseThrow(() -> new UnsupportedCurrencyException(order.currency()))
                .charge(order.total(), token);
    }
}

@Component
class KgsProvider implements PaymentProvider { /* ... */ }   // new currency = new class, zero edits above
```

## Pitfalls
- SRP taken to the extreme produces hundreds of one-method classes; "one reason to change" is about the *actor* that requests the change, not about line count.
- OCP does not mean "never modify"; it means modifications should not ripple. Adding an interface for a class with exactly one implementation and no test double is speculative generality (YAGNI).
- LSP violations hide behind compiler-valid code; `instanceof` checks in callers are the usual smell that a subtype broke the contract.
- Fat interfaces grow silently in Spring Data repositories when every query lands in one interface; split by use case ([[spring-data-repositories]]).
- Related: [[oop-principles]], [[class-relationships]], [[creational-patterns]].
