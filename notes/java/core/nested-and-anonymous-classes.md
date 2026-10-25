---
title: Nested, inner, local, anonymous classes and lambdas
tags: [nested-classes, inner-classes, anonymous-classes, lambdas, closures]
learned: 2026-10-22
---
## TL;DR
A class declared inside another class is **nested**. A `static` nested class is just a namespaced top-level class; a non-static **inner** class carries a hidden reference to an enclosing instance. Local and anonymous classes are inner classes declared inside a method. Lambdas look like anonymous classes but are not classes at all: no extra `.class` file, `this` is the enclosing object, and they can only implement functional interfaces.

## Key points
### The four kinds
| Kind | Declared | Holds outer instance | Can have static members | Typical use |
|---|---|---|---|---|
| Static nested | `static class In` inside `Out` | no | yes | helper types, builders, `Map.Entry`, `HashMap.Node` |
| Inner (non-static) | `class In` inside `Out` | yes (`Out.this`) | since Java 16 | iterators, views that need the outer state (`ArrayList.Itr`) |
| Local | inside a method or block | yes if in an instance method | since Java 16 | one-off helper visible only in that method |
| Anonymous | `new Type() { ... }` expression | yes if in an instance context | no | one-off implementation of an interface or subclass |

- Nested classes exist to serve the enclosing class; the compiler emits them as `Out$In.class`, `Out$1.class`.
- Access: a nested class sees all members of the outer class, including `private`, and the outer sees the nested class's privates too (since Java 11 via *nestmates*; before that, through synthetic accessor methods). The source says "but not the other way round"; that is wrong, access is mutual. What *is* true is that nesting weakens encapsulation: the outer class's private state is visible to code that is not in the outer class body.
- Creating an inner instance from outside: `outer.new Inner()`. Inside an inner class, `Out.this` names the enclosing instance.

### Anonymous classes
- An expression that declares and instantiates an unnamed subclass of a class or implementation of an interface in one go: `new Comparator<String>() { public int compare(...) {...} }`.
- Can extend a class **or** implement one interface, not both; can override any method including `Object`'s (`toString`); can have fields and initializer blocks but no constructors (use an initializer block or pass arguments to the superclass constructor).
- `this` refers to the anonymous object itself.
- Captured local variables must be final or effectively final ([[modifiers-final-static-access]]): the compiler copies them into synthetic fields.
- The class is defined at compile time, not "created at runtime" as the source says; what happens at runtime is ordinary class loading.

### Lambdas vs anonymous classes
- A lambda is an implementation of a functional interface (exactly one abstract method, [[functional-interfaces]]) in short form. It cannot implement an abstract class, even one with a single abstract method.
- Forms: expression body `x -> x * 2` and block body `x -> { ...; return y; }`.
- `this` inside a lambda is the enclosing instance; a lambda has no identity of its own and cannot refer to itself.
- No class file: `invokedynamic` + `LambdaMetafactory` spin a hidden class at first call; a non-capturing lambda is a singleton, a capturing one allocates per evaluation. Anonymous classes allocate every time and keep an outer reference.
- A lambda cannot shadow local variables of the enclosing scope; an anonymous class can.
- Method references (`String::length`, `this::handle`, `Order::new`) are lambdas without a body: the receiver of an unbound reference becomes the first parameter.

### Memory
- Every inner, local and anonymous class instance holds `this$0`, the outer instance. A long-lived listener or cache entry created as an inner class keeps the whole outer object alive ([[garbage-collection]]). Make nested classes `static` unless they need the outer state; this is Effective Java item 24 and a common IDE warning.

## Example
```java
public class RetryPolicy {
    private final int maxAttempts;
    private final Duration backoff;

    public RetryPolicy(int maxAttempts, Duration backoff) { this.maxAttempts = maxAttempts; this.backoff = backoff; }

    // Static nested: no outer state needed, just a namespace
    public record Attempt(int number, Throwable failure) {}

    // Inner: needs maxAttempts and backoff of this policy
    private final class Loop implements Iterator<Duration> {
        private int attempt = 0;
        public boolean hasNext() { return attempt < maxAttempts; }
        public Duration next() { return backoff.multipliedBy(1L << attempt++); }   // RetryPolicy.this.backoff
    }

    public Iterator<Duration> delays() { return new Loop(); }

    // Lambda where a functional interface is enough; 'this' here is the RetryPolicy
    public Runnable wrap(Runnable task, Consumer<Attempt> onFailure) {
        return () -> {
            int n = 0;
            for (Iterator<Duration> it = delays(); ; ) {
                try { task.run(); return; }
                catch (RuntimeException e) {
                    onFailure.accept(new Attempt(++n, e));
                    if (!it.hasNext()) throw e;
                    LockSupport.parkNanos(it.next().toNanos());
                }
            }
        };
    }
}
```

## Pitfalls
- Inner classes in entities, Jackson DTOs or JUnit nested tests without `static` fail with "no default constructor" or "cannot instantiate", because the framework has no outer instance to pass.
- Serializing an object with an anonymous or inner class field serializes the outer instance too, or fails with `NotSerializableException`.
- An anonymous class capturing a loop variable needs a fresh effectively final copy per iteration; the classic "all handlers see the last value" bug comes from capturing a mutable field instead.
- Related: [[functional-interfaces]], [[modifiers-final-static-access]], [[structural-patterns]] (anonymous Adapter/Strategy implementations).
