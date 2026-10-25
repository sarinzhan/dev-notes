---
title: Java EE, Jakarta EE and how Spring uses the specifications
tags: [jakarta-ee, java-ee, servlet, jpa, specifications]
learned: 2026-10-22
---
## TL;DR
Java EE (now **Jakarta EE**) is a set of specifications for enterprise applications: Servlet, JPA, Bean Validation, JMS, JTA, CDI, JAX-RS and others. Each is an API plus a TCK; vendors ship implementations (Tomcat, Hibernate, Hibernate Validator, ActiveMQ). Spring is not an application server, but it builds on many of these specs and wraps them in simpler programming models; since Spring 6 / Boot 3 the packages are `jakarta.*`.

## Key points
### History and naming
- J2EE (1999) → Java EE 5–8 (Sun, then Oracle) → in 2017 Oracle handed the platform to the Eclipse Foundation, which could not use the `javax` trademark. Jakarta EE 8 (2019) kept `javax.*`; **Jakarta EE 9 renamed every package to `jakarta.*`**; Jakarta EE 10 (2022) and 11 (2025) are the current ones.
- Consequence: Spring Framework 6 / Spring Boot 3 (Nov 2022) require `jakarta.*` and Java 17+. Libraries still importing `javax.servlet` or `javax.persistence` do not work with Boot 3; the migration is mostly a package rename (`spring-boot-properties-migrator` and OpenRewrite help).

### The specifications you actually meet
| Spec | What it standardizes | Implementations | In Spring |
|---|---|---|---|
| Servlet | HTTP request/response handling in a container; `HttpServlet`, filters, listeners, sessions | Tomcat, Jetty, Undertow | Spring MVC's `DispatcherServlet` extends `HttpServlet`; Boot embeds Tomcat ([[spring-mvc-request-lifecycle]]) |
| JPA (Jakarta Persistence) | ORM: `@Entity`, `EntityManager`, JPQL, persistence context | Hibernate, EclipseLink | Spring Data JPA repositories on top of Hibernate ([[spring-data-repositories]], [[jpa-entities-and-relationships]]) |
| JDBC | low-level SQL access: `Connection`, `PreparedStatement`, `ResultSet` (part of Java SE, not EE) | every driver | `JdbcTemplate`, `JdbcClient` (6.1), transaction management ([[hikaricp-pool-sizing]]) |
| Bean Validation (JSR 380 / Jakarta Validation 3) | `@NotNull`, `@Size`, `@Pattern`, custom constraints, `Validator` | Hibernate Validator | `@Valid`/`@Validated` on controllers and services ([[spring-mvc-validation-and-uploads]]) |
| JMS (Jakarta Messaging) | message queues/topics API | ActiveMQ Artemis, IBM MQ | `JmsTemplate`, `@JmsListener` ([[activemq-basics]]) |
| JTA | distributed transactions, `UserTransaction` | Narayana, Atomikos | `JtaTransactionManager`; rarely needed, `@Transactional` abstracts it ([[spring-transactional]]) |
| CDI | dependency injection, `@Inject`, scopes | Weld | Spring has its own container but understands `@Inject` ([[spring-ioc-and-di]]) |
| JAX-RS | REST endpoints with `@Path`, `@GET` | Jersey, RESTEasy | Spring MVC/WebFlux instead; Jersey can be embedded in Boot |
| JSON-B / JSON-P | JSON binding and parsing | Yasson | Jackson is Spring's default |
| WebSocket | `@ServerEndpoint` | Tomcat | Spring WebSocket/STOMP |
| Mail, Activation, Annotations (`@PostConstruct`, `@PreDestroy`) | | | `spring-boot-starter-mail`; lifecycle annotations in [[spring-bean-lifecycle]] |

### Why Spring builds on these specs
- They are the ecosystem's standards: using the Servlet API means Spring MVC runs on any container; using JPA means any ORM; using Bean Validation means the same annotations work in the web layer, in JPA and in tests.
- Developers already know them; Spring lowers the entry cost by adding auto-configuration, templates and a consistent exception hierarchy on top.
- Spring then adds what the specs lack or make heavy: a lightweight DI container instead of a full application server, `@Transactional` instead of JTA ceremony, repositories instead of hand-written `EntityManager` code.

### Application server vs embedded runtime
- Classic Java EE: deploy a WAR/EAR to WildFly, WebLogic, WebSphere, Payara; the server provides every spec and manages pooling, transactions, security.
- Spring Boot: an executable jar with an embedded servlet container; only the specs you pull in; ops via Actuator ([[spring-boot-starters-and-autoconfiguration]]). MicroProfile and Quarkus are the Jakarta-side answer to the same need.

## Example
```java
// Spring Boot 3 controller: three Jakarta specs in ten lines
// jakarta.validation (Bean Validation), jakarta.servlet (via Spring MVC), jakarta.persistence (JPA)
@RestController
@RequestMapping("/api/v1/esim")
public class EsimController {
    private final EsimProfileRepository repository;   // Spring Data JPA over Hibernate

    public EsimController(EsimProfileRepository repository) { this.repository = repository; }

    public record ActivateRequest(
            @NotBlank @Pattern(regexp = "\\d{19,20}") String iccid,
            @NotBlank String msisdn) {}

    @PostMapping("/activate")
    public ResponseEntity<Void> activate(@Valid @RequestBody ActivateRequest req,
                                         HttpServletRequest http) {          // jakarta.servlet.http
        EsimProfile profile = repository.findByIccid(req.iccid())
                .orElseThrow(() -> new EntityNotFoundException(req.iccid())); // jakarta.persistence
        profile.activate(req.msisdn(), http.getRemoteAddr());
        return ResponseEntity.noContent().build();
    }
}
```

## Pitfalls
- Mixing `javax.persistence` and `jakarta.persistence` on one classpath (an old library plus Boot 3) produces entities that Hibernate silently ignores or `NoSuchMethodError`s at runtime.
- Jakarta EE and Spring both define `@Transactional`; Spring supports `jakarta.transaction.Transactional` but with fewer attributes, so use `org.springframework.transaction.annotation.Transactional`.
- `@PostConstruct`/`@PreDestroy` moved from `javax.annotation` (removed from the JDK in 11) to `jakarta.annotation`; the dependency `jakarta.annotation-api` is brought by Boot.
- JPA is the spec, Hibernate the implementation: Hibernate-specific annotations (`@Formula`, `@BatchSize`, `@Type`) lock you to it, which is fine as long as it is a conscious choice.
- Related: [[spring-mvc-request-lifecycle]], [[jpa-entities-and-relationships]], [[spring-boot-starters-and-autoconfiguration]].
