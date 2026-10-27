---
title: Java EE, Jakarta EE и как Spring использует спецификации
tags: [jakarta-ee, java-ee, servlet, jpa, specifications]
learned: 2026-10-22
---
## Кратко
Java EE (сейчас **Jakarta EE**) — это набор спецификаций для корпоративных приложений: Servlet, JPA, Bean Validation, JMS, JTA, CDI, JAX-RS и другие. Каждая спецификация — это API плюс TCK; вендоры поставляют реализации (Tomcat, Hibernate, Hibernate Validator, ActiveMQ). Spring — не сервер приложений, но он опирается на многие из этих спецификаций и оборачивает их в более простые модели программирования; начиная со Spring 6 / Boot 3 пакеты называются `jakarta.*`.

## Основное
### История и названия
- J2EE (1999) → Java EE 5–8 (Sun, затем Oracle) → в 2017 году Oracle передала платформу Eclipse Foundation, которая не могла использовать торговую марку `javax`. Jakarta EE 8 (2019) сохранила `javax.*`; **Jakarta EE 9 переименовала все пакеты в `jakarta.*`**; актуальные версии — Jakarta EE 10 (2022) и 11 (2025).
- Следствие: Spring Framework 6 / Spring Boot 3 (ноябрь 2022) требуют `jakarta.*` и Java 17+. Библиотеки, которые всё ещё импортируют `javax.servlet` или `javax.persistence`, не работают с Boot 3; миграция в основном сводится к переименованию пакетов (помогают `spring-boot-properties-migrator` и OpenRewrite).

### Спецификации, которые реально встречаются
| Спецификация | Что стандартизирует | Реализации | В Spring |
|---|---|---|---|
| Servlet | обработка HTTP-запросов/ответов в контейнере; `HttpServlet`, фильтры, слушатели, сессии | Tomcat, Jetty, Undertow | `DispatcherServlet` из Spring MVC расширяет `HttpServlet`; Boot встраивает Tomcat ([[spring-mvc-request-lifecycle]]) |
| JPA (Jakarta Persistence) | ORM: `@Entity`, `EntityManager`, JPQL, контекст персистентности | Hibernate, EclipseLink | репозитории Spring Data JPA поверх Hibernate ([[spring-data-repositories]], [[jpa-entities-and-relationships]]) |
| JDBC | низкоуровневый доступ к SQL: `Connection`, `PreparedStatement`, `ResultSet` (часть Java SE, а не EE) | каждый драйвер | `JdbcTemplate`, `JdbcClient` (6.1), управление транзакциями ([[hikaricp-pool-sizing]]) |
| Bean Validation (JSR 380 / Jakarta Validation 3) | `@NotNull`, `@Size`, `@Pattern`, собственные ограничения, `Validator` | Hibernate Validator | `@Valid`/`@Validated` на контроллерах и сервисах ([[spring-mvc-validation-and-uploads]]) |
| JMS (Jakarta Messaging) | API очередей/топиков сообщений | ActiveMQ Artemis, IBM MQ | `JmsTemplate`, `@JmsListener` ([[activemq-basics]]) |
| JTA | распределённые транзакции, `UserTransaction` | Narayana, Atomikos | `JtaTransactionManager`; нужен редко, `@Transactional` его абстрагирует ([[spring-transactional]]) |
| CDI | внедрение зависимостей, `@Inject`, области видимости (scopes) | Weld | у Spring свой контейнер, но он понимает `@Inject` ([[spring-ioc-and-di]]) |
| JAX-RS | REST-эндпоинты с `@Path`, `@GET` | Jersey, RESTEasy | вместо него Spring MVC/WebFlux; Jersey можно встроить в Boot |
| JSON-B / JSON-P | привязка и разбор JSON | Yasson | в Spring по умолчанию Jackson |
| WebSocket | `@ServerEndpoint` | Tomcat | Spring WebSocket/STOMP |
| Mail, Activation, Annotations (`@PostConstruct`, `@PreDestroy`) | | | `spring-boot-starter-mail`; аннотации жизненного цикла в [[spring-bean-lifecycle]] |

### Почему Spring опирается на эти спецификации
- Это стандарты экосистемы: использование Servlet API означает, что Spring MVC работает в любом контейнере; использование JPA — любой ORM; использование Bean Validation — что одни и те же аннотации работают в веб-слое, в JPA и в тестах.
- Разработчики их уже знают; Spring снижает порог входа, добавляя поверх автоконфигурацию, шаблоны и единую иерархию исключений.
- Затем Spring добавляет то, чего в спецификациях нет или что в них тяжеловесно: лёгкий DI-контейнер вместо полноценного сервера приложений, `@Transactional` вместо церемоний JTA, репозитории вместо рукописного кода с `EntityManager`.

### Сервер приложений против встроенной среды выполнения
- Классический Java EE: WAR/EAR деплоится в WildFly, WebLogic, WebSphere, Payara; сервер предоставляет все спецификации и управляет пулами, транзакциями, безопасностью.
- Spring Boot: исполняемый jar со встроенным контейнером сервлетов; только те спецификации, которые подключены; эксплуатация через Actuator ([[spring-boot-starters-and-autoconfiguration]]). MicroProfile и Quarkus — ответ со стороны Jakarta на ту же потребность.

## Пример
```java
// Контроллер Spring Boot 3: три спецификации Jakarta в десяти строках
// jakarta.validation (Bean Validation), jakarta.servlet (через Spring MVC), jakarta.persistence (JPA)
@RestController
@RequestMapping("/api/v1/esim")
public class EsimController {
    private final EsimProfileRepository repository;   // Spring Data JPA поверх Hibernate

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

## Подводные камни
- Смешение `javax.persistence` и `jakarta.persistence` в одном classpath (старая библиотека плюс Boot 3) даёт сущности, которые Hibernate молча игнорирует, или `NoSuchMethodError` во время выполнения.
- И Jakarta EE, и Spring определяют `@Transactional`; Spring поддерживает `jakarta.transaction.Transactional`, но с меньшим числом атрибутов, поэтому используй `org.springframework.transaction.annotation.Transactional`.
- `@PostConstruct`/`@PreDestroy` переехали из `javax.annotation` (удалён из JDK в 11) в `jakarta.annotation`; зависимость `jakarta.annotation-api` подтягивает Boot.
- JPA — это спецификация, Hibernate — реализация: специфичные для Hibernate аннотации (`@Formula`, `@BatchSize`, `@Type`) привязывают к нему, и это нормально, пока это осознанный выбор.
- Связанные заметки: [[spring-mvc-request-lifecycle]], [[jpa-entities-and-relationships]], [[spring-boot-starters-and-autoconfiguration]].
