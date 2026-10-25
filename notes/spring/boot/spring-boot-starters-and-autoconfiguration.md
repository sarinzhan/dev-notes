---
title: Spring Boot: starters, autoconfiguration and @SpringBootApplication
tags: [spring-boot, autoconfiguration, starters, conditional]
learned: 2026-10-22
---
## TL;DR
Spring Boot is a layer on top of Spring Framework that makes an application runnable with almost no configuration: **convention over configuration**, an **embedded server** (Tomcat by default), and **autoconfiguration** that creates beans based on what is on the classpath and in the properties. **Starters** are curated dependency bundles that bring the libraries and the matching autoconfiguration. Boot 3.x requires Java 17+, Spring Framework 6 and the Jakarta EE 9+ namespace (`jakarta.*`).

## Key points
### Core ideas
- **Simplified configuration**: sensible defaults; you override only what differs ([[spring-boot-properties-and-profiles]]).
- **Embedded server**: the app is a plain `main()`; Tomcat/Jetty/Undertow runs inside the JVM, packaged as a fat jar by the Boot Maven/Gradle plugin ([[maven]]). No external servlet container to deploy into.
- **Autoconfiguration**: Boot inspects the classpath and the environment and registers beans you did not declare: a `DataSource` if a JDBC driver and `spring.datasource.url` exist, a `KafkaTemplate` if `spring-kafka` is present, an `ObjectMapper`, a `DispatcherServlet`, etc.
- **Classpath**: the set of directories and jars where the JVM (or the build tool) looks for `.class` files and resources at run time. Autoconfiguration keys off it: "is `org.postgresql.Driver` loadable?".

### @SpringBootApplication
A composite of three annotations on the main class:
- `@SpringBootConfiguration` (a `@Configuration`) so the class itself can declare `@Bean`s.
- `@ComponentScan`: scans the package of the main class and its sub-packages for stereotypes. Put the main class in the root package; a class outside that package is not found.
- `@EnableAutoConfiguration`: imports the autoconfiguration classes listed in each jar's `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports` (Boot 2.7+; before that `spring.factories`). Exclude one with `exclude = DataSourceAutoConfiguration.class` or `spring.autoconfigure.exclude`.

### Starters
A starter is a pom with no code of its own: it pulls in a coherent set of libraries with compatible versions (managed by `spring-boot-dependencies`). Examples: `spring-boot-starter-web` (Spring MVC + Jackson + embedded Tomcat), `-data-jpa` (Hibernate + Spring Data + HikariCP), `-security`, `-validation`, `-actuator`, `-test`. Third parties follow the convention `<name>-spring-boot-starter`. A starter plus its autoconfiguration is the "just add the dependency and it works" experience.

### How autoconfiguration decides: @Conditional
`@Conditional(SomeCondition.class)` takes a `Condition` with a single method `boolean matches(ConditionContext, AnnotatedTypeMetadata)`:
- `ConditionContext` gives access to the `BeanDefinitionRegistry`, `BeanFactory`, `Environment`, `ResourceLoader` and `ClassLoader`.
- `AnnotatedTypeMetadata` exposes the annotations on the target class or method.

Boot's derived annotations:
| Annotation | True when |
|---|---|
| `@ConditionalOnClass` / `OnMissingClass` | the class is (not) on the classpath |
| `@ConditionalOnBean` / `OnMissingBean` | a bean of that type/name is (not) already defined; this is how your own `@Bean` overrides the default |
| `@ConditionalOnProperty` | a property has a given value (`matchIfMissing` for defaults) |
| `@ConditionalOnResource` | a resource exists (`classpath:...`) |
| `@ConditionalOnJava` | the JVM version matches |
| `@ConditionalOnWebApplication` | servlet or reactive web app |
| `@ConditionalOnExpression` | a SpEL expression is true |

Autoconfiguration classes are processed **after** user configuration, so `@ConditionalOnMissingBean` lets your bean win. The `@AutoConfiguration(after = ..., before = ...)` annotation (Boot 2.7+) orders them. Run with `--debug` or look at the actuator `conditions` endpoint to see the "condition evaluation report": which autoconfigs matched and why.

### Startup sequence in one line
`SpringApplication.run()` → build `Environment` → create `ApplicationContext` → register main class → `refresh()` (scan, autoconfigure, create beans, see [[spring-bean-lifecycle]]) → start embedded server → run `ApplicationRunner`/`CommandLineRunner`.

## Example
A small autoconfiguration for an internal library (the pattern every starter uses):
```java
@AutoConfiguration
@ConditionalOnClass(TelegramClient.class)
@ConditionalOnProperty(prefix = "alerts.telegram", name = "enabled", havingValue = "true")
@EnableConfigurationProperties(TelegramProps.class)
public class TelegramAlertsAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    TelegramClient telegramClient(TelegramProps props, RestClient.Builder builder) {
        return new TelegramClient(builder.baseUrl(props.baseUrl()).build(), props.botToken());
    }
}
```
Registered in `src/main/resources/META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`:
```
kg.beeline.alerts.TelegramAlertsAutoConfiguration
```

## Pitfalls
- Putting the main class in a deep package (`com.x.app.boot`) makes `@ComponentScan` miss `com.x.app.service`; move the class or set `scanBasePackages`.
- Autoconfiguration is not magic: if a bean is missing, read the condition report instead of adding random annotations.
- Mixing starter versions by hand breaks the version alignment the BOM gives you; let `spring-boot-dependencies` manage them.
- Boot 3 moved everything from `javax.*` to `jakarta.*`; old libraries that still use `javax.servlet` or `javax.persistence` do not work ([[java-ee-and-jakarta]]).
- `spring.factories` autoconfiguration registration was removed in Boot 3.0; use the `.imports` file.
- Related: [[spring-boot-properties-and-profiles]], [[spring-bean-scopes-and-configuration]], [[spring-ioc-and-di]].
