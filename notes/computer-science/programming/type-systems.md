---
title: Type systems
tags: [programming, type-systems, generics, variance, static-typing]
learned: 2026-10-27
---
## TL;DR
A type system assigns types to values and rejects programs that combine them wrongly. **Static** typing checks at compile time (Java, Go, Rust, TypeScript); **dynamic** typing checks at run time (Python, JavaScript, Clojure). **Strong** vs **weak** is about how freely values convert: Java and Python are strong (no silent `"1" + 1 → "11"`-style surprises beyond string concatenation), JavaScript and C are weak. Generics add parametric polymorphism (`List<T>`), and **variance** says whether `List<Dog>` may be used as `List<Animal>` (it may not, which is why Java has `? extends` and `? super`). The Java specifics (erasure, wildcards, PECS) live in [[generics]]; this note is the general picture.

## Key points

### The two axes
| | Static | Dynamic |
|---|---|---|
| **Strong** | Java, Kotlin, Go, Rust, Scala, Haskell | Python, Ruby, Clojure, Erlang |
| **Weak** | C, C++ (implicit casts, pointer punning) | JavaScript, PHP, Perl |

- Static: errors found before running; IDE navigation, refactoring and autocompletion; types as documentation; faster code because the compiler knows layouts. Cost: ceremony, slower prototyping, some valid programs rejected.
- Dynamic: concise, flexible, duck typing; errors found by tests or in production. Gradual typing (TypeScript, Python type hints + mypy) adds static checks back on top.
- Strong/weak is a spectrum, not a switch: Java allows `int → long` widening and `String + anything` but not `int + String → int`.

### Kinds of typing you will be asked about
| Term | Meaning | Java |
|---|---|---|
| Nominal | compatibility by declared name/hierarchy | yes: `implements Comparable` |
| Structural | compatibility by shape | no (TypeScript, Go interfaces are structural) |
| Duck typing | "if it quacks": structural, at run time | no; reflection or `instanceof` only |
| Type inference | compiler deduces types | `var` (10), lambda parameters, diamond `<>`, generic method arguments |
| Parametric polymorphism | one code for many types | generics ([[generics]]) |
| Ad-hoc polymorphism | overloading, type classes | overloading ([[methods-overloading-overriding]]); no type classes |
| Subtype polymorphism | a subtype can stand in for a supertype | inheritance, interfaces ([[oop-principles]]) |
| Algebraic data types | sum (one of) and product (all of) types | sealed interfaces (17) + records (16) |
| Null safety | the type says whether null is allowed | not in Java (`Optional`, `@Nullable` annotations, JSpecify); Kotlin `String?` |

### Variance
Given `Dog <: Animal`, how does `F<Dog>` relate to `F<Animal>`?
| Variance | Relation | Safe for | Java syntax |
|---|---|---|---|
| Invariant | no relation | reading and writing | `List<Animal>` (default) |
| Covariant | `F<Dog> <: F<Animal>` | reading (producer) | `List<? extends Animal>`, arrays (unsoundly) |
| Contravariant | `F<Animal> <: F<Dog>` | writing (consumer) | `List<? super Dog>`, `Comparator<? super T>` |

- Mutable containers must be invariant: if `List<Dog>` were a `List<Animal>`, you could `add(new Cat())` into it. Java arrays are covariant and pay with `ArrayStoreException` at run time, a known design mistake.
- PECS: producer `extends`, consumer `super`; `Collections.copy(List<? super T> dest, List<? extends T> src)` is the canonical signature.
- Kotlin and C# put variance on the declaration (`out T`, `in T`); Java puts it at the use site with wildcards.

### Generics implementation: erasure vs reification
- Java erases type arguments at compile time: `List<String>` and `List<Integer>` are the same class at run time, no `new T[]`, no `instanceof List<String>`, primitives need boxing. Chosen for backward compatibility in Java 5. Project Valhalla aims to specialize generics over value types ([[generics]], [[primitives-and-wrappers]]).
- C# and Kotlin/Native reify; C++ templates generate code per instantiation (monomorphization), as does Rust; Go generics (1.18) use a dictionary + partial monomorphization.

### Types as design tools in a backend
- Make illegal states unrepresentable: `Money` instead of `BigDecimal` + `String currency`, `Iccid` instead of `String`; validation at construction ([[value-objects-and-entities]]).
- Sealed result types instead of exceptions for expected failures ([[error-handling-strategies]]).
- Exhaustive `switch` over sealed types means adding a new event type fails compilation everywhere it is not handled, the opposite of an `else` branch that silently swallows it.
- Serialization boundaries are where static types end: JSON into a `Map<String, Object>` is dynamic typing by another name; prefer typed DTOs and schema evolution rules ([[message-schema-evolution]]).

- Interview: "why does `var` not make Java dynamic?" The type is still fixed at compile time, just inferred.

## Example
Variance in practice: a generic repository helper that accepts any producer of a subtype and any consumer of a supertype, plus a sealed result that the compiler forces callers to handle.

```java
public final class Batching {
    /** PECS: read from a producer of T (or subtypes), write to a consumer of T (or supertypes). */
    public static <T> int drain(Collection<? extends T> source, Collection<? super T> target, int max) {
        int moved = 0;
        for (Iterator<? extends T> it = source.iterator(); it.hasNext() && moved < max; moved++) {
            target.add(it.next());
            it.remove();
        }
        return moved;
    }
}

sealed interface Parsed<T> permits Parsed.Ok, Parsed.Invalid {
    record Ok<T>(T value) implements Parsed<T> {}
    record Invalid<T>(String field, String reason) implements Parsed<T> {}
}

static Parsed<Iccid> parseIccid(String raw) {
    if (raw == null || !raw.matches("\\d{19,20}")) return new Parsed.Invalid<>("iccid", "19–20 digits expected");
    return new Parsed.Ok<>(new Iccid(raw));
}

// caller must handle both cases; adding a third permitted type breaks this switch at compile time
String describe(Parsed<Iccid> p) {
    return switch (p) {
        case Parsed.Ok<Iccid> ok -> "valid " + ok.value();
        case Parsed.Invalid<Iccid> inv -> inv.field() + ": " + inv.reason();
    };
}

// usage of drain with variance
List<SimEvent> events = ...;            // SimEvent extends DomainEvent
List<DomainEvent> outbox = new ArrayList<>();
Batching.drain(events, outbox, 100);    // Collection<? extends DomainEvent>, Collection<? super SimEvent>
```

## Pitfalls
- Returning `List<? extends T>` from a public API: callers cannot add to it and get confusing errors; wildcards belong in parameters, not return types.
- Raw types (`List list`) silently disable the checks and produce `ClassCastException` far from the cause; treat `rawtypes` warnings as errors.
- Treating `Object`/`Map<String, Object>` payloads as "flexible": every consumer re-parses, and type errors surface in production ([[message-schema-evolution]]).
- Relying on arrays' covariance (`Object[] arr = new String[1]; arr[0] = 1;`) compiles and throws at run time.
- Related: [[generics]] for Java wildcards and erasure, [[programming-paradigms]] for where ADTs come from, [[value-objects-and-entities]] for domain types.
