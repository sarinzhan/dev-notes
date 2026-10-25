---
title: Type casting, instanceof and autoboxing
tags: [casting, autoboxing, instanceof, pattern-matching, type-conversion]
learned: 2026-10-22
---
## TL;DR
Java is statically typed: every expression has a compile-time type, and conversion between types is either implicit (widening, boxing) or explicit (narrowing, downcast). Upcasting and widening are always safe; downcasting a reference is checked at runtime and throws `ClassCastException` on mismatch, so guard it with `instanceof`, ideally the pattern-matching form. Autoboxing hides the primitive-to-wrapper conversion and with it a null check and an allocation.

## Key points
### Kinds of conversion
| Conversion | Primitives | References | Explicit? | Risk |
|---|---|---|---|---|
| Identity | `int` → `int` | `Dog` → `Dog` | no | none |
| Widening / upcast | `int` → `long`, `float` → `double` | `Dog` → `Animal`, `ArrayList` → `List` | no | `long` → `float`/`double` and `int` → `float` can lose precision silently |
| Narrowing / downcast | `long` → `int`, `double` → `int` | `Animal` → `Dog` | yes, `(int) x` | primitives: truncation, overflow wraps; references: `ClassCastException` |
| Boxing / unboxing | `int` ↔ `Integer` | | no | `NullPointerException` on unboxing `null` |
| String conversion | anything + `""` | | no | hidden `toString` cost |

- A downcast does not change the object: it only tells the compiler to treat the reference as the subtype. The JVM checks the actual class at runtime (`checkcast`).
- Primitive widening order: `byte → short → int → long → float → double`; `char → int`. Arithmetic on `byte`/`short`/`char` promotes to `int`, so `byte b = a + 1` needs a cast (unless `a` is a constant).
- `(int) 3.99` is `3` (truncation towards zero), `(int) Double.NaN` is `0`, `(int) 1e20` saturates to `Integer.MAX_VALUE`; `(byte) 200` is `-56` (wraparound).
- Generics: casting to a parameterized type (`(List<String>) obj`) is unchecked because of erasure ([[generics]]); only the raw type is verified at runtime.

### instanceof and pattern matching
- `x instanceof T` is `true` when `x` is non-null and its runtime class is `T` or a subtype; `null instanceof T` is `false`.
- Java 16: `if (o instanceof Dog d)` binds `d` after the check, removing the cast. Java 21: `switch` with type patterns and record deconstruction (`case Card(String pan) -> ...`), exhaustive over `sealed` hierarchies ([[abstract-classes-and-interfaces]]).
- Chains of `instanceof` are usually a sign that a method belongs on the type (polymorphism, [[oop-principles]]) or that a sealed hierarchy with a switch would be cleaner.

### Autoboxing and unboxing (Java 5)
- The compiler inserts `Integer.valueOf(i)` when a primitive is used where a wrapper is expected (assignment, method argument, collection element) and `x.intValue()` for the opposite. That is why `List<Integer>.add(5)` compiles.
- Boxing allocates unless the value is in the cache (`-128..127` for integral types, [[primitives-and-wrappers]]); unboxing `null` throws NPE.
- Mixed comparisons: `Integer == int` unboxes the wrapper; `Integer == Integer` compares references. `Integer == Long` does not compile. Ternaries mix types: `cond ? 1 : nullInteger` unboxes and NPEs.
- Overload resolution tries widening before boxing: `m(long)` is chosen over `m(Integer)` for an `int` argument ([[methods-overloading-overriding]]).

### Numeric conversions beyond casting
- Use `Math.toIntExact(long)` to fail instead of wrapping, `Math.round` for rounding instead of truncation, `BigDecimal.setScale` for money, `Integer.parseInt`/`Long.parseLong` for strings.
- `Number` methods (`longValue()`) convert any wrapper without casting.

## Example
```java
sealed interface ChargeEvent permits Debit, Refund {}
record Debit(String msisdn, long amountMinor) implements ChargeEvent {}
record Refund(String msisdn, long amountMinor, String reason) implements ChargeEvent {}

// Old style: cast after instanceof
static long signedAmountOld(Object event) {
    if (event instanceof Debit) {
        Debit d = (Debit) event;                 // explicit downcast
        return d.amountMinor();
    }
    throw new IllegalArgumentException(String.valueOf(event));
}

// Java 21: pattern matching, no casts, compiler-checked exhaustiveness
static long signedAmount(ChargeEvent event) {
    return switch (event) {
        case Debit d                       -> d.amountMinor();
        case Refund(var m, var amt, var r) -> -amt;
    };
}

static int percentOf(long amountMinor, double rate) {
    long fee = Math.round(amountMinor * rate);   // double -> long with rounding, not truncation
    return Math.toIntExact(fee);                 // long -> int, throws instead of wrapping
}

Map<String, Integer> attempts = new HashMap<>();
int n = attempts.getOrDefault("996555000111", 0);   // avoids NPE on unboxing a missing key
```

## Pitfalls
- `int total = map.get(key)` NPEs when the key is absent; `getOrDefault` or an `Integer` local with an explicit check.
- `long x = intA * intB` overflows in `int` before widening; cast an operand first: `(long) intA * intB`.
- `List<Integer>.remove(int)` vs `remove(Object)`: `list.remove(3)` removes index 3; `list.remove(Integer.valueOf(3))` removes the value.
- Casting an object to an interface it does not implement compiles if the static type is non-final (the compiler cannot rule out a subclass) and fails only at runtime.
- Related: [[primitives-and-wrappers]], [[generics]], [[oop-principles]].
