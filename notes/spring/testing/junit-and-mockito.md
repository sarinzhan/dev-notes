---
title: JUnit 5 and Mockito essentials
tags: [testing, junit, mockito, assertj]
learned: 2026-10-27
---
## TL;DR
**JUnit 5** (Jupiter) runs the test: lifecycle hooks, `@ParameterizedTest`, `@Nested`, `assertThrows`. **Mockito** replaces collaborators: `@Mock` for stubs and interaction checks, `@Spy` for partial real objects, `@InjectMocks` to build the class under test, `ArgumentCaptor` to inspect what was passed. **AssertJ** gives readable fluent assertions. Mock only your own boundaries (repository, gateway, sender); never mock value objects or the class you are testing. This note covers the tools; the "which test when" question is in [[testing-types-and-dev-cycles]].

## Key points
### JUnit 5 lifecycle and annotations
- Default lifecycle is `PER_METHOD`: a new test-class instance per test, so instance fields are isolated. `@TestInstance(PER_CLASS)` shares one instance and allows non-static `@BeforeAll`.
- Order of hooks: `@BeforeAll` (static) → `@BeforeEach` → `@Test` → `@AfterEach` → `@AfterAll`. Test method order is deterministic but intentionally unobvious; `@TestMethodOrder` exists but order-dependent tests are a smell.
- `@DisplayName`, `@Disabled("reason")`, `@Tag("slow")` (filter in Maven Surefire with `groups`), `@Timeout`, `@EnabledOnOs`, `@EnabledIfEnvironmentVariable`.
- `@ParameterizedTest` sources: `@ValueSource(strings = ...)`, `@CsvSource({"996555, KG", "7999, RU"})`, `@CsvFileSource`, `@EnumSource(Status.class, names = "ACTIVE")`, `@MethodSource("cases")` returning `Stream<Arguments>`, `@NullSource`/`@EmptySource`. Use `name = "{0} -> {1}"` for readable reports.
- `@Nested` inner classes group scenarios ("when the profile is reserved", "when SM-DP+ times out") and share the outer `@BeforeEach`.
- `assertThrows(Type.class, () -> ...)` returns the exception for further assertions; `assertAll(...)` reports every failed assertion instead of stopping at the first.
- Extensions replace JUnit 4 runners/rules: `@ExtendWith(MockitoExtension.class)`, `SpringExtension` (already inside `@SpringBootTest`), `@RegisterExtension` for programmatic ones.

### AssertJ
- `assertThat(list).hasSize(2).extracting(Order::status).containsExactly(NEW, PAID)`; `assertThatThrownBy(...).isInstanceOf(...).hasMessageContaining(...)`; `usingRecursiveComparison()` for DTO equality without `equals`. Comes with `spring-boot-starter-test`.

### Mockito
- `@ExtendWith(MockitoExtension.class)` initialises `@Mock`, `@Spy`, `@Captor` and `@InjectMocks` (constructor injection first, then setter, then field; a missing dependency silently stays `null`, so prefer a constructor and check it).
- Stubbing: `when(repo.findById(id)).thenReturn(Optional.of(x))` is the readable form. Use `doReturn(x).when(spy).method()` for spies (the `when(...)` form calls the real method first) and `doThrow`/`doNothing` for `void` methods, which cannot be put inside `when(...)`.
- Consecutive answers: `thenReturn(a, b)` or `thenThrow(new TimeoutException()).thenReturn(ok)` to simulate retry.
- `thenAnswer(inv -> inv.getArgument(0))` when the result depends on the input, for example a `save` that returns its argument.
- Verification: `verify(sender).send(...)` (= `times(1)`), `times(n)`, `never()`, `atLeastOnce()`, `verifyNoMoreInteractions(sender)`, `verifyNoInteractions(sms)`. `InOrder io = inOrder(a, b); io.verify(a).reserve(); io.verify(b).activate();` for ordering across mocks.
- `ArgumentCaptor` records the actual argument so you can assert on its fields; declare with `@Captor` or `ArgumentCaptor.forClass`.
- Matchers rule, all or nothing: if one argument uses a matcher (`any()`, `eq()`, `argThat()`), every argument of that call must use one. `send("996...", any())` throws `InvalidUseOfMatchersException`; write `send(eq("996..."), any())`.
- `any()` does not match `null`; `isNull()` or `nullable(Type.class)` does. `anyString()` only matches non-null `String`.
- Statics: `try (MockedStatic<Instant> s = mockStatic(Instant.class)) { s.when(Instant::now).thenReturn(fixed); ... }`. It is thread-local and must be closed. Better design: inject a `Clock`.
- Final classes and methods: since Mockito 5 the inline mock maker is the default, so `final` classes, records-as-collaborators and even constructors (`mockConstruction`) can be mocked without extra setup. That you *can* does not mean you *should*.
- Strict stubs: `MockitoExtension` runs with `Strictness.STRICT_STUBS`. An unused `when(...)` fails the test with `UnnecessaryStubbingException`; a stub called with other arguments fails with `PotentialStubbingProblem`. Fix the test, not the strictness; `lenient()` is for shared `@BeforeEach` stubs only.
- What not to mock: value objects and DTOs (just build them), the class under test, JDK types (`List`, `Optional`), framework internals you do not own (`EntityManager`, `KafkaTemplate` in most cases, see [[testcontainers]] for the integration alternative), and anything where a hand-written fake is clearer.

## Example
Service that reserves an eSIM profile and emits an outbox event; repository and gateway are mocked, the saved entity is captured.
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

## Pitfalls
- `when(spy.realMethod())` executes the real method during stubbing; with a repository spy this can hit a real DB or throw. Always `doReturn(...).when(spy)`.
- Mixing raw values and matchers in one call is the most common Mockito exception for newcomers; the message names the rule explicitly.
- Forgetting to close `MockedStatic` leaks the mock into other tests in the same thread and produces baffling failures far from the cause.
- `@InjectMocks` with two constructors or a `@Mock` of a type the class does not use: Mockito picks the biggest constructor and leaves unmatched fields `null`; a `NullPointerException` inside the service is usually this.
- Over-verification: `verify` every call and the test breaks on every refactor. Assert on returned state first; verify only side effects that matter (an event written, an SMS sent).
- Mocking `Clock`, `Random` or `UUID.randomUUID()` statically every time is a sign the class should take them as dependencies ([[spring-ioc-and-di]]).
- Related: [[testing-types-and-dev-cycles]] for the pyramid and test doubles, [[testcontainers]] for the integration layer, [[exceptions]] for designing the exceptions you assert on.
