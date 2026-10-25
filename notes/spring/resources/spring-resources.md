---
title: Spring resources: links, tools and templates
tags: [spring, resources, links, tools]
learned: 2026-10-22
---
## TL;DR
Collected links and tools for the Spring section: interview question lists, deep dives into the container internals, admin-panel generators, UI templates and project starters. Knowledge notes do not link outside the vault; everything external lives here.

## Key points
### Interview preparation
- [Spring interview questions (sibinfo.ru)](https://www.sibinfo.ru/about/articles/java-spring-sobesedovanie.html): Russian-language list of typical Spring questions with short answers; good checklist before an interview.
- [Spring interview questions and answers (DigitalOcean)](https://www.digitalocean.com/community/tutorials/spring-interview-questions-and-answers): English list covering core, MVC, AOP, transactions; the answers map onto [[spring-ioc-and-di]], [[spring-bean-lifecycle]], [[spring-aop]], [[spring-transactional]].
- [Habr: Spring questions deep dive (article 490586)](https://habr.com/ru/articles/490586/): longer Russian write-up of container internals and tricky questions.
- [JavaRevisited, SQL label](https://javarevisited.blogspot.com/search/label/SQL?max-results=3): blog with Java and SQL interview topics; uneven quality but wide coverage.
- [JSE helper: Java Junior interview Q&A](https://jsehelper.blogspot.com/search/label/Interview?updated-max=2016-04-08T19%3A01%3A00%2B03%3A00&max-results=5): old (2016) Russian junior-level Q&A; useful for the basics, check versions against current facts.

### Container internals
- [Medium: "Spring under the hood" (Kirill Sereda, Russian)](https://medium.com/@kirill.sereda/spring-под-капотом-9d92f2bf1a04): step-by-step walk through context refresh, `BeanFactoryPostProcessor`, `BeanPostProcessor` and proxy creation; the basis for [[spring-bean-lifecycle]].
- DMDEV (YouTube/Udemy, Russian): Spring course with a thorough lifecycle and AOP module.
- Evgeny Borisov, "Spring-потрошитель" / "Spring Ripper" (JPoint talks, Russian, on YouTube): the classic deep dive into BPP/BFPP, proxies and `@Configuration` magic; parts 1 and 2.
- [YouTube playlist on Spring (video mhofV15i4Wc)](https://www.youtube.com/watch?v=mhofV15i4Wc): Russian Spring playlist saved from the notes.

### Official docs worth bookmarking
- Spring Framework reference (docs.spring.io/spring-framework): Core (IoC, AOP), Web (MVC, WebFlux), Data Access (transactions).
- Spring Boot reference: "Core Features" (externalized configuration, profiles), "Production-ready Features" (Actuator), and the appendix with all `application.properties` keys ([[spring-boot-properties-and-profiles]]).
- Spring Security reference: Servlet "Architecture" chapter and the 5→6 migration guide ([[spring-security-basics]]).
- springdoc-openapi docs for Swagger UI setup ([[openapi-swagger]]).

### Admin panels and code generation
- SnapAdmin: drop-in dependency that builds a CRUD admin UI from your JPA entities; zero code, limited customization.
- KraftAdmin: similar dependency-based admin panel over JPA entities.
- Amplicode: IntelliJ plugin that generates Spring code (entities, repositories, controllers, admin UI) and can be configured per project.
- Spring Boot Admin: not a CRUD panel but an application monitoring UI over Actuator endpoints (health, metrics, loggers, env) for all registered instances.
- JHipster: full application generator (Spring Boot backend + Angular/React/Vue frontend, auth, CI) for bootstrapping a project.

### UI templates for admin frontends
- Velzon, Nazox, Adminto: commercial Bootstrap admin dashboard templates (Themesbrand) used as a starting point for internal tools' frontends.

### Project templates
- [spring-boot-template-project (hieubz, GitHub)](https://github.com/hieubz/spring-boot-template-project): opinionated Spring Boot skeleton with layered structure, exception handling and Docker setup; compare with your own conventions before copying.
- start.spring.io: the official initializer; the fastest way to get a correct Boot 3.x build file with managed versions ([[spring-boot-starters-and-autoconfiguration]]).

### Testing tools referenced in notes
- WireMock, MockRestServiceServer, Testcontainers, Spring Cloud Contract, MockServer: see the comparison in [[rest-clients]] and [[testing-types-and-dev-cycles]].

## Pitfalls
- Several of these links are Russian-language and some are old (2016-2020): verify every version-specific fact (Spring Security 6 config style, `jakarta.*`, `RestClient`) against the current reference docs before repeating it in an interview.
- Generated admin panels expose every entity by default; put them behind [[spring-security-basics]] and never on a public host.
- Related: [[architecture-resources]], [[devops-resources]].
