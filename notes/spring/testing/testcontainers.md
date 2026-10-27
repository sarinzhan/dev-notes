---
title: Testcontainers for Postgres, Kafka and Redis tests
tags: [testing, testcontainers, postgres, kafka, spring-boot-test]
learned: 2026-10-27
---
## TL;DR
Testcontainers starts real infrastructure (Postgres 16, Kafka, Redis) in Docker for the duration of a test run, so integration tests exercise the same SQL dialect, migrations and broker semantics as production. In Spring Boot 3.1+ `@ServiceConnection` wires the container into the context with no property glue. Keep the suite fast with one shared static container per JVM, context caching, and cleanup instead of restarts. For how this fits the pyramid see [[testing-types-and-dev-cycles]]; for the unit layer see [[junit-and-mockito]].

## Key points
### Why not H2 for Postgres tests
- H2 in "Postgres mode" is still H2: no `jsonb`, no `ON CONFLICT ... DO UPDATE` edge cases, different `SKIP LOCKED` and advisory-lock behaviour, different sequence and `RETURNING` semantics, different case folding of identifiers. The test passes, production fails.
- Flyway/Liquibase migrations written for Postgres often do not even apply on H2, so teams keep a second migration set for tests, which is a second source of bugs.
- Lock and isolation behaviour ([[postgres-isolation-levels]], [[jpa-locking]]) can only be tested against the real engine.

### Wiring
- `@Testcontainers` (JUnit extension) manages fields annotated `@Container`: a `static` field starts once per class, an instance field starts per test (slow, rarely wanted).
- `@ServiceConnection` (Boot 3.1+) on the container field replaces the old `@DynamicPropertySource` block: Boot detects `PostgreSQLContainer`, `KafkaContainer`/`ConfluentKafkaContainer`, `GenericContainer` with `RedisContainer`/image name `redis`, `RabbitMQContainer` and others, and registers `spring.datasource.*`, `spring.kafka.bootstrap-servers`, `spring.data.redis.*` for you.
- `@DynamicPropertySource` is still needed for things Boot has no connection detail for (a custom service, a WireMock URL): a `static void props(DynamicPropertyRegistry r)` that calls `r.add("sm-dp.url", wiremock::baseUrl)`.
- Dependencies: `spring-boot-testcontainers`, `org.testcontainers:postgresql`, `org.testcontainers:kafka`, `com.redis:testcontainers-redis` (or a `GenericContainer`), plus the JUnit integration `org.testcontainers:junit-jupiter`.

### Singleton container and reuse
- A `static` container in each test class still restarts per class. The singleton pattern: an abstract base class (or a `@TestConfiguration` with `@Bean @ServiceConnection`) that starts the container in a static initializer once per JVM; every test class extends it. Combined with Spring's context cache this gives one Postgres and one context for the whole module.
- `.withReuse(true)` plus `testcontainers.reuse.enable=true` in `~/.testcontainers.properties` keeps the container alive between runs on a developer machine (no startup cost on the second run). Do not rely on it in CI; the Ryuk sidecar normally kills containers when the JVM exits.
- Boot 3.1 also added `@SpringBootTest` + `SpringApplication.from(App::main).with(TestcontainersConfiguration.class).run()` for a local dev run with containers instead of a hand-maintained `docker-compose` ([[docker-fundamentals]]).

### Slices and migrations
- `@DataJpaTest` swaps the datasource for an embedded DB by default; add `@AutoConfigureTestDatabase(replace = NONE)` so it uses the container. It is `@Transactional` and rolls back each test, which hides flush and commit problems ([[jpa-persistence-context-and-entity-lifecycle]]).
- Flyway/Liquibase run automatically against the container on context start, so the migrations are tested on every build. Keep test data in `@Sql` scripts or builders, not in migrations.
- Cleanup between tests: truncate tables in `@AfterEach` (`TRUNCATE ... RESTART IDENTITY CASCADE`) or rely on rollback; avoid `@DirtiesContext`, which throws away the cached context.

### Kafka listener test pattern
1. Start `KafkaContainer` with `@ServiceConnection`; the real `@KafkaListener` ([[spring-kafka-consumer]]) is part of the context.
2. Produce a message with an injected `KafkaTemplate` (or the listener's input topic via a test producer).
3. Wait with Awaitility: `await().atMost(10, SECONDS).untilAsserted(() -> assertThat(repo.findById(id)).isPresent())`. Never `Thread.sleep`.
4. Assert on the state change (row written, outbox event, downstream call to a WireMock stub).
- Set `spring.kafka.consumer.auto-offset-reset=earliest` in test properties, otherwise the listener may subscribe after the record was produced and never see it.

### Docker in CI
- Testcontainers needs a Docker socket. In a GitLab runner this means Docker-out-of-Docker (mount `/var/run/docker.sock`) or a DinD service with `DOCKER_HOST=tcp://docker:2375` and `TESTCONTAINERS_HOST_OVERRIDE`; trade-offs in [[docker-socket-dind-dood]]. Image pulls dominate the first run; use a registry mirror or pre-pulled images on the runner.

### Speed tips
- One container per JVM, one Spring context configuration per module, `@Tag("integration")` so unit tests run without Docker.
- Small images (`postgres:16-alpine`), `withTmpFs(Map.of("/var/lib/postgresql/data", "rw"))` to keep the DB in memory.
- Run the IT suite in Maven Failsafe (`*IT.java`) separately from Surefire unit tests so a Docker problem does not block `mvn test` ([[maven]]).

## Example
Shared configuration plus a Kafka listener integration test.
```java
@TestConfiguration(proxyBeanMethods = false)
public class TestcontainersConfig {

    @Bean @ServiceConnection
    PostgreSQLContainer<?> postgres() {
        return new PostgreSQLContainer<>("postgres:16-alpine")
                .withTmpFs(Map.of("/var/lib/postgresql/data", "rw"));
    }

    @Bean @ServiceConnection
    ConfluentKafkaContainer kafka() {
        return new ConfluentKafkaContainer("confluentinc/cp-kafka:7.6.1");
    }

    @Bean @ServiceConnection(name = "redis")
    GenericContainer<?> redis() {
        return new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);
    }
}

@SpringBootTest
@Import(TestcontainersConfig.class)
@TestPropertySource(properties = "spring.kafka.consumer.auto-offset-reset=earliest")
class ActivationListenerIT {

    @Autowired KafkaTemplate<String, String> kafka;
    @Autowired ActivationRepository activations;
    @MockitoBean HlrClient hlr;                       // external system we do not own

    @AfterEach
    void clean() { activations.deleteAllInBatch(); }

    @Test
    void activatesSubscriberWhenProfileDownloaded() {
        when(hlr.activate("996555000001")).thenReturn(HlrResult.OK);

        kafka.send("esim.profile-downloaded", "order-7",
                """{"orderId":"order-7","msisdn":"996555000001","iccid":"8999..01"}""");

        await().atMost(Duration.ofSeconds(10)).untilAsserted(() -> {
            var a = activations.findByOrderId("order-7").orElseThrow();
            assertThat(a.status()).isEqualTo(ActivationStatus.ACTIVE);
        });
        verify(hlr, times(1)).activate("996555000001");
    }
}
```
With `@Bean @ServiceConnection` the containers are started by the Spring context, cached with it, and shared by every test class that imports the same configuration.

## Pitfalls
- A `@Container` on an instance field or a `new PostgreSQLContainer` inside each test class: a container restart per class makes a 40-test module take minutes.
- Mixing `spring.datasource.url` in `application-test.yml` with `@ServiceConnection`: the explicit property wins and the test silently talks to a non-existent local DB.
- Kafka tests that assert immediately after `send()` are flaky; always await. Also give consumer groups unique ids per test class or offsets from a previous class leak in.
- `@Transactional` on a test that triggers a `@KafkaListener` or an `@Async` method: the listener runs in another thread and cannot see uncommitted rows.
- CI without a Docker socket fails with "Could not find a valid Docker environment"; this is an infrastructure issue, not a code one ([[docker-socket-dind-dood]]).
- Related: [[outbox-pattern]] relay tests are the classic Postgres + Kafka case, [[hikaricp-pool-sizing]] for pool settings when many contexts share one container.
