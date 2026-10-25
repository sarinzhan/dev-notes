---
title: Abstract classes and interfaces
tags: [abstract-class, interface, default-methods, sealed, functional-interface]
learned: 2026-10-22
---
## TL;DR
An abstract class is a partially implemented base for a family of related classes ("is-a", shared state and constructor); an interface is a contract that unrelated classes can fulfil (a capability, multiple per class). Since Java 8 interfaces can carry `default` and `static` method bodies, since 9 `private` helper methods, and since 17 both can be `sealed` to restrict who implements them.

## Key points
### Abstract class
- Declared `abstract`; cannot be instantiated; may contain abstract methods (no body, marked `abstract`) and concrete ones, fields with any modifier, constructors (called by subclass constructors via `super(...)`), static members.
- An abstract method cannot be `final`, `private` or `static`: all three would make overriding impossible, which is the whole point.
- A class with at least one abstract method must itself be abstract; an abstract class may have none (used to forbid direct instantiation).
- Single inheritance: a class extends exactly one class but may implement many interfaces.

### Interface
- A set of rules (contract) that an implementing class must follow.
- Fields are implicitly `public static final` (constants). Abstract methods are implicitly `public abstract`; since Java 9 a method can also be `private` (helper for defaults).
- Java 8 added `default` methods (instance methods with a body, inherited by implementors) and `static` methods (not inherited; call as `Interface.m()`).
- An interface can extend several interfaces. It has no constructor and no instance state.
- A `default` or `static` interface method cannot be declared `synchronized` or `final` in its signature; `synchronized` is allowed only as a block inside the body. `final` is excluded on purpose: a class must stay free to override a default.

### Why default methods were added
- To evolve interfaces without breaking every implementation: `Collection.stream()`, `Iterable.forEach`, `List.sort` were added to the JDK this way.
- To ship utilities with functional interfaces: `Comparator.thenComparing`, `Predicate.and`, `Function.andThen` ([[functional-interfaces]]).
- To reduce duplication: a default implementation written once in the interface instead of in every implementor.

### Conflicting defaults
- Two interfaces with the same default signature → the class must override the method and may pick one with `A.super.m()`.
- If one interface extends the other and overrides the default, the more specific one wins and the class does nothing.
- A superclass method always beats an interface default ("class wins").

### Functional interfaces
- Exactly one abstract method (`@FunctionalInterface` makes the compiler check). It may have any number of defaults and statics, and may extend another interface as long as the total number of abstract methods stays one; the source's "all parent methods must be overridden" is wrong, they just must not add a second abstract method. Methods matching `Object`'s public methods (`equals`) do not count.
- Lambdas and method references implement them ([[functional-interfaces]]); lambdas cannot implement an abstract class even with a single abstract method.

### Choosing between them
| | Abstract class | Interface |
|---|---|---|
| Relationship | "is-a", a hierarchy of similar classes | "can-do", unrelated classes share a capability |
| State | fields, constructors | constants only |
| Multiplicity | one per class | many per class |
| Evolution | add a concrete method freely | add a `default` method |
| Typical | `AbstractList`, `InputStream`, Spring's `AbstractController` | `Comparable`, `Runnable`, `UserRepository` |

- `sealed` (Java 17) works on both: `sealed interface Shape permits Circle, Square` lets a `switch` with pattern matching (21) be exhaustive without `default`.

## Example
```java
public interface Notifier {
    void send(String recipient, String text);

    default void sendAll(Collection<String> recipients, String text) {   // Java 8 default
        recipients.forEach(r -> send(r, normalize(text)));
    }

    static Notifier noop() { return (r, t) -> {}; }                      // Java 8 static

    private String normalize(String text) { return text.strip(); }      // Java 9 private helper
}

public abstract class RetryingNotifier implements Notifier {
    private final int attempts;                                          // state: only a class can hold it
    protected RetryingNotifier(int attempts) { this.attempts = attempts; }

    @Override public final void send(String recipient, String text) {    // template method
        for (int i = 1; ; i++) {
            try { doSend(recipient, text); return; }
            catch (IOException e) { if (i == attempts) throw new UncheckedIOException(e); }
        }
    }
    protected abstract void doSend(String recipient, String text) throws IOException;
}
```

## Pitfalls
- Interface constants are a code smell ("constant interface antipattern"); use an `enum` or a final class.
- Default methods cannot override `equals`/`hashCode`/`toString`, and cannot access state; if the default needs state, it belongs in an abstract class.
- Adding an abstract method to a published interface breaks every implementor; adding a default silently changes behaviour for implementors that already had a method with that name.
- Related: [[oop-principles]], [[methods-overloading-overriding]], [[enums]].
