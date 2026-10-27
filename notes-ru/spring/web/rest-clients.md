---
title: Вызов внешних API: RestTemplate, RestClient, WebClient, Feign и их тестирование
tags: [rest-client, http, resttemplate, webclient, wiremock, testing]
learned: 2026-10-22
---
## Кратко
В Spring есть три HTTP-клиента: **`RestTemplate`** (классический, синхронный, API в стиле шаблонного метода, в режиме поддержки), **`RestClient`** (Spring 6.1 / Boot 3.2, синхронный с fluent API, рекомендуемый вариант по умолчанию) и **`WebClient`** (реактивный, неблокирующий, из WebFlux). Поверх любого из них **HTTP interface clients** (`@HttpExchange`, Spring 6) или **OpenFeign** дают декларативные интерфейсы. Тестирование: `MockRestServiceServer` для unit-тестов `RestTemplate`/`RestClient`, **WireMock** — реалистичный фейковый сервер для любого клиента, Mockito — для чистых unit-тестов кода вокруг клиента.

## Основное
### Клиенты
| Клиент | Модель | Статус | Заметки |
|---|---|---|---|
| `RestTemplate` | синхронный, блокирующий | в поддержке с 5.0, не deprecated | `getForObject`, `exchange(...)`; настраивается через `RestTemplateBuilder`; много перегрузок, трудно читать |
| `RestClient` | синхронный, блокирующий, fluent | Spring 6.1+, рекомендуется | те же `HttpMessageConverter` и `ClientHttpRequestFactory`, что у `RestTemplate`; `RestClient.create(restTemplate)` переиспользует конфигурацию; `.retrieve().body(T)` / `.toEntity(T)` |
| `WebClient` | реактивный, неблокирующий | WebFlux | возвращает `Mono`/`Flux`; `.block()` делает его синхронным, но теряется весь смысл; см. [[spring-reactive-webclient-sse]] |
| HTTP interface (`@HttpExchange`) | декларативный поверх любого из перечисленных | Spring 6.0+ | `HttpServiceProxyFactory` строит прокси; Boot 3.4 умеет регистрировать их автоматически |
| OpenFeign | декларативный | Spring Cloud | `@FeignClient`, интегрируется с балансировкой нагрузки и circuit breaker; дополнительная зависимость |

- Под капотом `ClientHttpRequestFactory` выбирает транспорт: `JdkClientHttpRequestFactory` (`HttpClient` из Java 11, поддерживает HTTP/2), Apache HttpClient 5 (пул соединений, обычный выбор для production), `SimpleClientHttpRequestFactory` (`HttpURLConnection`, без пула, лучше избегать). `RestClient.Builder` из Boot выбирает лучший из доступных на classpath.
- Всегда задавай **таймауты подключения и чтения** (`ClientHttpRequestFactorySettings` в Boot 3.x или `HttpComponentsClientHttpRequestFactory`). По умолчанию они бесконечные или очень длинные; тогда зависший downstream исчерпает потоки Tomcat.
- **Пул соединений (connection pooling)**: в HTTP/1.1 каждому выполняющемуся запросу нужно своё TCP-соединение; keep-alive позволяет пулу их переиспользовать (`PoolingHttpClientConnectionManager`, `setMaxTotal`, `setDefaultMaxPerRoute`, по умолчанию на маршрут всего 5). HTTP/2 мультиплексирует запросы в одном соединении. Серверный аналог описан в [[spring-boot-properties-and-profiles]].
- **Сжатие**: отправляй `Accept-Encoding: gzip`; клиенты Apache и JDK распаковывают прозрачно.
- Обработка ошибок: `retrieve()` бросает `HttpClientErrorException` / `HttpServerErrorException` (`RestClientResponseException`) на 4xx/5xx; поведение настраивается через `.onStatus(...)`. Для отказоустойчивости оборачивай вызовы в Resilience4j (`@Retry`, `@CircuitBreaker`, `@TimeLimiter`) и помни, что HTTP-вызов внутри транзакции БД удерживает соединение ([[spring-transactional]]).
- Блокирующие клиенты на Java 21 хорошо масштабируются с виртуальными потоками (`spring.threads.virtual.enabled=true`, [[virtual-threads]]); WebClient не нужен только ради пропускной способности.
- Наблюдаемость (observability): `RestClient.Builder` и `RestTemplateBuilder` из Boot инструментированы Micrometer (метрики `http.client.requests`, проброс трейсов), если использовать внедрённый builder, а не `new`.

### Тестирование внешних клиентов
| Подход | Уровень | Работает с | Комментарии |
|---|---|---|---|
| Mockito | unit | любой клиент (мокается собственный класс-шлюз) | самый простой; не проверяет сериализацию, URL и обработку статусов |
| `MockRestServiceServer` | unit / slice | `RestTemplate`, `RestClient` | встроен в Spring Test; привязывается к клиенту, без сервера, быстро; `expect(requestTo(...)).andRespond(withSuccess(...))`; slice `@RestClientTest` подключает его; **не** для WebClient и Feign |
| WireMock | интеграционный | любой HTTP-клиент | отдельный или встроенный stub-сервер; проверяет запросы, добавляет задержки, возвращает сбои, записывает реальный трафик; немного тяжелее, нужен порт; `wiremock-spring-boot` или `@AutoConfigureWireMock` |
| `WebTestClient` | интеграционный | WebFlux / любой сервер | тестирует *твой* сервер, а не исходящий клиент |
| `@MockBean`/`@MockitoBean` на Feign-клиенте | unit / интеграционный | Feign | базовые заглушки интерфейса |
| Spring Cloud Contract | интеграционный | Feign, RestTemplate | контракты, управляемые потребителем (consumer-driven contracts), stub runner |
| MockServer + Testcontainers | интеграционный | любой | полная симуляция HTTP в Docker; ближе всего к prod |

Практическое правило: Mockito вокруг шлюза — для тестов бизнес-логики, `MockRestServiceServer` — для собственной логики маппинга в шлюзе, WireMock — для интеграционного теста всего среза, включая таймауты и статусы ошибок.

## Пример
```java
@Component
public class VendorEsimGateway {
    private final RestClient client;

    VendorEsimGateway(RestClient.Builder builder, EsimVendorProps props) {
        this.client = builder.baseUrl(props.baseUrl())
                .defaultHeader(HttpHeaders.AUTHORIZATION, "Bearer " + props.apiKey())
                .requestFactory(ClientHttpRequestFactories.get(ClientHttpRequestFactorySettings.DEFAULTS
                        .withConnectTimeout(Duration.ofSeconds(2))
                        .withReadTimeout(props.timeout())))
                .build();
    }

    public ProfileStatus status(String iccid) {
        return client.get().uri("/profiles/{iccid}", iccid)
                .retrieve()
                .onStatus(HttpStatusCode::is4xxClientError,
                          (req, res) -> { throw new VendorProfileNotFound(iccid); })
                .body(ProfileStatus.class);
    }
}

@RestClientTest(VendorEsimGateway.class)
@EnableConfigurationProperties(EsimVendorProps.class)
class VendorEsimGatewayTest {
    @Autowired MockRestServiceServer server;
    @Autowired VendorEsimGateway gateway;

    @Test
    void mapsStatus() {
        server.expect(requestTo("https://sandbox.vendor.example/profiles/8999600000000000001"))
              .andExpect(method(HttpMethod.GET))
              .andRespond(withSuccess("""{"state":"RELEASED"}""", MediaType.APPLICATION_JSON));
        assertThat(gateway.status("8999600000000000001").state()).isEqualTo("RELEASED");
        server.verify();
    }
}
```

## Подводные камни
- `new RestTemplate()` / `RestClient.create()` в поле: нет таймаутов, нет пула, нет метрик. Внедряй builder из Boot и создавай по одному бину на каждый downstream.
- Повторное чтение тела (для логирования и для парсинга) падает, потому что поток уже прочитан; используй `BufferingClientHttpRequestFactory` или интерцептор с буферизацией.
- Retry неидемпотентного POST без ключа идемпотентности дублирует заказы ([[idempotency]]).
- `@RestClientTest` загружает только указанный компонент; если шлюзу нужны `@ConfigurationProperties` или настройки Jackson, импортируй их явно.
- Слишком общие заглушки WireMock (`anyUrl()`) проходят тесты, даже когда реальный URL неверен; проверяй путь и тело.
- Связанные заметки: [[rest-and-http]], [[spring-reactive-webclient-sse]], [[testing-types-and-dev-cycles]], [[completable-future]] — для параллельного веерного вызова (fan-out) нескольких сервисов.
