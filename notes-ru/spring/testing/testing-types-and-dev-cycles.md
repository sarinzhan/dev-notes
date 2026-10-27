---
title: Виды тестов, тестовые дублёры и циклы разработки
tags: [testing, junit, mockito, spring-boot-test, tdd]
learned: 2026-10-22
---
## Кратко
**Тестирование** проверяет, что реальное поведение программы совпадает с ожидаемым. Пирамида: много быстрых **модульных (unit)** тестов одного класса, меньше **интеграционных** тестов совместной работы компонентов (контекст Spring, настоящие Postgres/Kafka в Testcontainers), несколько **сквозных (end-to-end)** тестов всей системы, плюс тестирование производительности и безопасности. **Тестовые дублёры (test doubles)** — dummy, fake, stub, mock, spy — подменяют зависимости. Цикл разработки определяет, *когда* пишутся тесты: классический git-flow (часто после кода), trunk-based development (тесты с каждым небольшим коммитом, фичи за флагами), TDD (сначала тесты).

## Основное
### Виды тестов
| Вид | Что проверяет | Инструменты в Spring-проекте | Скорость |
|---|---|---|---|
| Модульный | один класс/метод в изоляции, зависимости замоканы | JUnit 5, Mockito, AssertJ; без контекста Spring | мс |
| Интеграционный | взаимодействие компонентов: сервис + репозиторий + БД, Kafka listener, HTTP-клиент | `@SpringBootTest`, срезы (`@WebMvcTest`, `@DataJpaTest`, `@RestClientTest`), Testcontainers, WireMock | с |
| Сквозной | развёрнутую систему через её публичный интерфейс | REST Assured / Playwright против stage-окружения | мин |
| Производительности | задержку и пропускную способность под нагрузкой | k6, Gatling, JMeter | мин–ч |
| Безопасности | уязвимости, правила аутентификации | OWASP ZAP, dependency-check, тесты с `@WithMockUser` | по-разному |

- Срезы Boot (slices): `@WebMvcTest(Controller.class)` загружает только MVC + контроллер и даёт `MockMvc` (`MockMvcTester` в Boot 3.4 в стиле AssertJ); `@DataJpaTest` загружает JPA со встроенной БД или БД из Testcontainers (`@AutoConfigureTestDatabase(replace = NONE)`, чтобы оставить Postgres); `@RestClientTest` — для HTTP-клиентов ([[rest-clients]]). `@SpringBootTest(webEnvironment = RANDOM_PORT)` с `TestRestTemplate`/`WebTestClient` — интеграционный тест с полным контекстом.
- Заменить бин в контексте можно через `@MockitoBean` / `@MockitoSpyBean` (Boot 3.4+, Spring Framework 6.2; `@MockBean` устарел). Каждый отдельный набор замоканных бинов создаёт новый контекст, что замедляет набор тестов; используй общие конфигурации.
- Testcontainers + `@ServiceConnection` (Boot 3.1+) запускает Postgres/Kafka/Redis в Docker и автоматически подставляет свойства подключения; это стандарт для тестов репозиториев и [[spring-kafka-consumer]]. Переиспользуй контейнеры между тестами (`.withReuse(true)` или статический синглтон), чтобы набор тестов оставался быстрым.
- Основы JUnit 5: `@Test`, `@ParameterizedTest` с `@CsvSource`/`@MethodSource`, `@Nested` для группировки, `@BeforeEach`/`@BeforeAll`, `assertThrows`; AssertJ — для fluent-проверок (`assertThat(x).isEqualTo(...)`).

### Тестовые дублёры: какой когда
| Тебе нужно | Используй | Mockito |
|---|---|---|
| передать заглушку-заполнитель, которую логика никогда не трогает | **Dummy** | `null` или неиспользуемый `mock()` |
| протестировать простую бизнес-логику на работающей облегчённой реализации | **Fake** (репозиторий в памяти, H2) | класс, написанный вручную |
| заставить метод вернуть заранее заданное значение | **Stub** | `when(repo.findById(1)).thenReturn(Optional.of(x))` |
| проверить, что метод был вызван, сколько раз и с какими аргументами | **Mock** | `verify(sender, times(1)).send(eq("996..."), any())` |
| заменить часть поведения, сохранив реальный объект | **Spy** | `spy(realObject)` + `doReturn(...).when(spy).method()` |

Предпочитай проверку состояния (проверку результата) проверке взаимодействий (проверке вызовов); избыточное мокирование привязывает тесты к деталям реализации, и они ломаются при каждом рефакторинге.

### Циклы разработки
- **Классический (git-flow)**: написать код → (тесты, которые часто пропускают или пишут позже) → commit в feature-ветку → CI → код-ревью → merge → QA на stage-сборке → релиз → проверка релиза → продакшен. Долгоживущие ветки, большие слияния, медленная обратная связь.
- **Trunk-based development (TBD)**: написать код **и тесты** → небольшой commit в `main` (или ветку, живущую часы, а не недели) → CI → ревью → deploy в **продакшен с выключенным feature flag** → QA на проде или stage → **флаг включается**. Требует хороших автоматических тестов и переключателей функциональности (feature toggles); так выглядит непрерывная поставка (continuous delivery).
- **TDD (разработка через тестирование, test-driven development)**: red → green → refactor: написать падающий тест, написать минимум кода, чтобы он прошёл, навести порядок, повторить. Даёт тестируемый дизайн и набор регрессионных тестов по построению; лучше всего работает для чистой логики (расчёт цен, машины состояний, парсеры) и хуже — для исследовательской работы с UI.
- Связанные практики: BDD (Given/When/Then, Cucumber), контрактное тестирование между сервисами (Spring Cloud Contract, Pact), мутационное тестирование (PIT), чтобы проверить, что тесты действительно падают, когда код ломается.

## Пример
Модульный тест сервиса со stub и mock, без контекста Spring:
```java
@ExtendWith(MockitoExtension.class)
class OtpServiceTest {

    @Mock OtpRepository otps;
    @Mock SmsSender sms;
    @InjectMocks OtpService service;

    @Test
    void sendsCodeAndStoresHash() {
        when(otps.countRecent("996555000001", Duration.ofMinutes(10))).thenReturn(0L);   // stub

        service.request("996555000001");

        verify(sms).send(eq("996555000001"), matches("Your code: \\d{6}"));              // mock
        var captor = ArgumentCaptor.forClass(OtpEntry.class);
        verify(otps).save(captor.capture());
        assertThat(captor.getValue().codeHash()).isNotBlank();
    }

    @Test
    void refusesWhenRateLimited() {
        when(otps.countRecent(any(), any())).thenReturn(5L);
        assertThatThrownBy(() -> service.request("996555000001"))
                .isInstanceOf(TooManyOtpRequests.class);
        verifyNoInteractions(sms);
    }
}
```
Интеграционный тест с Testcontainers:
```java
@SpringBootTest
@Testcontainers
class OtpRepositoryIT {
    @Container @ServiceConnection
    static PostgreSQLContainer<?> pg = new PostgreSQLContainer<>("postgres:16");

    @Autowired OtpRepository otps;

    @Test
    void countsOnlyRecent() { /* вставить строки, проверить countRecent */ }
}
```

## Подводные камни
- `@SpringBootTest` для всего подряд: 10-секундный запуск контекста на каждый тестовый класс; используй срезы и обычные модульные тесты и держи конфигурации контекста одинаковыми, чтобы срабатывал кэш контекстов Spring.
- Тесты, зависящие от порядка или общего изменяемого состояния (статические поля, переиспользуемая БД без очистки), проходят поодиночке и падают в CI; используй откат через `@Transactional` на тестах или очищай таблицы и избегай `@DirtiesContext`.
- Мокирование того, что тебе не принадлежит (JPA-`EntityManager`, `RestClient`), ничего реального не проверяет; вместо этого мокай собственный интерфейс шлюза, а сам шлюз покрывай интеграционными тестами.
- `@Transactional` на тесте скрывает проблемы ленивой загрузки и проблемы, возникающие при commit (flush так и не происходит); добавь `TestEntityManager.flush()` или запускай ключевые тесты без тестовой транзакции ([[jpa-persistence-context-and-entity-lifecycle]]).
- Время и случайность: внедряй `Clock` и `Random`/`SecureRandom`, чтобы тесты были детерминированными.
- Связанные заметки: [[rest-clients]] — матрица тестирования клиентов, [[spring-transactional]], [[docker-fundamentals]] (Testcontainers нужен сокет Docker, про CI см. [[docker-socket-dind-dood]]).
