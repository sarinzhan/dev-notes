---
title: Constructors and object initialization
tags: [constructors, initialization, builder, records, object-creation]
learned: 2026-10-22
---
## TL;DR
A constructor is a method with the class name and no return type that runs when `new` creates an object; its job is to leave the object in a valid state. The first statement is always a call to another constructor (`this(...)`) or to the superclass constructor (`super(...)`, inserted implicitly). Records and builders are the modern ways to avoid long constructor chains.

## Key points
### Basics
- Declared like a method without a return type; can be overloaded; can have any access modifier (`private` for singletons and factories, see [[creational-patterns]]).
- If a class declares no constructor the compiler adds a public no-arg **default constructor** that calls `super()`. The moment you declare any constructor, the default one disappears; frameworks that need a no-arg constructor (JPA entities, Jackson without `@JsonCreator`) then fail.
- A constructor is a function that initializes an object; it is not inherited, so a subclass must declare its own if the parent has no accessible no-arg constructor.
- Constructors cannot be `abstract`, `static`, `final` or `synchronized` (the object is not yet shared). They may declare `throws`.

### Chaining
- `this(args)` delegates to another constructor of the same class; `super(args)` to the parent. Either must be the **first statement** (Java 22 preview "flexible constructor bodies" relaxes this to allow statements that do not touch `this` before it).
- If neither is written, `super()` is implied; if the parent has no accessible no-arg constructor, the code does not compile.
- Full order of events on `new`: see [[class-initialization-order]] (parent fields and blocks → parent constructor → child fields and blocks → child constructor).

### What happens inside `new`
1. The JVM allocates memory on the heap ([[jvm-memory-areas]]) with every field at its default value (`0`, `false`, `null`); the JIT can allocate on the stack if escape analysis proves the object does not escape.
2. Instance initializers run, then the constructor body.
3. The reference is returned. Once a constructor finishes, `final` fields are safely published to all threads ([[java-memory-model]]).

### Other ways to create objects
- Static factory method (`LocalDate.of`, `List.of`): named, can return cached instances or subtypes, can hide the implementation.
- `Class.getDeclaredConstructor().newInstance()` via [[object-class-and-reflection]] (what Spring and Hibernate do).
- `clone()` ([[object-class-and-reflection]]) and deserialization bypass constructors entirely.
- **Records** (Java 16): `record Point(int x, int y)` generates a canonical constructor, accessors, `equals`/`hashCode`/`toString`. A *compact constructor* `Point { if (x < 0) throw ...; }` validates or normalizes without repeating the assignments.
- **Builder** for classes with many optional parameters; Lombok `@Builder` or hand-written. Prefer it to "telescoping" constructor overloads.

## Example
```java
public final class EsimProfile {
    private final String iccid;
    private final String activationCode;
    private final Instant createdAt;
    private final List<String> tags;

    private EsimProfile(Builder b) {                       // private: only the builder constructs
        this.iccid = Objects.requireNonNull(b.iccid, "iccid");
        this.activationCode = Objects.requireNonNull(b.activationCode, "activationCode");
        this.createdAt = b.createdAt != null ? b.createdAt : Instant.now();
        this.tags = List.copyOf(b.tags);                   // defensive copy
    }

    public static Builder builder() { return new Builder(); }

    public static final class Builder {
        private String iccid, activationCode;
        private Instant createdAt;
        private final List<String> tags = new ArrayList<>();

        public Builder iccid(String v) { iccid = v; return this; }
        public Builder activationCode(String v) { activationCode = v; return this; }
        public Builder createdAt(Instant v) { createdAt = v; return this; }
        public Builder tag(String v) { tags.add(v); return this; }
        public EsimProfile build() { return new EsimProfile(this); }
    }
}

// Same idea in one line for a simple value:
record Msisdn(String value) {
    Msisdn { if (!value.matches("996\\d{9}")) throw new IllegalArgumentException(value); }
}
```

## Pitfalls
- Calling an overridable method from a constructor executes subclass code on a half-built object (subclass fields still `null`).
- Leaking `this` from a constructor (registering a listener, starting a thread with `this`) publishes an object before construction ends; other threads can see default field values.
- Storing a caller's mutable collection or `Date` directly instead of copying breaks immutability ([[immutable-objects]]).
- Heavy work in constructors (I/O, DB calls) makes classes untestable; constructors should assign, validate and nothing else. Spring's constructor injection ([[spring-ioc-and-di]]) follows this.
- Related: [[class-initialization-order]], [[creational-patterns]], [[modifiers-final-static-access]].
