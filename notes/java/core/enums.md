---
title: Enums
tags: [enum, constants, singleton, enummap, switch]
learned: 2026-10-22
---
## TL;DR
An `enum` is a class with a fixed set of named instances created by the JVM at class initialization. Each constant is a singleton, so `==` is safe; enums can have fields, constructors, methods, implement interfaces and give each constant its own behaviour. They replaced the pre-Java 5 "int constant" pattern, which was neither type-safe nor printable.

## Key points
### What you get
- Every enum implicitly extends `java.lang.Enum<E>` and is therefore `final` (except that constants with bodies are anonymous subclasses) and cannot extend another class. It can implement any number of interfaces.
- Constructors are implicitly `private`; `new` is forbidden, and so are reflective and deserialization-based instantiation. The JVM creates the constants once in static initialization, in declaration order ([[class-initialization-order]]). That makes an enum the simplest correct singleton (Effective Java item 3, [[creational-patterns]]): thread-safe, serialization-safe, reflection-proof.
- Built-in methods: `name()`, `ordinal()` (position, 0-based), `values()` (static, new array each call, in declaration order), `valueOf(String)` (throws `IllegalArgumentException` on an unknown name), `compareTo` (by ordinal; enums implement `Comparable`), `toString()` (defaults to the name, overridable), `getDeclaringClass()`.
- `equals` and `hashCode` are final and identity-based; `==` and `equals` are equivalent, and `==` is null-safe.

### Before enums
- Java 1–4 used `public static final int STATUS_ACTIVE = 1;`: not type-safe (any `int` passes), no namespace, meaningless in logs, breaks when values are reordered and clients are not recompiled (constants inline). The type-safe enum pattern (a class with private constructor and public static final instances) was the hand-written precursor; Java 5 made it a language feature along with `EnumSet`, `EnumMap` and `switch` on enums.

### Behaviour per constant
- Fields and a constructor give each constant data: `KGS("som", 2)`.
- An abstract method in the enum must be implemented by every constant in its body (`PLUS { int apply(int a, int b) { return a + b; } }`); alternatively pass a lambda to the constructor, which is shorter.
- `switch` on an enum: since Java 14 the arrow form `case A, B -> ...` is an expression; with `sealed` types and pattern matching (21) the compiler enforces exhaustiveness, so a new constant becomes a compile error instead of a silent fall-through to `default`.

### Collections
- `EnumSet` (bit vector) and `EnumMap` (array indexed by ordinal) are the fastest set/map for enum keys; use them instead of `HashSet`/`HashMap` ([[collections-framework]]).

### Persistence and serialization
- JPA: `@Enumerated(EnumType.STRING)` always; the default `ORDINAL` breaks when constants are reordered or inserted ([[jpa-entities-and-relationships]]).
- Jackson serializes by `name()`; `@JsonValue` on a field changes that. An unknown incoming value fails unless `READ_UNKNOWN_ENUM_VALUES_AS_NULL` or a `@JsonCreator` fallback is configured.
- Java serialization writes only the name, which is why singletons via enum survive it.

## Example
```java
public enum SubscriptionStatus {
    PENDING("pending", false),
    ACTIVE("active", true),
    SUSPENDED("suspended", true),
    TERMINATED("terminated", false);

    private final String code;
    private final boolean billable;

    SubscriptionStatus(String code, boolean billable) { this.code = code; this.billable = billable; }

    public String code() { return code; }
    public boolean billable() { return billable; }

    public boolean canTransitionTo(SubscriptionStatus next) {
        return switch (this) {
            case PENDING    -> next == ACTIVE || next == TERMINATED;
            case ACTIVE     -> next == SUSPENDED || next == TERMINATED;
            case SUSPENDED  -> next == ACTIVE || next == TERMINATED;
            case TERMINATED -> false;
        };                                                   // exhaustive: no default needed
    }

    private static final Map<String, SubscriptionStatus> BY_CODE =
            Arrays.stream(values()).collect(Collectors.toUnmodifiableMap(s -> s.code, s -> s));

    public static SubscriptionStatus fromCode(String code) {
        var s = BY_CODE.get(code);
        if (s == null) throw new IllegalArgumentException("unknown status: " + code);
        return s;
    }
}
```

## Pitfalls
- Storing or exposing `ordinal()` (DB columns, wire formats) couples external data to declaration order; use `name()` or an explicit code.
- `values()` clones the array on every call; cache it in a static field for hot paths.
- A static field of the enum cannot be read from the constructor, because constants are initialized before other statics; build lookup maps in a static block or lazily, as above.
- `switch` with `default` hides missing cases when a constant is added; drop `default` in switch expressions to get the compile error.
- Related: [[abstract-classes-and-interfaces]], [[creational-patterns]], [[immutable-objects]].
