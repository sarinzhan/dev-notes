---
title: Structural design patterns
tags: [design-patterns, structural, adapter, decorator, proxy]
learned: 2026-10-22
---
## TL;DR
Structural patterns are about **composing objects and classes into larger structures that stay easy to maintain**. Adapter translates one interface into another, Decorator and Proxy wrap an object with the same interface (to add behaviour or to control access), Bridge splits abstraction from implementation, Facade hides a subsystem behind a simple entry point, Composite treats a tree as a single object, Flyweight shares common state to save memory.

## The problem it solves
- **Adapter:** a third-party class has the right behaviour but the wrong interface → a wrapper translates it into the interface your code expects.
- **Decorator:** adding retry, metrics and logging through subclasses gives a class for every combination → wrappers with the same interface are stacked as needed.
- **Proxy:** cross-cutting work (transactions, caching, lazy loading, access checks) would be copied into every method → a stand-in intercepts calls and does it before or after the real object.
- **Bridge:** two things that vary (a common API and vendor drivers) in one hierarchy multiply classes → abstraction and implementation become two hierarchies that change independently.
- **Facade:** a complex library has many moving parts, but you need only a slice of its capabilities → one simple entry point exposes just that slice.
- **Composite:** code treats single items and groups differently, with type checks everywhere → items and groups share one interface in a tree.
- **Flyweight:** many objects store the same data and waste memory → the shared state is stored once and reused.
- **When you don't need them:** an interface you own and can change directly, or one implementation with no cross-cutting concerns; a wrapper there only adds a layer to debug.

## Key points
| Pattern | Intent | JDK / Spring example |
|---|---|---|
| **Adapter** | Let objects with incompatible interfaces work together: a translator that converts the interface or data of one object into a form another object understands. | `Arrays.asList()`, `InputStreamReader` (bytes to chars), `Collections.enumeration()`; `HandlerAdapter` in Spring MVC ([[spring-mvc-request-lifecycle]]), `WebMvcConfigurerAdapter` (removed in 5.x) |
| **Decorator** | Add behaviour to an object dynamically by placing it in a wrapper that implements the same interface, avoiding subclass explosion. Wrappers share a common ancestor with the wrapped object. | `BufferedInputStream(new FileInputStream(f))` ([[java-io-streams]]), `Collections.unmodifiableList()`, `Collections.synchronizedMap()`; `BeanPostProcessor` wrapping beans, `TransactionAwareCacheDecorator` |
| **Proxy** | Substitute a stand-in object that intercepts calls to the real one and does something before or after forwarding (lazy init, access control, logging, remote call). | `java.lang.reflect.Proxy`; Spring AOP proxies behind `@Transactional`, `@Cacheable`, `@Async` ([[spring-aop]], [[spring-transactional]]); Hibernate lazy-loading proxies ([[jpa-persistence-context-and-entity-lifecycle]]) |
| **Bridge** | Split one or more classes into two separate hierarchies, abstraction and implementation, so both can vary independently. | JDBC: `DriverManager` / `Connection` API vs vendor drivers; SLF4J API vs Logback / Log4j2 binding |
| **Facade** | Provide a simple interface to a complex system, library or framework. It may expose a trimmed interface, not 100% of the subsystem, but exactly what the client needs. | `javax.faces.context.FacesContext`, `Executors`; `JdbcTemplate`, `RestClient`, `KafkaTemplate` ([[kafka-producer]]), `SpringApplication.run()` |
| **Composite** | Group objects into a tree and work with the tree as if it were a single object. Only makes sense when the model is naturally a tree. | AWT/Swing `Container` and `Component`; Spring `CompositeCacheManager`, `CompositeMessageConverter` |
| **Flyweight** | Save memory by sharing common (intrinsic) state between many objects instead of storing identical data in each. | `Integer.valueOf()` cache (-128..127) ([[primitives-and-wrappers]]), the string pool ([[strings-and-string-pool]]), `Boolean.TRUE` |

### Telling the wrappers apart
- **Adapter** changes the interface; **Decorator** keeps the interface and adds behaviour; **Proxy** keeps the interface and controls access. Decorator is composed by the client ("stack what you want"); a Proxy usually manages the lifecycle of its target and the client does not know it is there.
- Use **Adapter** also when several existing subclasses lack some common functionality and you cannot extend the superclass.
- Spring's proxies explain the classic bug: `@Transactional` on a method called from the same class does nothing, because the call bypasses the proxy ([[spring-transactional]]).

## Example
```java
// Decorator: add retry to any PaymentGateway without touching implementations.
public interface PaymentGateway {
    PaymentResult charge(Money amount, CardToken token);
}

public final class RetryingGateway implements PaymentGateway {
    private final PaymentGateway delegate;
    private final int maxAttempts;

    public RetryingGateway(PaymentGateway delegate, int maxAttempts) {
        this.delegate = delegate; this.maxAttempts = maxAttempts;
    }

    @Override
    public PaymentResult charge(Money amount, CardToken token) {
        TransientGatewayException last = null;
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            try {
                return delegate.charge(amount, token);        // same interface, extra behaviour
            } catch (TransientGatewayException e) {
                last = e;
            }
        }
        throw last;
    }
}

@Configuration
class GatewayConfig {
    @Bean PaymentGateway paymentGateway(HttpGateway http, MeterRegistry metrics) {
        return new MeteredGateway(new RetryingGateway(http, 3), metrics);   // decorators stack
    }
}
```

## Pitfalls
- Retrying a non-[[idempotency|idempotent]] call (like the charge above) needs an idempotency key, otherwise the decorator double-charges.
- Deep decorator stacks make stack traces and debugging painful; name the wrappers clearly and keep the order explicit in one config class.
- Proxies break `this`-calls, `final` classes/methods (CGLIB cannot subclass them) and field access; Hibernate proxies break `equals` when compared by class ([[equals-and-hashcode]]).
- Facades tend to grow into god classes; keep them thin and delegate.
- Related: [[creational-patterns]], [[behavioral-patterns]], [[solid]].
