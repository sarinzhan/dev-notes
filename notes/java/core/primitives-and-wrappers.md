---
title: Primitive types and wrapper classes
tags: [primitives, wrappers, integer-cache, immutability]
learned: 2026-10-22
---
## TL;DR
Java has eight primitive types that hold raw values, and one immutable `final` wrapper class for each so that primitives can take part in generics, collections and streams. Integer wrappers for values in -128..127 are cached, which is why `==` on small `Integer`s "works" and on large ones does not.

## Key points
### The eight primitives
| Type | Size | Range | Default |
|---|---|---|---|
| `boolean` | JVM-dependent (1 byte in arrays, often an `int` slot on the stack) | `true`/`false` | `false` |
| `byte` | 1 byte | -128..127 | 0 |
| `short` | 2 bytes | -32 768..32 767 | 0 |
| `int` | 4 bytes | ±2.1·10⁹ | 0 |
| `long` | 8 bytes | ±9.2·10¹⁸ | 0L |
| `float` | 4 bytes | IEEE 754 single, ~7 digits | 0.0f |
| `double` | 8 bytes | IEEE 754 double, ~16 digits | 0.0 |
| `char` | 2 bytes | one UTF-16 code unit, 0..65 535 | `'\u0000'` |

- The spec does not fix the size of `boolean`; "1 bit" from the source is wrong. Everything else is fixed by the JLS, which is part of "write once, run anywhere".
- Integer arithmetic silently overflows (`Integer.MAX_VALUE + 1 == Integer.MIN_VALUE`); use `Math.addExact` or `long` when that matters. Integer division by zero throws `ArithmeticException`; floating-point gives `Infinity`/`NaN`.
- `float`/`double` cannot represent 0.1 exactly: never use them for money. Use `long` minor units or `BigDecimal`.
- A `char` is not a character: code points outside the BMP (emoji) take two `char`s. Use `String.codePoints()` when you need real characters.
- Local primitives have no default and must be assigned before use; fields and array elements get the defaults in the table.

### Wrappers: why they exist and why they are final
- `Boolean, Byte, Short, Integer, Long, Float, Double, Character` (plus `Number` as the abstract parent of the numeric ones).
- Collections, [[generics]] and the [[stream-api]] work with objects only (type erasure forbids `List<int>`), so a primitive must be wrapped. Wrappers also carry utilities: `Integer.parseInt`, `Integer.toBinaryString`, `Character.isDigit`, `Double.compare`, `MAX_VALUE`.
- They are **immutable**: no method changes the wrapped value. They are **final** because an immutable class must not be subclassable (a subclass could add mutable state or override `equals` and break every `HashMap` key), and because the JIT relies on their exact types.
- The source calls wrappers "a special case of Decorator"; that is a stretch. Decorator adds behaviour to the same interface; a wrapper changes the representation. Think "boxing", not Decorator.

### The cache
- `Integer.valueOf(n)` (which autoboxing calls) returns a cached instance for -128..127; `Long`, `Short`, `Byte` do the same, `Character` caches 0..127, `Boolean` has two instances. `Float` and `Double` cache nothing.
- The upper bound of the `Integer` cache can be raised with `-XX:AutoBoxCacheMax=N`; nobody should rely on it.
- `new Integer(5)` always allocates and is deprecated for removal since Java 9/16; use `valueOf` or autoboxing ([[casting-and-autoboxing]]).

## Example
```java
Integer a = 127, b = 127;      // Integer.valueOf -> cached
Integer c = 128, d = 128;      // two distinct objects
System.out.println(a == b);           // true
System.out.println(c == d);           // false
System.out.println(c.equals(d));      // true: always compare wrappers with equals

long balanceMinor = 1_250_00L;        // money in minor units, never double
double ratio = 0.1 + 0.2;             // 0.30000000000000004
BigDecimal exact = new BigDecimal("0.1").add(new BigDecimal("0.2")); // 0.3
```

## Pitfalls
- `==` between two wrappers compares references; it only looks right for small cached values. Comparing wrapper with primitive unboxes, so `Integer x == 5` is fine and `Integer x == Integer y` is a bug.
- Unboxing a `null` wrapper throws `NullPointerException`: an `Integer` field mapped from a nullable DB column and used in `int` arithmetic is a classic production NPE.
- `Map<String, Integer>` counters do `get`, unbox, add, box, `put` on every increment; use `merge`/`compute` or a `long[]`/`LongAdder` in hot paths.
- `char` arithmetic promotes to `int`: `'a' + 1` is `98`, not `'b'`; cast back.
- Related: [[casting-and-autoboxing]], [[strings-and-string-pool]], [[immutable-objects]].
