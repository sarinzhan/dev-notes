---
title: Spring AOP: aspects, proxies and their limits
tags: [spring-core, aop, proxies, cglib]
learned: 2026-10-22
---
## TL;DR
**Aspect-oriented programming** moves *cross-cutting concerns* (logging, transactions, security checks, metrics, retries) out of business code into separate modules called aspects. Spring AOP implements this at runtime with **proxies**: the container wraps a bean in a JDK dynamic proxy or a CGLIB subclass, and the proxy runs advice before/after/around the real method. Everything Spring does declaratively with annotations (`@Transactional`, `@Async`, `@Cacheable`, `@PreAuthorize`, `@Retryable`) is this mechanism.

## Key points
### Vocabulary
- **Cross-cutting concern**: logic needed in many unrelated places; without AOP it is *scattered* across modules and *tangled* with business logic.
- **Aspect**: a class (`@Aspect`, registered as a bean) that holds advice and pointcuts.
- **Join point**: a point in execution where advice can run; in Spring AOP always a *method execution* on a bean (no field access or constructor join points, unlike full AspectJ).
- **Pointcut**: an expression selecting join points, e.g. `execution(* kg.beeline.esim.service.*.*(..))` or `@annotation(Audited)`.
- **Advice**: the code to run. Types: `@Before`, `@AfterReturning`, `@AfterThrowing`, `@After` (finally), `@Around` (receives `ProceedingJoinPoint` and decides whether and how to call `proceed()`).
- **Weaving**: linking aspects with code. Spring weaves at runtime by proxying; AspectJ can weave at compile time or load time and has no proxy limitations.

### JDK dynamic proxy vs CGLIB
| | JDK dynamic proxy | CGLIB |
|---|---|---|
| Mechanism | `java.lang.reflect.Proxy` implements the bean's **interfaces** | generates a **subclass** of the bean class at runtime |
| Requires | the bean implements at least one interface; injection point must be typed as the interface | class and advised methods not `final`; a visible no-arg constructor is no longer needed (Objenesis) |
| Cost | reflection on each call | slightly slower to create, calls go through `MethodInterceptor`; fine in practice |
| Default | Spring Framework: JDK if interfaces exist | **Spring Boot defaults to CGLIB** (`spring.aop.proxy-target-class=true`) since 2.0 |

### How a call flows
`caller → proxy → [advice chain] → target.method()`. The proxy holds a reference to the real object; the real object knows nothing about the proxy.

### Enabling it
Boot's `spring-boot-starter-aop` (AspectJ annotations + `spring-aop`) auto-enables `@EnableAspectJAutoProxy`. `@Transactional` and `@Async` work without the starter because they use Spring's own proxy infrastructure (`InfrastructureAdvisorAutoProxyCreator`).

## Example
```java
@Aspect
@Component
public class TimingAspect {

    private final MeterRegistry registry;
    TimingAspect(MeterRegistry registry) { this.registry = registry; }

    @Around("@annotation(timed)")
    public Object time(ProceedingJoinPoint pjp, Timed timed) throws Throwable {
        var sample = Timer.start(registry);
        try {
            return pjp.proceed();
        } finally {
            sample.stop(registry.timer(timed.value(),
                    "method", pjp.getSignature().toShortString()));
        }
    }
}

@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.METHOD)
public @interface Timed { String value(); }

@Service
public class EsimOrderService {
    @Timed("esim.order.activate")
    public void activate(String iccid) { /* ... */ }
}
```

## Pitfalls
- **Self-invocation bypasses the proxy.** `this.activate()` inside the same bean is a plain Java call: no transaction, no timing, no caching. Fix: move the method to another bean, or inject the bean into itself via `ObjectProvider`/`@Lazy` (ugly), or use AspectJ weaving.
- Only `public` methods are advised by default (CGLIB can intercept `protected`/package-private, but Spring's `@Transactional` ignores them unless configured otherwise in Spring 6.0+).
- `final` classes and methods cannot be proxied by CGLIB; the aspect is silently skipped for a `final` method.
- Aspect order matters when several apply (`@Order`); e.g. a retry aspect should sit *outside* the transaction so each attempt gets a fresh transaction.
- Proxy type and casting: with a JDK proxy, `(EsimOrderService) bean` throws `ClassCastException`; inject the interface.
- Exceptions thrown from `@Around` advice replace the original one; rethrow carefully.
- Related: [[spring-transactional]] is the most important aspect you use daily; [[spring-bean-lifecycle]] shows at which step the proxy is created; [[structural-patterns]] (Proxy, Decorator) and [[object-class-and-reflection]] for the underlying Java mechanics.
