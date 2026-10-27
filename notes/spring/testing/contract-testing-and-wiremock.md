---
title: Contract testing and WireMock
tags: [testing, wiremock, contract-testing, pact, spring-boot-test]
learned: 2026-10-27
---
## TL;DR
**WireMock** is an HTTP server you run inside a test to stand in for an external API (SM-DP+, HLR, a payment gateway): you declare request → response stubs, point the client at `wiremock.baseUrl()`, and test timeouts, 5xx, malformed bodies and retries that you could never trigger against a real sandbox. **Contract testing** answers a different question: does the stub match what the provider really returns, and does the provider still return what its consumers rely on? In **consumer-driven contracts** (Pact, Spring Cloud Contract) the consumer writes the expectations, they are recorded as a contract file, and the provider's CI replays that contract against the real implementation. WireMock verifies *your* code against *your assumptions*; contracts verify the assumptions. Unit mocking of the client interface is in [[junit-and-mockito]]; infrastructure in containers is in [[testcontainers]].

## Key points
### WireMock basics
- Dependency `org.wiremock:wiremock-standalone` (3.x) plus `org.wiremock.integrations:wiremock-spring-boot` for `@EnableWireMock` / `@InjectWireMock`, or the plain JUnit 5 extension `@RegisterExtension static WireMockExtension wm = WireMockExtension.newInstance().options(wireMockConfig().dynamicPort()).build()`.
- Stub DSL: `wm.stubFor(post(urlPathEqualTo("/es2plus/downloadOrder")).withHeader("Content-Type", containing("json")).withRequestBody(matchingJsonPath("$.iccid")).willReturn(okJson("...")))`. Matchers cover URL, headers, query, cookies, JSON/XML body, multipart.
- Fault injection: `.willReturn(aResponse().withFixedDelay(5000))` for timeouts, `withStatus(503)`, `withFault(Fault.CONNECTION_RESET_BY_PEER)`, `Fault.MALFORMED_RESPONSE_CHUNK`. This is how you test [[circuit-breaker]] and retry policies deterministically.
- **Scenarios** are state machines: first call returns 503 in state `Started` and moves to `Recovered`, second call returns 200. Needed for "retry succeeds on the second attempt" tests.
- Verification: `wm.verify(1, postRequestedFor(urlPathEqualTo("/...")).withHeader("Idempotency-Key", matching(".+")))`; `wm.findAll(...)` returns recorded requests for detailed assertions.
- Stub files: JSON mappings under `src/test/resources/mappings/` and bodies under `__files/` load automatically; handy for large provider responses captured once with the record/playback proxy mode.
- Wiring into Spring: a `@DynamicPropertySource` that sets `smdp.base-url` to the WireMock URL, or `@EnableWireMock(@ConfigureWireMock(name = "smdp", property = "smdp.base-url"))` which does the same declaratively. Client config itself is in [[rest-clients]]; `@RestClientTest` with `MockRestServiceServer` is an in-process alternative when you only test the client class.

### What WireMock cannot tell you
- The stub is your belief about the provider. If the provider renames `eid` to `EID`, adds a required header or changes a status code, every WireMock test stays green and production breaks. Options: capture real responses periodically (record mode against a sandbox), validate stubs against the provider's OpenAPI spec ([[openapi-swagger]] + WireMock's OpenAPI validation extension), or use a contract.

### Contract testing
| | Consumer-driven (Pact) | Provider-driven (Spring Cloud Contract) |
|---|---|---|
| Who writes the contract | consumer, in a consumer test | provider, as Groovy/YAML DSL next to the code |
| Artifact | pact JSON in a Pact Broker | stub JAR published to Maven/Artifactory |
| Provider side | provider CI fetches pacts and replays them against the running service with provider states | tests are generated from the DSL and run against the provider |
| Consumer side | consumer test runs against a Pact mock server | consumer starts `StubRunner` with the published stubs (WireMock underneath) |
| Best for | many consumers whose needs the provider must not break (`can-i-deploy`) | one team owning the API and wanting consumers to use its stubs |

- Contracts are about the *shape and semantics* of the interaction (fields present, types, status for a given state), not about business data. Use matchers (`like`, `regex`, `eachLike`) rather than literal values so the provider can satisfy them with any valid data.
- Provider states ("profile 8999…01 exists and is RELEASED") let the provider set up data before replay; they are the glue that makes replay realistic.
- Both tools work for messaging too: Pact has message pacts, Spring Cloud Contract supports Kafka/AMQP, which is where [[message-schema-evolution]] meets testing.
- Where it pays off: internal services across teams in one company, a public API with SDK consumers, a monolith being split ([[strangler-fig-migration]], [[monolith-vs-microservices]]). Where it does not: a third-party provider that will never run your pacts; there you fall back to recorded WireMock stubs plus a nightly smoke test against the sandbox.

### How the layers fit
| Layer | Tool | Catches |
|---|---|---|
| Unit | Mockito mock of `SmDpClient` interface | service logic |
| Client integration | WireMock / `MockRestServiceServer` | serialisation, headers, timeouts, retries, error mapping |
| Contract | Pact / Spring Cloud Contract | drift between consumer expectation and provider reality |
| End-to-end / smoke | real sandbox in a scheduled pipeline | everything else, slowly and flakily |

## Example
Client test with WireMock: the SM-DP+ returns 503 once, the client retries and succeeds, and the idempotency header is sent on both attempts.
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
                .willReturn(aResponse().withFixedDelay(3_000).withStatus(200)));   // client timeout is 1 s

        assertThatThrownBy(() -> client.downloadOrder(new DownloadOrderRequest("8999...", "8904...")))
                .isInstanceOf(SmDpUnavailableException.class);
    }
}
```
A Pact consumer test for the same call would describe the request and a response with `like("ABC-123")` matchers; the SM-DP+ team's CI would then replay it, and `pact-broker can-i-deploy` gates the deploy.

## Pitfalls
- Stubs with `anyUrl()` and a canned 200: the test passes no matter what the client sends. Match the path, the body fields that matter and verify the request.
- Shared WireMock state between tests: `smdp.resetAll()` in `@BeforeEach` (the Spring Boot integration does this by default) or scenario state from a previous test bleeds through.
- Fixed ports (`8089`) collide in parallel builds; use `dynamicPort()` and inject the URL.
- Mocking the HTTP layer and the client at the same time: either `@MockitoBean SmDpClient` (unit level) or WireMock (integration level), not both in one test.
- Contracts with literal values and full bodies: every unrelated provider change breaks them and teams start ignoring failures. Match only what the consumer reads.
- Related: [[testing-async-and-kafka-flows]] for the asynchronous counterpart of these tests, [[circuit-breaker]] for the resilience behaviour WireMock faults are meant to exercise.
