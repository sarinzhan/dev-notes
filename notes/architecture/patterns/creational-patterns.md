---
title: Creational design patterns
tags: [design-patterns, creational, singleton, builder, factory]
learned: 2026-10-22
---
## TL;DR
Design patterns are recurring solutions to common design problems: ready-made, standardised, a shared vocabulary between programmers. Creational patterns are about **creating objects without adding unnecessary dependencies** on concrete classes: Singleton, Prototype, Builder, Factory Method and Abstract Factory. "Creational patterns give birth to objects, structural ones join them, behavioural ones teach them to talk."

## The problem it solves
- **Singleton:** several parts of the code create their own copy of a resource that must be shared (config, connection pool) → one instance with one access point.
- **Prototype:** code needs a copy of an object it sees only through an interface, so it cannot call the right constructor → the object copies itself.
- **Builder:** a constructor with many mostly optional parameters leads to calls like `new X(a, null, 3, null, true)` that nobody can read → the object is built step by step with named methods and checked in `build()`.
- **Factory Method:** code creates objects with `new ConcreteClass()` and must change for every new type → a creation method decides the class, and subclasses or callers do not depend on it.
- **Abstract Factory:** related objects must come from the same family (one vendor's parser and builder), and mixing families breaks things → one factory creates the whole family.
- **When you don't need them:** a class with one implementation and a few required fields; a plain constructor is clearer. In Spring the container already gives you singletons and factories.

## Key points
| Pattern | Intent | JDK / Spring example |
|---|---|---|
| **Singleton** | Guarantee a class has one instance and give a global access point. Hide the constructor, expose a static accessor (or an enum). | `Runtime.getRuntime()`; every Spring bean is a singleton *per container* by default ([[spring-bean-scopes-and-configuration]]) |
| **Prototype** | Copy objects without depending on their concrete class; the copying logic lives in the object itself, behind an interface or abstract class. | `Object.clone()` / `Cloneable`; Spring `prototype` bean scope creates a fresh instance per `getBean()` |
| **Builder** | Construct complex objects step by step; the same building code yields different representations. Moves construction out of a constructor with many parameters. | `StringBuilder`, `HttpRequest.newBuilder()`, `Stream.builder()`; `UriComponentsBuilder`, `RestClient.builder()`, Lombok `@Builder` |
| **Factory Method** | Define a common interface for creating objects in a superclass and let subclasses decide which class to instantiate. | `Calendar.getInstance()`, `Collection.iterator()` ([[iterators-and-fail-fast]]), `Executors.newFixedThreadPool()` ([[executors-and-thread-pools]]); `FactoryBean<T>` in Spring |
| **Abstract Factory** | Produce families of related objects without naming their concrete classes. | `DocumentBuilderFactory`, `XMLInputFactory`; `BeanFactory` / `ApplicationContext` ([[spring-ioc-and-di]]) |

### When to use which
- **Singleton**: a single shared resource (config, connection pool such as [[hikaricp-pool-sizing|HikariCP]]). Thread safety matters: eager static field, holder-class idiom, or `enum`. Double-checked locking needs `volatile` ([[java-memory-model]]).
- **Prototype**: creating an object is expensive (parsed template, pre-configured client) and you need many similar ones; copying is cheaper than rebuilding.
- **Builder**: the code must produce different representations of an object, or the object has many optional parameters (telescoping constructors). The common Java variant is a fluent builder whose methods return `this`; the classic GoF variant separates *Director* and *Builder*.
- **Factory**: the exact types and dependencies of the objects are not known in advance, or you want to let users of a library/framework extend a part of it. A static factory method (`List.of`, `Optional.of`) is the lightweight version: it can cache, return subtypes and has a readable name.
- Spring's container is one big creational pattern set: `@Bean` methods are factory methods, `@Configuration` classes are abstract factories, and bean scopes decide between singleton and prototype ([[spring-bean-lifecycle]]).

## Example
```java
// Builder with validation in build(): immutable result, readable call site.
public final class KafkaTopicSpec {
    private final String name;
    private final int partitions;
    private final short replicationFactor;
    private final Duration retention;

    private KafkaTopicSpec(Builder b) {
        this.name = b.name; this.partitions = b.partitions;
        this.replicationFactor = b.replicationFactor; this.retention = b.retention;
    }

    public static Builder named(String name) { return new Builder(name); }

    public static final class Builder {
        private final String name;
        private int partitions = 3;
        private short replicationFactor = 3;
        private Duration retention = Duration.ofDays(7);

        private Builder(String name) { this.name = Objects.requireNonNull(name); }
        public Builder partitions(int n) { this.partitions = n; return this; }
        public Builder replicationFactor(short rf) { this.replicationFactor = rf; return this; }
        public Builder retention(Duration d) { this.retention = d; return this; }

        public KafkaTopicSpec build() {
            if (partitions < 1) throw new IllegalArgumentException("partitions >= 1");
            if (replicationFactor < 1) throw new IllegalArgumentException("rf >= 1");
            return new KafkaTopicSpec(this);
        }
    }
}

// usage
var spec = KafkaTopicSpec.named("esim.activations").partitions(12).retention(Duration.ofDays(30)).build();
```

## Pitfalls
- A hand-written Singleton is a global variable: hard to test, hidden coupling, and one per classloader, not one per JVM. Prefer a container-managed singleton bean.
- `Cloneable` is a broken API (no `clone()` in the interface, shallow copy by default, bypasses constructors). Use a copy constructor or a static `copyOf` instead of Prototype-by-`clone`.
- Builders that allow `build()` on an incomplete object just move the error to runtime; validate in `build()`. Lombok `@Builder` does no validation and makes every field optional.
- Factory explosion: a factory per class with one implementation is ceremony. Add a factory when there is real variation.
- Related: [[structural-patterns]], [[behavioral-patterns]], [[solid]], [[immutable-objects]].
