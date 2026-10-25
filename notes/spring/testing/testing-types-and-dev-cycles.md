---
title: Test types, test doubles and development cycles
tags: [testing, junit, mockito, spring-boot-test, tdd]
learned: 2026-10-22
---
## TL;DR
**Testing** checks that the real behaviour of a program matches the expected one. The pyramid: many fast **unit** tests of one class, fewer **integration** tests of components working together (Spring context, real Postgres/Kafka in Testcontainers), a handful of **end-to-end** tests of the whole system, plus performance and security testing. **Test doubles** (dummy, fake, stub, mock, spy) replace collaborators. The dev cycle decides *when* tests are written: classic git-flow (often after the code), trunk-based development (tests with every small commit, features behind flags), TDD (tests first).

## Key points
### Types of tests
| Type | Checks | Tools in a Spring project | Speed |
|---|---|---|---|
| Unit | one class/method in isolation, collaborators mocked | JUnit 5, Mockito, AssertJ; no Spring context | ms |
| Integration | interaction between components: service + repository + DB, Kafka listener, HTTP client | `@SpringBootTest`, slices (`@WebMvcTest`, `@DataJpaTest`, `@RestClientTest`), Testcontainers, WireMock | s |
| End-to-end | the deployed system through its public interface | REST Assured / Playwright against a stage env | min |
| Performance | latency and throughput under load | k6, Gatling, JMeter | min-h |
| Security | vulnerabilities, auth rules | OWASP ZAP, dependency-check, `@WithMockUser` tests | varies |

- Boot slices: `@WebMvcTest(Controller.class)` loads only MVC + the controller and gives `MockMvc` (`MockMvcTester` in Boot 3.4 with AssertJ style); `@DataJpaTest` loads JPA with an embedded or Testcontainers DB (`@AutoConfigureTestDatabase(replace = NONE)` to keep Postgres); `@RestClientTest` for HTTP clients ([[rest-clients]]). `@SpringBootTest(webEnvironment = RANDOM_PORT)` with `TestRestTemplate`/`WebTestClient` is the full-context integration test.
- Replace a bean in the context with `@MockitoBean` / `@MockitoSpyBean` (Boot 3.4+, Spring Framework 6.2; `@MockBean` is deprecated). Each distinct set of mocked beans creates a new context, which slows the suite; share configurations.
- Testcontainers + `@ServiceConnection` (Boot 3.1+) starts Postgres/Kafka/Redis in Docker and wires the connection properties automatically; this is the standard for repository and [[spring-kafka-consumer]] tests. Reuse containers across tests (`.withReuse(true)` or a static singleton) to keep the suite fast.
- JUnit 5 essentials: `@Test`, `@ParameterizedTest` with `@CsvSource`/`@MethodSource`, `@Nested` for grouping, `@BeforeEach`/`@BeforeAll`, `assertThrows`; AssertJ for fluent assertions (`assertThat(x).isEqualTo(...)`).

### Test doubles: which one when
| You need to | Use | Mockito |
|---|---|---|
| pass a placeholder the logic never touches | **Dummy** | `null` or `mock()` unused |
| test simple business logic against a working lightweight implementation | **Fake** (in-memory repository, H2) | hand-written class |
| make a method return a predefined value | **Stub** | `when(repo.findById(1)).thenReturn(Optional.of(x))` |
| verify that a method was called, how many times, with which arguments | **Mock** | `verify(sender, times(1)).send(eq("996..."), any())` |
| replace part of the behaviour but keep the real object | **Spy** | `spy(realObject)` + `doReturn(...).when(spy).method()` |

Prefer state verification (assert on the result) to interaction verification (verify calls); over-mocking couples tests to implementation details and they break on every refactor.

### Development cycles
- **Classic (git-flow)**: write code → (tests, often skipped or written later) → commit to a feature branch → CI → code review → merge → QA on a stage build → release → release verification → production. Long-lived branches, big merges, slow feedback.
- **Trunk-based development (TBD)**: write code **and tests** → small commit to `main` (or a branch living hours, not weeks) → CI → review → deploy to **production with the feature flag off** → QA on prod or stage → **flag on**. Requires good automated tests and feature toggles; this is what continuous delivery looks like.
- **TDD (test-driven development)**: red → green → refactor: write a failing test, write the minimum code to pass, clean up, repeat. Produces testable design and a regression suite by construction; works best for pure logic (pricing, state machines, parsers) and less well for exploratory UI work.
- Related practices: BDD (Given/When/Then, Cucumber), contract testing between services (Spring Cloud Contract, Pact), mutation testing (PIT) to check the tests actually fail when code breaks.

## Example
Unit test of a service with a stub and a mock, no Spring context:
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
Integration test with Testcontainers:
```java
@SpringBootTest
@Testcontainers
class OtpRepositoryIT {
    @Container @ServiceConnection
    static PostgreSQLContainer<?> pg = new PostgreSQLContainer<>("postgres:16");

    @Autowired OtpRepository otps;

    @Test
    void countsOnlyRecent() { /* insert rows, assert countRecent */ }
}
```

## Pitfalls
- `@SpringBootTest` for everything: 10-second context starts per test class; use slices and plain unit tests, and keep context configurations identical so Spring's context cache hits.
- Tests that depend on order or shared mutable state (static fields, a reused DB without cleanup) pass alone and fail in CI; use `@Transactional` rollback on tests or truncate tables, and avoid `@DirtiesContext`.
- Mocking what you do not own (JPA `EntityManager`, `RestClient`) tests nothing real; mock your own gateway interface instead and integration-test the gateway.
- `@Transactional` on a test hides lazy-loading and commit-time problems (flush never happens); add `TestEntityManager.flush()` or run key tests without the test transaction ([[jpa-persistence-context-and-entity-lifecycle]]).
- Time and randomness: inject a `Clock` and a `Random`/`SecureRandom` so tests are deterministic.
- Related: [[rest-clients]] for the client-testing matrix, [[spring-transactional]], [[docker-fundamentals]] (Testcontainers needs a Docker socket, see [[docker-socket-dind-dood]] in CI).
