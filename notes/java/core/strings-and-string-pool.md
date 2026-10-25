---
title: Strings, the string pool, StringBuilder and StringBuffer
tags: [string, string-pool, interning, stringbuilder, immutability]
learned: 2026-10-22
---
## TL;DR
`String` is an immutable, final class whose literals live in the **string pool**, a JVM-managed table of unique strings on the heap. Immutability is what makes the pool, hash-code caching, thread safety and secure use as keys/credentials possible. For building strings in loops use `StringBuilder`; `StringBuffer` is its synchronized, mostly obsolete twin.

## Key points
### Properties of String
- `final` class, immutable: every "modifying" method (`concat`, `replace`, `toUpperCase`, `strip`) returns a new object.
- Since Java 9 the content is a `byte[]` plus a coder byte (Latin-1 = 1 byte per char, UTF-16 = 2) instead of `char[]` ("compact strings"), halving memory for ASCII text.
- Created from a literal (`"abc"`), concatenation (`+`, compiled to `invokedynamic`/`StringConcatFactory` since Java 9), `String.valueOf`, `new String(...)`, text blocks `"""..."""` (Java 15).
- Usable in `switch` since Java 7; `equals`/`hashCode` are value-based; `hashCode` is cached in a field after the first call (immutability makes that safe).
- Useful recent API: `strip`, `isBlank`, `lines`, `repeat` (11), `formatted`, `stripIndent` (15), `String.join`, `chars()`/`codePoints()`.

### The string pool
- A hash table (`StringTable`) inside the JVM holding one canonical instance per distinct content. In Java 6 it lived in PermGen; since 7 it is in the main heap ([[jvm-memory-areas]]), so pooled strings are garbage-collected normally once unreferenced. Default table size is 65 536 buckets (`-XX:StringTableSize`).
- A literal in source code is interned at class loading: `"abc" == "abc"` is `true` across classes. Compile-time constant expressions (`"a" + "b"`, `final String x = "a"; x + "b"`) are folded and interned too.
- `new String("abc")` always allocates a new object outside the pool (the literal is still interned). `s.intern()` returns the pooled instance, adding it if absent.
- Why: memory savings for repeated strings (log levels, column names, JSON keys) and cheap `==` inside the JVM. The pool is the textbook example of the **Flyweight** pattern ([[structural-patterns]]).
- The cost: a lookup on literal creation; runtime strings (read from a socket or DB) are not pooled unless you call `intern()`, which is usually a bad idea; deduplication is better left to G1's `-XX:+UseStringDeduplication` ([[garbage-collection]]).

### Why immutability and finality
- The pool is only correct if nobody can change a shared instance.
- Security: strings carry class names for the class loader, file paths, JDBC URLs and credentials; a mutable string could be changed after validation and before use.
- Thread safety without synchronization; safe to share and to use as keys in `HashMap`, `ConcurrentHashMap` ([[hashmap-internals]]).
- `final` prevents a subclass from breaking any of the above by overriding `equals` or `hashCode`.

### `char[]` for passwords
- A `String` password stays in memory (and possibly in the pool and heap dumps) until the GC removes it, and you cannot clear it. A `char[]` can be overwritten with zeros (`Arrays.fill`) as soon as it is used, which is why `Console.readPassword()` and `KeyStore` APIs take `char[]`. It is a mitigation, not a guarantee: the JIT and the OS can still copy the bytes.

### String vs StringBuilder vs StringBuffer
| | `String` | `StringBuilder` (1.5) | `StringBuffer` (1.0) |
|---|---|---|---|
| Mutable | no | yes | yes |
| Thread-safe | yes (immutable) | no | yes (every method `synchronized`) |
| Use | values, keys, constants | building in a loop, single thread | legacy; almost never: builders are rarely shared |

- Since Java 9 `a + b + c` in one expression is compiled to a single `invokedynamic` call that is as fast as a builder; the builder only wins inside loops, where `+=` allocates a new string per iteration.
- `String.join`, `Collectors.joining`, `StringJoiner` cover the common "join with separator" case.

## Example
```java
String a = "esim-" + "kg";            // constant folding -> interned
String b = "esim-kg";
String c = new String("esim-kg");     // fresh object outside the pool
System.out.println(a == b);           // true
System.out.println(b == c);           // false
System.out.println(b == c.intern());  // true
System.out.println(b.equals(c));      // true: always compare content with equals

// Building a report in a loop: StringBuilder, not +=
StringBuilder sb = new StringBuilder(256);
for (var line : lines) {
    sb.append(line.msisdn()).append(';').append(line.amountMinor()).append('\n');
}
String report = sb.toString();

// Same thing, more declarative
String report2 = lines.stream()
        .map(l -> l.msisdn() + ";" + l.amountMinor())
        .collect(Collectors.joining("\n"));
```

## Pitfalls
- Comparing strings with `==` works for literals and fails for runtime strings; it is never the right choice. `Objects.equals(a, b)` also handles `null`.
- `substring` in Java 6 shared the parent's `char[]` and could leak a huge array; since 7u6 it copies. `split` with a regex on a hot path is slow; precompile a `Pattern` or use `indexOf` loops.
- `toLowerCase()`/`toUpperCase()` are locale-sensitive (the Turkish-i problem); pass `Locale.ROOT` for protocol strings.
- Logging with `"..." + obj` builds the string even when the level is disabled; use parameterized logging `log.debug("id={}", id)`.
- Related: [[equals-and-hashcode]], [[immutable-objects]], [[primitives-and-wrappers]].
