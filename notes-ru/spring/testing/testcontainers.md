---
title: Testcontainers для тестов с Postgres, Kafka и Redis
tags: [testing, testcontainers, postgres, kafka, spring-boot-test]
learned: 2026-10-27
---
## Кратко
Testcontainers запускает настоящую инфраструктуру (Postgres 16, Kafka, Redis) в Docker на время прогона тестов, поэтому интеграционные тесты проверяют тот же диалект SQL, те же миграции и ту же семантику брокера, что и в продакшене. В Spring Boot 3.1+ `@ServiceConnection` подключает контейнер к контексту без промежуточных свойств. Набор тестов остаётся быстрым благодаря одному общему статическому контейнеру на JVM, кэшированию контекста и очистке данных вместо перезапусков. Как это вписывается в пирамиду тестов, см. [[testing-types-and-dev-cycles]]; модульный уровень — в [[junit-and-mockito]].

## Основное
### Почему не H2 для тестов Postgres
- H2 в «режиме Postgres» всё равно остаётся H2: нет `jsonb`, нет пограничных случаев `ON CONFLICT ... DO UPDATE`, другое поведение `SKIP LOCKED` и advisory-блокировок, другая семантика последовательностей и `RETURNING`, другое приведение регистра идентификаторов. Тест проходит, продакшен падает.
- Миграции Flyway/Liquibase, написанные для Postgres, часто даже не применяются на H2, поэтому команды держат второй набор миграций для тестов, а это второй источник багов.
- Поведение блокировок и изоляции ([[postgres-isolation-levels]], [[jpa-locking]]) можно протестировать только на настоящем движке.

### Подключение
- `@Testcontainers` (расширение JUnit) управляет полями с аннотацией `@Container`: `static`-поле запускается один раз на класс, поле экземпляра — на каждый тест (медленно, нужно редко).
- `@ServiceConnection` (Boot 3.1+) на поле контейнера заменяет старый блок `@DynamicPropertySource`: Boot распознаёт `PostgreSQLContainer`, `KafkaContainer`/`ConfluentKafkaContainer`, `GenericContainer` с `RedisContainer`/образом с именем `redis`, `RabbitMQContainer` и другие и сам регистрирует `spring.datasource.*`, `spring.kafka.bootstrap-servers`, `spring.data.redis.*`.
- `@DynamicPropertySource` по-прежнему нужен для того, для чего у Boot нет сведений о подключении (собственный сервис, URL WireMock): `static void props(DynamicPropertyRegistry r)`, который вызывает `r.add("sm-dp.url", wiremock::baseUrl)`.
- Зависимости: `spring-boot-testcontainers`, `org.testcontainers:postgresql`, `org.testcontainers:kafka`, `com.redis:testcontainers-redis` (или `GenericContainer`), плюс интеграция с JUnit `org.testcontainers:junit-jupiter`.

### Контейнер-синглтон и повторное использование
- `static`-контейнер в каждом тестовом классе всё равно перезапускается для каждого класса. Паттерн синглтона: абстрактный базовый класс (или `@TestConfiguration` с `@Bean @ServiceConnection`), который запускает контейнер в статическом инициализаторе один раз на JVM; все тестовые классы наследуются от него. Вместе с кэшем контекстов Spring это даёт один Postgres и один контекст на весь модуль.
- `.withReuse(true)` плюс `testcontainers.reuse.enable=true` в `~/.testcontainers.properties` сохраняют контейнер между прогонами на машине разработчика (при втором прогоне нет затрат на запуск). В CI на это не полагайся; sidecar Ryuk обычно убивает контейнеры при завершении JVM.
- В Boot 3.1 также появился `@SpringBootTest` + `SpringApplication.from(App::main).with(TestcontainersConfiguration.class).run()` для локального запуска при разработке с контейнерами вместо поддерживаемого вручную `docker-compose` ([[docker-fundamentals]]).

### Срезы и миграции
- `@DataJpaTest` по умолчанию подменяет источник данных встроенной БД; добавь `@AutoConfigureTestDatabase(replace = NONE)`, чтобы он использовал контейнер. Он `@Transactional` и откатывает каждый тест, что скрывает проблемы с flush и commit ([[jpa-persistence-context-and-entity-lifecycle]]).
- Flyway/Liquibase автоматически применяются к контейнеру при старте контекста, так что миграции тестируются в каждой сборке. Тестовые данные держи в скриптах `@Sql` или построителях (builders), а не в миграциях.
- Очистка между тестами: очищай таблицы в `@AfterEach` (`TRUNCATE ... RESTART IDENTITY CASCADE`) или полагайся на откат; избегай `@DirtiesContext`, который выбрасывает закэшированный контекст.

### Паттерн теста Kafka listener'а
1. Запусти `KafkaContainer` с `@ServiceConnection`; настоящий `@KafkaListener` ([[spring-kafka-consumer]]) входит в контекст.
2. Отправь сообщение через внедрённый `KafkaTemplate` (или во входной топик слушателя через тестовый producer).
3. Жди через Awaitility: `await().atMost(10, SECONDS).untilAsserted(() -> assertThat(repo.findById(id)).isPresent())`. Никогда не используй `Thread.sleep`.
4. Проверяй изменение состояния (записана строка, outbox-событие, вызов заглушки WireMock ниже по цепочке).
- Задай `spring.kafka.consumer.auto-offset-reset=earliest` в тестовых свойствах, иначе слушатель может подписаться уже после отправки записи и никогда её не увидеть.

### Docker в CI
- Testcontainers нужен сокет Docker. В раннере GitLab это означает Docker-out-of-Docker (монтирование `/var/run/docker.sock`) или сервис DinD с `DOCKER_HOST=tcp://docker:2375` и `TESTCONTAINERS_HOST_OVERRIDE`; компромиссы описаны в [[docker-socket-dind-dood]]. В первом прогоне основное время уходит на скачивание образов; используй зеркало реестра или заранее скачанные образы на раннере.

### Советы по скорости
- Один контейнер на JVM, одна конфигурация контекста Spring на модуль, `@Tag("integration")`, чтобы модульные тесты запускались без Docker.
- Маленькие образы (`postgres:16-alpine`), `withTmpFs(Map.of("/var/lib/postgresql/data", "rw"))`, чтобы держать БД в памяти.
- Запускай набор интеграционных тестов в Maven Failsafe (`*IT.java`) отдельно от модульных тестов Surefire, чтобы проблема с Docker не блокировала `mvn test` ([[maven]]).

## Пример
Общая конфигурация плюс интеграционный тест Kafka listener'а.
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
    @MockitoBean HlrClient hlr;                       // внешняя система, которая нам не принадлежит

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
С `@Bean @ServiceConnection` контейнеры запускаются контекстом Spring, кэшируются вместе с ним и используются всеми тестовыми классами, импортирующими ту же конфигурацию.

## Подводные камни
- `@Container` на поле экземпляра или `new PostgreSQLContainer` внутри каждого тестового класса: перезапуск контейнера на каждый класс превращает модуль из 40 тестов в минуты ожидания.
- Смешивание `spring.datasource.url` в `application-test.yml` с `@ServiceConnection`: явное свойство побеждает, и тест молча обращается к несуществующей локальной БД.
- Тесты Kafka, которые проверяют результат сразу после `send()`, нестабильны (flaky); всегда жди. Кроме того, давай consumer group уникальные id для каждого тестового класса, иначе просачиваются offset'ы из предыдущего класса.
- `@Transactional` на тесте, который запускает `@KafkaListener` или `@Async`-метод: слушатель работает в другом потоке и не видит незакоммиченных строк.
- CI без сокета Docker падает с «Could not find a valid Docker environment»; это проблема инфраструктуры, а не кода ([[docker-socket-dind-dood]]).
- Связанные заметки: тесты ретранслятора [[outbox-pattern]] — классический случай Postgres + Kafka, [[hikaricp-pool-sizing]] — про настройки пула, когда много контекстов используют один контейнер.
