---
title: Behavioral design patterns
tags: [design-patterns, behavioral, strategy, observer, state]
learned: 2026-10-22
---
## TL;DR
Behavioural patterns organise **effective and safe interaction between objects** and the distribution of responsibility among them. Most of them do the same trick: turn an individual behaviour into an object (a handler, a state, a strategy, a command) so it can be swapped, chained, stored or replayed at runtime.

## The problem it solves
- **Chain of Responsibility:** auth, rate limiting and logging are hard-wired into one request method in a fixed order → each check becomes a handler in a chain that can be reordered or extended.
- **State:** behaviour depends on a status, and the alternative, a giant `switch` on an enum in every method, becomes unreadable fast → each state gets its own class with its own behaviour.
- **Observer:** after payment the order service calls notifications, loyalty and analytics directly and must change for each new consumer → it publishes an event and subscribers register themselves.
- **Memento:** undo or rollback needs the old state, but copying it from outside breaks encapsulation → the object itself makes an opaque snapshot and restores from it.
- **Strategy:** discount rules live in one `if/else` that grows with every promo → each rule is a separate interchangeable class chosen at runtime.
- **Iterator:** callers walk a collection's internal array or nodes and break when the structure changes → one traversal interface hides the structure.
- **Command:** a request must be queued, logged, retried or run later, but it is only a method call → the request becomes an object that can be stored and executed later.
- **Template Method:** several classes repeat the same algorithm with small differences (open, run, map, close) → the superclass fixes the steps and subclasses fill in only the differences.
- **Mediator:** many objects call each other directly, so one change touches several of them → they talk through one central object instead.
- **Visitor:** a new operation (export, validation) is needed over a fixed object structure without editing every class → the operation lives in a separate visitor.
- **When you don't need them:** one fixed behaviour with no variation; an interface, a chain or a class per state is only ceremony there, and a lambda or a small `switch` is enough.

## Key points
| Pattern | Intent | JDK / Spring example |
|---|---|---|
| **Chain of Responsibility** | Pass a request along a chain of handlers; each decides whether to handle it and whether to pass it on. Handlers are objects, each holding a reference to the next. | `javax.servlet.Filter` chain; Spring Security `FilterChain` ([[spring-security-basics]]), `HandlerInterceptor`s ([[spring-mvc-request-lifecycle]]), `@ExceptionHandler` resolution |
| **State** | Let an object change behaviour when its internal state changes; from outside it looks as if the class changed. One class per state, state-specific behaviour moved into it. | `Thread.State` transitions ([[threads-basics]]); Spring Statemachine; JPA entity lifecycle states ([[jpa-persistence-context-and-entity-lifecycle]]) |
| **Observer** | A subscription mechanism: a publisher keeps a list of subscribers and notifies them of events; subscribers add and remove themselves. | `java.util.EventListener`, `PropertyChangeListener`, `Flow.Subscriber` (Java 9); `ApplicationEventPublisher` / `@EventListener` ([[spring-bean-lifecycle]]), `@KafkaListener` ([[spring-kafka-consumer]]) |
| **Memento** | Save and restore an object's past state without exposing its internals; the object itself creates the snapshot because it can see its private fields. | `Serializable` snapshots; editor undo; JPA flush / rollback restoring managed state |
| **Strategy** | Extract a family of similar algorithms into separate interchangeable classes so the client can switch them. | `Comparator` in `Collections.sort()` ([[stream-api]]), `ThreadPoolExecutor.RejectedExecutionHandler` ([[executors-and-thread-pools]]); `PasswordEncoder`, `CacheManager`, `ErrorHandler` in Spring Kafka |
| **Iterator** | Traverse elements of a collection without exposing its internal representation. | `Iterator`, `Iterable`, enhanced `for` ([[iterators-and-fail-fast]]); `ResultSet.next()` |
| **Command** | Encapsulate a request as an object: queue it, log it, undo it, execute it later. | `Runnable` / `Callable` submitted to an executor; Spring Batch `Tasklet`; `TransactionCallback` |
| **Template Method** | Define the skeleton of an algorithm in a superclass and let subclasses override specific steps. | `AbstractList.get()`, `InputStream.read(byte[])`; `JdbcTemplate`, `AbstractApplicationContext.refresh()`, `WebSecurityConfigurerAdapter` (removed in Security 6) |
| **Mediator** | Centralise communication between objects so they do not reference each other directly. | `ExecutorService` between producers and workers; Spring `DispatcherServlet` as front controller; a message broker ([[rabbitmq-basics]]) |
| **Visitor** | Add new operations to an object structure without changing its classes. | `java.nio.file.FileVisitor` ([[java-nio]]), `AnnotationVisitor` in ASM; sealed interfaces + pattern matching `switch` (Java 21) are the modern replacement |

### When each one fits
- **Chain of Responsibility**: the set of handlers must be defined dynamically; handlers must run in strict order; or the program must handle varied requests in several ways and you do not know in advance which requests and handlers you will need.
- **State**: behaviour changes drastically with internal state, there are many states, and their code changes often.
- **Observer**: some objects must watch others only in certain cases, or after a state change in one object something must happen in others and you do not know in advance which ones.
- **Strategy**: different variations of an algorithm inside one object, or many similar classes that differ only in some behaviour. With lambdas a strategy is often just a functional interface ([[functional-interfaces]]).
- **Memento**: undo, transactions, checkpoints; the snapshot is opaque to everyone but the originator.

## Example
```java
// Strategy chosen by key at runtime; Spring injects every implementation into the map.
public interface DiscountStrategy {
    String code();                       // "NONE", "LOYALTY", "PROMO"
    Money apply(Money total, Customer c);
}

@Component class LoyaltyDiscount implements DiscountStrategy {
    public String code() { return "LOYALTY"; }
    public Money apply(Money total, Customer c) { return total.minusPercent(c.loyaltyYears() * 2); }
}

@Service
public class PricingService {
    private final Map<String, DiscountStrategy> strategies;

    public PricingService(List<DiscountStrategy> all) {
        this.strategies = all.stream().collect(Collectors.toMap(DiscountStrategy::code, s -> s));
    }

    public Money price(Order order, Customer customer) {
        DiscountStrategy s = strategies.getOrDefault(customer.discountCode(), strategies.get("NONE"));
        return s.apply(order.total(), customer);
    }
}
```

## Pitfalls
- Observer with synchronous listeners: a slow or failing subscriber blocks or breaks the publisher. Spring `@EventListener` runs in the caller's thread and transaction by default; use `@TransactionalEventListener` or `@Async` deliberately.
- A chain where no handler handles the request fails silently; end every chain with an explicit terminal handler.
- State objects that mutate the context and each other produce hidden cycles; keep transitions in one place (a table or the context) and the states stateless.
- Strategy maps keyed by string break on typos; key by enum.
- Related: [[creational-patterns]], [[structural-patterns]], [[solid]], [[ddd-basics]].
