---
title: Основы JUnit 5 и Mockito
tags: [testing, junit, mockito, assertj]
learned: 2026-10-27
---
## Кратко
**JUnit 5** (Jupiter) запускает тест: хуки жизненного цикла, `@ParameterizedTest`, `@Nested`, `assertThrows`. **Mockito** подменяет зависимости: `@Mock` — для заглушек и проверки взаимодействий, `@Spy` — для частично реальных объектов, `@InjectMocks` — чтобы собрать тестируемый класс, `ArgumentCaptor` — чтобы посмотреть, что было передано. **AssertJ** даёт читаемые fluent-проверки. Мокай только собственные границы (репозиторий, шлюз, отправитель); никогда не мокай объекты-значения (value objects) и сам тестируемый класс. Эта заметка — про инструменты; вопрос «какой тест когда» разобран в [[testing-types-and-dev-cycles]].

## Основное
### Жизненный цикл и аннотации JUnit 5
- Жизненный цикл по умолчанию — `PER_METHOD`: новый экземпляр тестового класса на каждый тест, поэтому поля экземпляра изолированы. `@TestInstance(PER_CLASS)` использует один общий экземпляр и разрешает нестатический `@BeforeAll`.
- Порядок хуков: `@BeforeAll` (статический) → `@BeforeEach` → `@Test` → `@AfterEach` → `@AfterAll`. Порядок тестовых методов детерминирован, но намеренно неочевиден; `@TestMethodOrder` существует, но тесты, зависящие от порядка, — дурной знак.
- `@DisplayName`, `@Disabled("reason")`, `@Tag("slow")` (фильтруется в Maven Surefire через `groups`), `@Timeout`, `@EnabledOnOs`, `@EnabledIfEnvironmentVariable`.
- Источники для `@ParameterizedTest`: `@ValueSource(strings = ...)`, `@CsvSource({"996555, KG", "7999, RU"})`, `@CsvFileSource`, `@EnumSource(Status.class, names = "ACTIVE")`, `@MethodSource("cases")`, возвращающий `Stream<Arguments>`, `@NullSource`/`@EmptySource`. Используй `name = "{0} -> {1}"` для читаемых отчётов.
- Вложенные классы `@Nested` группируют сценарии («когда профиль зарезервирован», «когда SM-DP+ не отвечает») и используют общий внешний `@BeforeEach`.
- `assertThrows(Type.class, () -> ...)` возвращает исключение для дальнейших проверок; `assertAll(...)` сообщает обо всех упавших проверках, а не останавливается на первой.
- Расширения (extensions) заменяют runners/rules из JUnit 4: `@ExtendWith(MockitoExtension.class)`, `SpringExtension` (уже внутри `@SpringBootTest`), `@RegisterExtension` для программных расширений.

### AssertJ
- `assertThat(list).hasSize(2).extracting(Order::status).containsExactly(NEW, PAID)`; `assertThatThrownBy(...).isInstanceOf(...).hasMessageContaining(...)`; `usingRecursiveComparison()` — для сравнения DTO без `equals`. Входит в `spring-boot-starter-test`.

### Mockito
- `@ExtendWith(MockitoExtension.class)` инициализирует `@Mock`, `@Spy`, `@Captor` и `@InjectMocks` (сначала внедрение через конструктор, затем через сеттер, затем через поле; недостающая зависимость молча остаётся `null`, поэтому предпочитай конструктор и проверяй его).
- Заглушки: `when(repo.findById(id)).thenReturn(Optional.of(x))` — читаемая форма. Для spy используй `doReturn(x).when(spy).method()` (форма `when(...)` сначала вызывает реальный метод), а для `void`-методов, которые нельзя поместить внутрь `when(...)`, — `doThrow`/`doNothing`.
- Последовательные ответы: `thenReturn(a, b)` или `thenThrow(new TimeoutException()).thenReturn(ok)`, чтобы имитировать retry.
- `thenAnswer(inv -> inv.getArgument(0))` — когда результат зависит от входных данных, например `save`, который возвращает свой аргумент.
- Проверка: `verify(sender).send(...)` (= `times(1)`), `times(n)`, `never()`, `atLeastOnce()`, `verifyNoMoreInteractions(sender)`, `verifyNoInteractions(sms)`. `InOrder io = inOrder(a, b); io.verify(a).reserve(); io.verify(b).activate();` — для проверки порядка между моками.
- `ArgumentCaptor` записывает фактический аргумент, чтобы можно было проверить его поля; объявляется через `@Captor` или `ArgumentCaptor.forClass`.
- Правило матчеров «всё или ничего»: если один аргумент использует матчер (`any()`, `eq()`, `argThat()`), матчеры должны использовать все аргументы этого вызова. `send("996...", any())` бросает `InvalidUseOfMatchersException`; пиши `send(eq("996..."), any())`.
- `any()` не совпадает с `null`; совпадают `isNull()` или `nullable(Type.class)`. `anyString()` совпадает только с непустым (не null) `String`.
- Статические методы: `try (MockedStatic<Instant> s = mockStatic(Instant.class)) { s.when(Instant::now).thenReturn(fixed); ... }`. Мок привязан к потоку, и его обязательно нужно закрыть. Лучший дизайн — внедрять `Clock`.
- Final-классы и методы: начиная с Mockito 5 по умолчанию используется inline mock maker, поэтому `final`-классы, record'ы в роли зависимостей и даже конструкторы (`mockConstruction`) можно мокать без дополнительной настройки. То, что ты *можешь*, не значит, что *должен*.
- Строгие заглушки: `MockitoExtension` работает со `Strictness.STRICT_STUBS`. Неиспользованный `when(...)` роняет тест с `UnnecessaryStubbingException`; заглушка, вызванная с другими аргументами, — с `PotentialStubbingProblem`. Исправляй тест, а не строгость; `lenient()` — только для общих заглушек в `@BeforeEach`.
- Что не мокать: объекты-значения и DTO (просто создай их), тестируемый класс, типы JDK (`List`, `Optional`), внутренности фреймворков, которые тебе не принадлежат (`EntityManager`, в большинстве случаев `KafkaTemplate`; интеграционная альтернатива — в [[testcontainers]]), и всё, где написанная вручную заглушка (fake) понятнее.

## Пример
Сервис, который резервирует профиль eSIM и порождает outbox-событие; репозиторий и шлюз замоканы, сохранённая сущность перехватывается.
```java
@ExtendWith(MockitoExtension.class)
class ProfileReservationServiceTest {

    @Mock ProfileRepository profiles;
    @Mock SmDpGateway smdp;
    @Mock OutboxWriter outbox;
    @Captor ArgumentCaptor<OutboxEvent> eventCaptor;
    @InjectMocks ProfileReservationService service;

    @Nested
    class WhenProfileIsFree {
        @BeforeEach
        void stub() {
            when(profiles.findFree("KG-PREPAID")).thenReturn(Optional.of(new Profile("8999..01", FREE)));
            when(smdp.reserve("8999..01")).thenReturn(new ReserveResult("AC-123"));
        }

        @Test
        void reservesAndPublishesEvent() {
            var result = service.reserve(new ReserveCommand("order-7", "KG-PREPAID"));

            assertThat(result.activationCode()).isEqualTo("AC-123");
            var inOrder = inOrder(smdp, profiles, outbox);
            inOrder.verify(smdp).reserve("8999..01");
            inOrder.verify(profiles).save(argThat(p -> p.status() == RESERVED));
            inOrder.verify(outbox).write(eventCaptor.capture());
            assertThat(eventCaptor.getValue())
                    .extracting(OutboxEvent::type, OutboxEvent::aggregateId)
                    .containsExactly("ProfileReserved", "order-7");
        }
    }

    @ParameterizedTest(name = "{0} -> ProfileUnavailable")
    @ValueSource(strings = {"KG-POSTPAID", "KG-TOURIST"})
    void failsWhenNoFreeProfile(String plan) {
        when(profiles.findFree(plan)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.reserve(new ReserveCommand("order-8", plan)))
                .isInstanceOf(ProfileUnavailableException.class);
        verifyNoInteractions(smdp, outbox);
    }
}
```

## Подводные камни
- `when(spy.realMethod())` выполняет реальный метод во время создания заглушки; со spy репозитория это может обратиться к настоящей БД или бросить исключение. Всегда используй `doReturn(...).when(spy)`.
- Смешивание обычных значений и матчеров в одном вызове — самое частое исключение Mockito у новичков; сообщение об ошибке прямо называет правило.
- Незакрытый `MockedStatic` протекает в другие тесты того же потока и даёт загадочные падения далеко от причины.
- `@InjectMocks` с двумя конструкторами или `@Mock` типа, который класс не использует: Mockito выбирает самый большой конструктор и оставляет несопоставленные поля `null`; `NullPointerException` внутри сервиса обычно означает именно это.
- Избыточные проверки: если делать `verify` каждого вызова, тест ломается при каждом рефакторинге. Сначала проверяй возвращённое состояние; через `verify` проверяй только важные побочные эффекты (записано событие, отправлено SMS).
- Если каждый раз приходится статически мокать `Clock`, `Random` или `UUID.randomUUID()`, значит, класс должен получать их как зависимости ([[spring-ioc-and-di]]).
- Связанные заметки: [[testing-types-and-dev-cycles]] — про пирамиду тестов и тестовых дублёров, [[testcontainers]] — про интеграционный уровень, [[exceptions]] — про проектирование исключений, которые ты проверяешь.
