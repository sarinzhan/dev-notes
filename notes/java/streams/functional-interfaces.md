---
title: Functional interfaces and lambdas
tags: [functional, lambda, method-reference, java.util.function]
learned: 2026-10-22
---
## TL;DR
A functional interface has exactly one abstract method, which is what lets a lambda or a method reference stand in for it. `java.util.function` ships the standard shapes (`Function`, `Predicate`, `Consumer`, `Supplier` and friends); `@FunctionalInterface` makes the compiler enforce the single-method rule.

## Key points
### The standard shapes
| Interface | Signature | Reads as |
|---|---|---|
| `Function<T,R>` | `R apply(T)` | transform |
| `BiFunction<T,U,R>` | `R apply(T,U)` | combine two |
| `UnaryOperator<T>` | `T apply(T)` | same type in and out |
| `BinaryOperator<T>` | `T apply(T,T)` | reduce two of the same type |
| `Predicate<T>` | `boolean test(T)` | filter |
| `Consumer<T>` | `void accept(T)` | side effect |
| `BiConsumer<T,U>` | `void accept(T,U)` | e.g. `Map.forEach` |
| `Supplier<T>` | `T get()` | lazy value, factory |
| `Runnable` / `Callable<V>` | `void run()` / `V call()` | tasks, see [[threads-basics]] |
Primitive variants avoid boxing: `IntPredicate`, `IntFunction<R>`, `ToIntFunction<T>`, `IntUnaryOperator`, `IntSupplier`, etc.

### Composition
- `Function`: `andThen`, `compose`, `Function.identity()`.
- `Predicate`: `and`, `or`, `negate`, `Predicate.not(p)` (Java 11).
- `Comparator` is a functional interface too: `comparing`, `thenComparing`, `reversed`.

### Lambdas and method references
- A lambda `(a, b) -> a + b` is converted to whichever functional interface the context expects (target typing).
- Method references: static `Integer::parseInt`, bound instance `str::length`, unbound instance `String::length`, constructor `ArrayList::new`.
- Captured local variables must be effectively final; lambdas do not get their own `this` (unlike anonymous classes).
- Default and static methods are allowed in a functional interface; only the abstract-method count matters. A functional interface may extend another interface as long as it still has one abstract method.

## Example
```java
@FunctionalInterface
interface Validator<T> {
    Optional<String> validate(T value);

    default Validator<T> and(Validator<T> other) {
        return v -> validate(v).or(() -> other.validate(v));
    }
}

Validator<String> notBlank = s -> s.isBlank() ? Optional.of("blank") : Optional.empty();
Validator<String> maxLen   = s -> s.length() > 20 ? Optional.of("too long") : Optional.empty();
Optional<String> error = notBlank.and(maxLen).validate(input);
```

## Pitfalls
- Checked exceptions cannot escape a lambda targeting the standard interfaces; wrap them or define your own interface with `throws`.
- `Function<Integer,Integer>` boxes on every call; in hot loops use `IntUnaryOperator`.
- Long lambdas hurt readability and stack traces; extract a method and use a reference.
- Used everywhere in [[stream-api]] and in [[completable-future]].
