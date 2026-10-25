---
title: IoC container and dependency injection in Spring
tags: [spring-core, ioc, dependency-injection, beans]
learned: 2026-10-22
---
## TL;DR
**Inversion of Control** means the framework, not your code, creates objects and wires them together. **Dependency injection** is the concrete mechanism: a class declares what it needs (constructor parameters, setters or fields) and the container supplies an implementation when it builds the bean. The container is a `BeanFactory`; in practice you always use its richer subtype `ApplicationContext`.

## Key points
### Definitions (interview answers)
- **Framework**: a set of libraries that solve typical problems (configuration, wiring, web, data access) and dictate the structure of the application; the framework calls your code, not the other way round.
- **IoC**: control over object creation and the program flow moves from the application to the container. Spring manages the lifecycle of objects (beans) and their dependencies.
- **DI**: a design pattern that gives loose coupling between collaborating POJOs. Instead of calling `new`, a class declares a dependency on an interface and Spring injects an implementation. DI is one way to implement IoC (the other classic one is Service Locator).
- **Bean**: any object whose lifecycle is managed by the container.

### Why use it
- Fewer hard links: classes depend on abstractions, not concrete classes.
- Easier testing: a dependency can be replaced by a mock or a fake without touching the class.
- Simpler configuration: you say what you need, the container creates and wires it.

### Three injection styles
| Style | How | Verdict |
|---|---|---|
| Constructor | `final` fields set via the constructor; a single constructor needs no `@Autowired` (Spring 4.3+) | Preferred: immutable, dependencies are mandatory and visible, works without Spring in tests |
| Setter (method) | `@Autowired` on a setter | For optional dependencies or to break a circular reference |
| Field | `@Autowired` on a private field | Shortest but hides dependencies, needs reflection in tests, allows circular references to go unnoticed; avoid |

### BeanFactory vs ApplicationContext
- `BeanFactory` is the basic container: create, look up and configure beans; lazy by default (a bean is created on first `getBean`).
- `ApplicationContext` extends it and adds: eager creation of singletons at startup (so misconfiguration fails fast), `Environment` and property resolution, `MessageSource` (i18n), application events (`ApplicationEventPublisher`), resource loading, and automatic registration of `BeanPostProcessor`s. Common implementations: `AnnotationConfigApplicationContext`, `GenericWebApplicationContext`, and in Boot `AnnotationConfigServletWebServerApplicationContext`.
- The container holds `BeanDefinition`s (class, scope, constructor args, dependencies) built from XML, annotations or `@Configuration` classes, then instantiates beans from them; see [[spring-bean-lifecycle]] for the steps and [[spring-bean-scopes-and-configuration]] for the configuration styles.

### Resolving an ambiguous dependency
When several beans implement the injected interface, Spring throws `NoUniqueBeanDefinitionException`. Options: `@Primary` on the default candidate, `@Qualifier("name")` at the injection point, a parameter name that matches the bean name, inject a `List<T>`/`Map<String, T>` of all candidates (`@Order` controls the list order), or make the beans conditional with `@Profile` ([[spring-boot-properties-and-profiles]]). You can also inject `ObjectProvider<T>` or `Optional<T>` for optional dependencies.

## Example
```java
public interface SmsSender { void send(String msisdn, String text); }

@Component @Primary
class TwilioSmsSender implements SmsSender { /* ... */ }

@Component @Qualifier("local")
class LocalGatewaySmsSender implements SmsSender { /* ... */ }

@Service
public class OtpService {
    private final SmsSender sender;
    private final List<SmsSender> allSenders;   // both, ordered by @Order

    OtpService(@Qualifier("local") SmsSender sender, List<SmsSender> allSenders) {
        this.sender = sender;
        this.allSenders = allSenders;
    }
}
```

## Pitfalls
- A circular dependency between two constructor-injected beans fails at startup (`BeanCurrentlyInCreationException`); Boot 2.6+ also forbids circular references by default. Fix the design (extract a third bean) rather than switching to field injection or `@Lazy`.
- Calling an injected bean's method from the constructor is fine, but `@PostConstruct`-style work must not happen in the constructor of a bean that others depend on before the context is ready.
- Injecting a `prototype` bean into a `singleton` gives you one instance forever; use `ObjectProvider` or a scoped proxy ([[spring-bean-scopes-and-configuration]]).
- Autowiring by type works through proxies: inject the interface, not the class, when the bean is proxied by [[spring-aop]] or [[spring-transactional]].
- Related: [[spring-bean-lifecycle]], [[spring-boot-starters-and-autoconfiguration]], [[solid]] (dependency inversion principle).
