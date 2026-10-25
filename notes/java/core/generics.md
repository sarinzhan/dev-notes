---
title: Generics, wildcards and type erasure
tags: [generics, wildcards, pecs, type-erasure]
learned: 2026-10-22
---
## TL;DR
Generics (Java 5) let classes, interfaces and methods take a type as a parameter, so the compiler checks what goes into a `List<String>` instead of you casting from `Object`. They exist only at compile time: after **type erasure** a `List<String>` is a plain `List` in the bytecode, which explains every limitation below.

## Key points
### Basics
- Type parameter on a class or interface: `class Box<T> { T value; }`; on a method: `static <T> T first(List<T> xs)`.
- Bounded type parameter: `<T extends Comparable<T>>` restricts what T may be and lets you call `compareTo`.
- Raw types (`List` without a parameter) behave as `List<Object>` with warnings; they exist for backward compatibility. Mixing them in defeats the type checks.
- Invariance: `List<Integer>` is **not** a `List<Number>`, even though `Integer` is a `Number`. Otherwise you could add a `Double` through the `List<Number>` reference. Wildcards exist to relax this.

### Wildcards
- `?` unbounded: a list of something; you can read `Object`s, you cannot add anything except `null`.
- `? extends T` upper bound: a list of T or a subtype. Safe to **read** as T, unsafe to add.
- `? super T` lower bound: a list of T or a supertype. Safe to **add** a T, reads come back as `Object`.
- **PECS**: Producer `extends`, Consumer `super`. A method that only reads from a collection takes `Collection<? extends T>`; one that only writes takes `Collection<? super T>`. This is how `Collections.copy(List<? super T> dest, List<? extends T> src)` is declared.

### What cannot be generic (because of erasure)
- Primitives as type arguments: `List<int>` is illegal, use `List<Integer>` (autoboxing).
- `new T()`, `new T[]`, `T.class`, `instanceof List<String>` and `catch (T e)`: there is no T at runtime.
- Static fields of type T: statics are shared by all parameterizations.
- Generic exceptions: `class MyEx<T> extends Exception` is illegal.
- Overloads that differ only by type argument (`f(List<String>)` and `f(List<Integer>)`) erase to the same signature.
- Generic arrays: `List<String>[]` cannot be created safely because arrays are covariant and checked at runtime.

## Example
```java
// PECS in practice
static <T extends Comparable<? super T>> T max(Collection<? extends T> items) {
    Iterator<? extends T> it = items.iterator();
    T best = it.next();
    while (it.hasNext()) { T x = it.next(); if (x.compareTo(best) > 0) best = x; }
    return best;
}

static void fill(List<? super Integer> target, int n) {
    for (int i = 0; i < n; i++) target.add(i);     // Integer into List<Number> or List<Object>
}
```

## Pitfalls
- Unchecked casts (`(T) obj`) compile with a warning and fail later with `ClassCastException` far from the cause.
- `? extends T` collections cannot be added to: a common surprise when a method tries to mutate its input.
- Reflection sees only erased types, except for generic signatures kept in class metadata (`getGenericSuperclass`), which is how Jackson's `TypeReference` and Spring's `ParameterizedTypeReference` recover `List<User>`.
- Where generics meet the standard library: [[collections-framework]].
