---
title: Контрактное тестирование и WireMock
tags: [testing, wiremock, contract-testing, pact, spring-boot-test]
learned: 2026-10-27
---
## Кратко
**WireMock** — это HTTP-сервер, который запускается внутри теста и подменяет внешний API (SM-DP+, HLR, платёжный шлюз): ты объявляешь заглушки «запрос → ответ» (stubs), направляешь клиента на `wiremock.baseUrl()` и тестируешь таймауты, 5xx, некорректные тела ответов и retry, которые никогда не удалось бы вызвать на настоящей песочнице (sandbox). **Контрактное тестирование (contract testing)** отвечает на другой вопрос: соответствует ли заглушка тому, что поставщик (provider) возвращает на самом деле, и возвращает ли поставщик по-прежнему то, на что полагаются его потребители (consumers)? В **контрактах, управляемых потребителем (consumer-driven contracts)** (Pact, Spring Cloud Contract) ожидания пишет потребитель, они записываются в файл контракта, а CI поставщика воспроизводит этот контракт против настоящей реализации. WireMock проверяет *твой* код относительно *твоих предположений*; контракты проверяют сами предположения. Модульное мокирование интерфейса клиента описано в [[junit-and-mockito]]; инфраструктура в контейнерах — в [[testcontainers]].

## Основное
### Основы WireMock
- Зависимость `org.wiremock:wiremock-standalone` (3.x) плюс `org.wiremock.integrations:wiremock-spring-boot` для `@EnableWireMock` / `@InjectWireMock` или обычное расширение JUnit 5 `@RegisterExtension static WireMockExtension wm = WireMockExtension.newInstance().options(wireMockConfig().dynamicPort()).build()`.
- DSL заглушек: `wm.stubFor(post(urlPathEqualTo("/es2plus/downloadOrder")).withHeader("Content-Type", containing("json")).withRequestBody(matchingJsonPath("$.iccid")).willReturn(okJson("...")))`. Матчеры охватывают URL, заголовки, query, cookie, тело в JSON/XML, multipart.
- Внедрение сбоев: `.willReturn(aResponse().withFixedDelay(5000))` для таймаутов, `withStatus(503)`, `withFault(Fault.CONNECTION_RESET_BY_PEER)`, `Fault.MALFORMED_RESPONSE_CHUNK`. Так детерминированно тестируются [[circuit-breaker]] и политики retry.
- **Сценарии (scenarios)** — это машины состояний: первый вызов возвращает 503 в состоянии `Started` и переходит в `Recovered`, второй возвращает 200. Нужны для тестов вида «retry проходит со второй попытки».
- Проверка: `wm.verify(1, postRequestedFor(urlPathEqualTo("/...")).withHeader("Idempotency-Key", matching(".+")))`; `wm.findAll(...)` возвращает записанные запросы для детальных проверок.
- Файлы заглушек: JSON-маппинги в `src/test/resources/mappings/` и тела в `__files/` загружаются автоматически; удобно для больших ответов поставщика, один раз записанных в режиме прокси record/playback.
- Подключение к Spring: `@DynamicPropertySource`, который выставляет `smdp.base-url` в URL WireMock, или `@EnableWireMock(@ConfigureWireMock(name = "smdp", property = "smdp.base-url"))`, который делает то же самое декларативно. Сама конфигурация клиента описана в [[rest-clients]]; `@RestClientTest` с `MockRestServiceServer` — альтернатива внутри процесса, когда тестируется только класс клиента.

### Чего WireMock сказать не может
- Заглушка — это твоё представление о поставщике. Если поставщик переименует `eid` в `EID`, добавит обязательный заголовок или изменит код статуса, все тесты на WireMock останутся зелёными, а продакшен сломается. Варианты: периодически записывать реальные ответы (режим записи против песочницы), проверять заглушки по OpenAPI-спецификации поставщика ([[openapi-swagger]] + расширение WireMock для валидации по OpenAPI) или использовать контракт.

### Контрактное тестирование
| | Управляемое потребителем (Pact) | Управляемое поставщиком (Spring Cloud Contract) |
|---|---|---|
| Кто пишет контракт | потребитель, в тесте потребителя | поставщик, в виде DSL на Groovy/YAML рядом с кодом |
| Артефакт | pact JSON в Pact Broker | JAR с заглушками, опубликованный в Maven/Artifactory |
| Сторона поставщика | CI поставщика забирает pact'ы и воспроизводит их против запущенного сервиса с состояниями поставщика (provider states) | тесты генерируются из DSL и запускаются против поставщика |
| Сторона потребителя | тест потребителя работает против mock-сервера Pact | потребитель запускает `StubRunner` с опубликованными заглушками (внутри WireMock) |
| Лучше всего для | множества потребителей, чьи потребности поставщик не должен сломать (`can-i-deploy`) | одной команды, владеющей API и желающей, чтобы потребители использовали её заглушки |

- Контракты описывают *форму и смысл* взаимодействия (какие поля есть, типы, статус для данного состояния), а не бизнес-данные. Используй матчеры (`like`, `regex`, `eachLike`), а не буквальные значения, чтобы поставщик мог удовлетворить их любыми корректными данными.
- Состояния поставщика («профиль 8999…01 существует и находится в статусе RELEASED») позволяют поставщику подготовить данные перед воспроизведением; именно они делают воспроизведение реалистичным.
- Оба инструмента работают и с обменом сообщениями: у Pact есть message pacts, Spring Cloud Contract поддерживает Kafka/AMQP — здесь [[message-schema-evolution]] встречается с тестированием.
- Где это окупается: внутренние сервисы разных команд в одной компании, публичный API с потребителями через SDK, распиливаемый монолит ([[strangler-fig-migration]], [[monolith-vs-microservices]]). Где нет: сторонний поставщик, который никогда не будет запускать твои pact'ы; там остаётся откат к записанным заглушкам WireMock плюс ночной smoke-тест против песочницы.

### Как сочетаются уровни
| Уровень | Инструмент | Что ловит |
|---|---|---|
| Модульный | Mockito-мок интерфейса `SmDpClient` | логику сервиса |
| Интеграция клиента | WireMock / `MockRestServiceServer` | сериализацию, заголовки, таймауты, retry, отображение ошибок |
| Контрактный | Pact / Spring Cloud Contract | расхождение между ожиданиями потребителя и реальностью поставщика |
| Сквозной / smoke | настоящая песочница в пайплайне по расписанию | всё остальное, медленно и нестабильно |

## Пример
Тест клиента с WireMock: SM-DP+ один раз возвращает 503, клиент повторяет запрос и получает успех, а заголовок идемпотентности отправляется в обеих попытках.
```java
@SpringBootTest(classes = {SmDpClientConfig.class, SmDpClient.class})
@EnableWireMock(@ConfigureWireMock(name = "smdp", property = "smdp.base-url"))
class SmDpClientWireMockTest {

    @InjectWireMock("smdp") WireMockServer smdp;
    @Autowired SmDpClient client;

    @Test
    void retriesOnceOn503AndSendsIdempotencyKey() {
        smdp.stubFor(post(urlPathEqualTo("/gsma/rsp2/es2plus/downloadOrder"))
                .inScenario("flaky")
                .whenScenarioStateIs(Scenario.STARTED)
                .willReturn(aResponse().withStatus(503))
                .willSetStateTo("recovered"));
        smdp.stubFor(post(urlPathEqualTo("/gsma/rsp2/es2plus/downloadOrder"))
                .inScenario("flaky")
                .whenScenarioStateIs("recovered")
                .willReturn(okJson("""
                        {"header":{"functionExecutionStatus":{"status":"Executed-Success"}},
                         "iccid":"8999700000000000001","matchingId":"ABC-123"}""")));

        DownloadOrder order = client.downloadOrder(new DownloadOrderRequest("8999700000000000001", "89049032..."));

        assertThat(order.matchingId()).isEqualTo("ABC-123");
        smdp.verify(2, postRequestedFor(urlPathEqualTo("/gsma/rsp2/es2plus/downloadOrder"))
                .withHeader("Idempotency-Key", equalTo("8999700000000000001"))
                .withRequestBody(matchingJsonPath("$.iccid", equalTo("8999700000000000001"))));
    }

    @Test
    void mapsTimeoutToRetryableException() {
        smdp.stubFor(post(urlPathEqualTo("/gsma/rsp2/es2plus/downloadOrder"))
                .willReturn(aResponse().withFixedDelay(3_000).withStatus(200)));   // таймаут клиента — 1 с

        assertThatThrownBy(() -> client.downloadOrder(new DownloadOrderRequest("8999...", "8904...")))
                .isInstanceOf(SmDpUnavailableException.class);
    }
}
```
Pact-тест потребителя для того же вызова описал бы запрос и ответ с матчерами `like("ABC-123")`; затем CI команды SM-DP+ воспроизвёл бы его, а `pact-broker can-i-deploy` пропускал бы или блокировал deploy.

## Подводные камни
- Заглушки с `anyUrl()` и заготовленным 200: тест проходит, что бы клиент ни отправил. Сопоставляй путь и важные поля тела и проверяй запрос.
- Общее состояние WireMock между тестами: нужен `smdp.resetAll()` в `@BeforeEach` (интеграция со Spring Boot делает это по умолчанию), иначе состояние сценария из предыдущего теста просачивается дальше.
- Фиксированные порты (`8089`) конфликтуют при параллельных сборках; используй `dynamicPort()` и внедряй URL.
- Мокирование HTTP-слоя и клиента одновременно: либо `@MockitoBean SmDpClient` (модульный уровень), либо WireMock (интеграционный уровень), но не оба в одном тесте.
- Контракты с буквальными значениями и полными телами: любое постороннее изменение у поставщика их ломает, и команды начинают игнорировать падения. Сопоставляй только то, что потребитель читает.
- Связанные заметки: [[testing-async-and-kafka-flows]] — асинхронный аналог этих тестов, [[circuit-breaker]] — поведение отказоустойчивости, которое должны проверять сбои WireMock.
