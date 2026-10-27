---
title: Тестирование асинхронных процессов и потоков Kafka
tags: [testing, kafka, awaitility, async, spring-kafka]
learned: 2026-10-27
---
## Кратко
Асинхронный процесс (`@KafkaListener`, `@Async`-метод, outbox-ретранслятор по `@Scheduled`, activity в Temporal) завершается в другом потоке в неизвестный момент, поэтому тест не может проверять результат сразу после запуска. Правила: **опрашивай ожидаемое состояние через Awaitility**, а не засыпай; **управляй временем**, внедряя `Clock` и вызывая запланированные методы напрямую, а не дожидаясь планировщика; **делай асинхронность наблюдаемой** (строка в репозитории, outbox-запись, запрос в WireMock, `CountDownLatch` в тестовом слушателе). В качестве брокера предпочитай Kafka из Testcontainers, а не `@EmbeddedKafka`; само подключение описано в [[testcontainers]], а эта заметка — про проектирование тестов поверх него.

## Основное
### Awaitility
- `await().atMost(Duration.ofSeconds(10)).pollInterval(Duration.ofMillis(100)).untilAsserted(() -> assertThat(repo.findById(id)).hasValueSatisfying(a -> assertThat(a.status()).isEqualTo(ACTIVE)))`: повторно выполняет лямбду, пока она не пройдёт или пока таймаут не уронит тест с последней ошибкой проверки.
- `until(callable, matcher)` — для булева условия, `pollDelay(Duration.ZERO)` — чтобы проверить сразу, `ignoreExceptions()` — когда первые опросы бросают исключения (строки ещё нет).
- `during(Duration)` проверяет, что условие *остаётся* истинным: полезно для тестов вида «сообщение НЕ было обработано дважды за 2 с».
- Задавай щедрый `atMost` (10–15 с) для CI-машин; тест завершается, как только условие выполнено, поэтому большой таймаут ничего не стоит при успехе.
- `Thread.sleep(2000)` одновременно слишком долгий (за него платит каждый тест) и слишком короткий (CI под нагрузкой падает случайным образом). Замени каждый такой вызов.

### Встроенная Kafka и Testcontainers
| | `@EmbeddedKafka` (spring-kafka-test) | `KafkaContainer` / `ConfluentKafkaContainer` |
|---|---|---|
| Запуск | ~2 с, внутри JVM | ~5–10 с, нужен Docker |
| Достоверность | брокер эпохи ZooKeeper в той же JVM; режим KRaft доступен, но это всё равно «тестовый брокер» | настоящий образ брокера той же версии, что в продакшене |
| Расхождение конфигурации | порт и свойство `spring.embedded.kafka.brokers` | `@ServiceConnection` задаёт `spring.kafka.bootstrap-servers` |
| Изоляция | делит кучу и потоки JVM с тестируемым приложением | отдельный процесс; шум логов остаётся снаружи |
| Хорошо подходит для | быстрых тестов слушателя без Docker, CI без сокета Docker | всего остального, особенно транзакций, compaction, rebalance'ов consumer group |

- В обоих случаях задавай `spring.kafka.consumer.auto-offset-reset=earliest` и используй **уникальный group id для каждого тестового класса** (`${random.uuid}` или `@DynamicPropertySource`), иначе закоммиченные offset'ы предыдущего класса пропустят твою запись.
- Дожидайся назначения партиций до отправки: `ContainerTestUtils.waitForAssignment(container, partitions)` на `MessageListenerContainer` из `KafkaListenerEndpointRegistry` или просто `await()` на результат; запись, отправленная до назначения, всё равно будет потреблена при `earliest`, поэтому второго подхода обычно достаточно.

### Тестирование consumer'а
- Через настоящий слушатель (предпочтительно): отправь через `KafkaTemplate`, дождись побочного эффекта. Проверяет десериализатор, обработчик ошибок, retry и путь в DLQ ([[spring-kafka-consumer]], [[dead-letter-queues-and-retries]]).
- Модульный уровень: вызови метод `@KafkaListener` напрямую с `ConsumerRecord` и замоканными зависимостями; быстро, но ничего не говорит о конфигурации контейнера.
- Пути ошибок: опубликуй некорректный payload и проверь, что он попал в топик `.DLT`, прочитав его в тесте через `KafkaTestUtils.getSingleRecord(consumer, "topic.DLT")`. Количество retry проверяй через `@MockitoSpyBean` на обработчике или `AtomicInteger` в тестовом `RecordInterceptor`.
- Идемпотентность: отправь одну и ту же запись дважды (тот же ключ и id события) и проверь через `during(2 s)`, что существует только одна строка или один исходящий вызов ([[idempotency]]).

### Тестирование producer'а и outbox
- Consumer в тесте: создай `KafkaConsumer` с `KafkaTestUtils.consumerProps(bootstrap, "test-group", "true")`, подпишись, вызови `KafkaTestUtils.getRecords(consumer, Duration.ofSeconds(5))`, проверь ключ, заголовки и payload.
- Outbox-ретранслятор ([[outbox-pattern]]): вставь бизнес-строку через сервис, затем вызови `relay.relay()` напрямую (без ожидания `@Scheduled`), затем проверь запись в топике и `published_at IS NOT NULL`. Тестирование ретранслятора как обычного метода полностью снимает вопрос тайминга.
- Транзакции: тестовый метод с `@Transactional` откатывается, поэтому поток слушателя никогда не увидит строку, а ретранслятор её не найдёт. Делай такие тесты нетранзакционными и убирай данные в `@AfterEach`.

### Управление временем
- Внедряй `java.time.Clock` в каждый компонент, который читает время; в тестах используй `Clock.fixed(...)` или изменяемые тестовые часы с `advance(Duration)`. Так «токен истекает через 15 минут» превращается в синхронную проверку.
- Методы `@Scheduled`: не жди cron. Вызывай метод бина напрямую, а в `@SpringBootTest` отключай планирование через профиль (`@ConditionalOnProperty` на конфигурации с `@EnableScheduling` или собственное свойство `spring.task.scheduling.enabled=false`), чтобы настоящий планировщик не соревновался с тестом.
- `@Async`: в интеграционных тестах замени исполнитель на `SyncTaskExecutor` через `@TestConfiguration`, чтобы метод выполнялся в том же потоке; в модульных тестах вызывай метод напрямую. Оставь один тест с настоящим исполнителем, чтобы ловить баги вида «прокси `@Async` обходится вызовом самого себя» ([[spring-aop]]).
- Workflow в Temporal: `TestWorkflowEnvironment` пропускает время, поэтому workflow с 24-часовым таймером завершается за миллисекунды; activities мокаются через Mockito ([[temporal-concepts]]).
- Реактивный код и код на `CompletableFuture`: `future.get(5, SECONDS)` или `StepVerifier.withVirtualTime` ([[completable-future]], [[spring-reactive-webclient-sse]]).

## Пример
Тест consumer'а с тестовыми часами, сбоем на первой попытке, который повторяется через retry, и проверкой идемпотентности через `during`.
```java
@SpringBootTest
@Import(TestcontainersConfig.class)
@TestPropertySource(properties = {
        "spring.kafka.consumer.auto-offset-reset=earliest",
        "spring.kafka.consumer.group-id=activation-it-${random.uuid}"})
class ProfileDownloadedListenerIT {

    @TestConfiguration
    static class ClockConfig {
        @Bean @Primary MutableClock clock() { return new MutableClock(Instant.parse("2026-10-27T09:00:00Z")); }
    }

    @Autowired KafkaTemplate<String, String> kafka;
    @Autowired ActivationRepository activations;
    @Autowired MutableClock clock;
    @MockitoBean HlrClient hlr;

    @Test
    void retriesHlrAndActivatesOnce() {
        when(hlr.activate("996555000001"))
                .thenThrow(new HlrTimeoutException())        // первая доставка падает -> контейнер делает retry
                .thenReturn(HlrResult.OK);
        String event = """{"eventId":"e-1","orderId":"o-1","msisdn":"996555000001"}""";

        kafka.send("esim.profile-downloaded", "o-1", event);
        kafka.send("esim.profile-downloaded", "o-1", event);  // повторная доставка

        await().atMost(Duration.ofSeconds(15)).untilAsserted(() -> {
            Activation a = activations.findByOrderId("o-1").orElseThrow();
            assertThat(a.status()).isEqualTo(ActivationStatus.ACTIVE);
            assertThat(a.activatedAt()).isEqualTo(clock.instant());   // время взято из внедрённого Clock
        });
        await().during(Duration.ofSeconds(2)).atMost(Duration.ofSeconds(3))
               .until(() -> activations.countByOrderId("o-1") == 1);  // дубликат не создал вторую строку
        verify(hlr, times(2)).activate("996555000001");               // один сбой + один успех
    }
}
```
`MutableClock` — подкласс `Clock` из десяти строк с `advance(Duration)`; проверки `activatedAt` становятся точными, а не «в пределах последних нескольких секунд».

## Подводные камни
- Проверка после `kafka.send(...)` без `await`: проходит на быстром ноутбуке, падает в CI. Кроме того, `send()` асинхронный; `send(...).get()` гарантирует только подтверждение брокера, но не потребление.
- Повторное использование consumer group в разных тестовых классах: offset'ы, закоммиченные классом A, заставляют слушатель класса B начать после его записи. Случайные group id или `earliest` с новым топиком для каждого класса.
- `@DirtiesContext`, чтобы «починить» утечки между тестами: он пересобирает контекст и перезапускает контейнеры, превращая набор тестов на 10 с в минуты. Исправь утечку (уникальные топики, очистка, сброс моков).
- `@Transactional` на асинхронных тестах, `@EmbeddedKafka` и Kafka из Testcontainers в одном контексте (два `bootstrap-servers`) или `Thread.sleep` «на всякий случай» перед первым await.
- Тесты, зависящие от реального времени (`Instant.now()` в продакшен-коде): нестабильны около полуночи, а логику истечения сроков невозможно протестировать; внедряй `Clock`.
- Связанные заметки: [[contract-testing-and-wiremock]] — синхронная HTTP-сторона тех же процессов, [[exactly-once-myth]] — почему именно тест на повторную доставку выше важнее всего.
