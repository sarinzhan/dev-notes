---
title: Testing asynchronous and Kafka flows
tags: [testing, kafka, awaitility, async, spring-kafka]
learned: 2026-10-27
---
## TL;DR
An asynchronous flow (a `@KafkaListener`, an `@Async` method, a `@Scheduled` outbox relay, a Temporal activity) finishes on another thread at an unknown moment, so a test cannot assert right after the trigger. The rules: **poll for the expected state with Awaitility** instead of sleeping, **control time** by injecting a `Clock` and calling scheduled methods directly instead of waiting for the scheduler, and **make the asynchrony observable** (a repository row, an outbox record, a WireMock request, a `CountDownLatch` in a test listener). For the broker, prefer a Testcontainers Kafka over `@EmbeddedKafka`; the wiring itself is in [[testcontainers]], and this note covers the test design on top of it.

## Key points
### Awaitility
- `await().atMost(Duration.ofSeconds(10)).pollInterval(Duration.ofMillis(100)).untilAsserted(() -> assertThat(repo.findById(id)).hasValueSatisfying(a -> assertThat(a.status()).isEqualTo(ACTIVE)))`: re-runs the lambda until it passes or the timeout fails the test with the last assertion error.
- `until(callable, matcher)` for a boolean condition, `pollDelay(Duration.ZERO)` to check immediately, `ignoreExceptions()` when the first polls throw (row not there yet).
- `during(Duration)` asserts that a condition *stays* true: useful for "the message was NOT processed twice within 2 s" tests.
- Set a generous `atMost` (10–15 s) for CI machines; the test returns as soon as the condition holds, so a large timeout costs nothing on success.
- `Thread.sleep(2000)` is both too long (every test pays it) and too short (CI under load fails randomly). Replace every one.

### Embedded Kafka vs Testcontainers
| | `@EmbeddedKafka` (spring-kafka-test) | `KafkaContainer` / `ConfluentKafkaContainer` |
|---|---|---|
| Startup | ~2 s, in-JVM | ~5–10 s, Docker required |
| Fidelity | ZooKeeper-era broker in the same JVM; KRaft mode available but still "the test broker" | real broker image, same version as production |
| Config drift | port and `spring.embedded.kafka.brokers` property | `@ServiceConnection` sets `spring.kafka.bootstrap-servers` |
| Isolation | shares the JVM heap and threads with the app under test | separate process; log noise stays outside |
| Good for | quick listener tests without Docker, CI without a Docker socket | everything else, especially transactions, compaction, consumer-group rebalances |

- Either way, set `spring.kafka.consumer.auto-offset-reset=earliest` and use a **unique group id per test class** (`${random.uuid}` or a `@DynamicPropertySource`), otherwise committed offsets from a previous class skip your record.
- Wait for partition assignment before producing: `ContainerTestUtils.waitForAssignment(container, partitions)` on the `MessageListenerContainer` from `KafkaListenerEndpointRegistry`, or simply `await()` on the result; a record produced before assignment is still consumed with `earliest`, so the second approach is usually enough.

### Testing a consumer
- Through the real listener (preferred): produce with `KafkaTemplate`, await the side effect. Tests the deserialiser, the error handler, retries and the DLQ path ([[spring-kafka-consumer]], [[dead-letter-queues-and-retries]]).
- Unit level: call the `@KafkaListener` method directly with a `ConsumerRecord` and mocked collaborators; fast, but says nothing about the container config.
- Error paths: publish a malformed payload and assert it lands on the `.DLT` topic, consumed in the test with `KafkaTestUtils.getSingleRecord(consumer, "topic.DLT")`. Assert the retry count with a `@MockitoSpyBean` on the handler or an `AtomicInteger` in a test `RecordInterceptor`.
- Idempotency: send the same record twice (same key and event id) and assert `during(2 s)` that only one row or one outbound call exists ([[idempotency]]).

### Testing a producer and the outbox
- Consumer-in-test: create a `KafkaConsumer` with `KafkaTestUtils.consumerProps(bootstrap, "test-group", "true")`, subscribe, `KafkaTestUtils.getRecords(consumer, Duration.ofSeconds(5))`, assert key, headers and payload.
- Outbox relay ([[outbox-pattern]]): insert the business row through the service, then call `relay.relay()` directly (no `@Scheduled` wait), then assert the record on the topic and `published_at IS NOT NULL`. Testing the relay as a plain method removes the timing question entirely.
- Transactions: a `@Transactional` test method rolls back, so the listener thread never sees the row and the relay never finds it. Keep such tests non-transactional and clean up in `@AfterEach`.

### Controlling time
- Inject `java.time.Clock` into every component that reads the time; in tests use `Clock.fixed(...)` or a mutable test clock with `advance(Duration)`. This turns "token expires after 15 minutes" into a synchronous assertion.
- `@Scheduled` methods: do not wait for the cron. Call the bean method directly, and in `@SpringBootTest` disable scheduling with a profile (`@ConditionalOnProperty` on the `@EnableScheduling` config, or `spring.task.scheduling.enabled=false` in your own property) so the real scheduler does not race the test.
- `@Async`: for integration tests replace the executor with a `SyncTaskExecutor` via a `@TestConfiguration` so the method runs inline; for unit tests call the method directly. Keep one test with the real executor to catch "the `@Async` proxy is bypassed by a self-call" bugs ([[spring-aop]]).
- Temporal workflows: `TestWorkflowEnvironment` skips time, so a workflow with a 24-hour timer completes in milliseconds; activities are mocked with Mockito ([[temporal-concepts]]).
- Reactive/`CompletableFuture` code: `future.get(5, SECONDS)` or `StepVerifier.withVirtualTime` ([[completable-future]], [[spring-reactive-webclient-sse]]).

## Example
Consumer test with a test clock, a failure on the first attempt that is retried, and an idempotency check with `during`.
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
                .thenThrow(new HlrTimeoutException())        // first delivery fails -> container retries
                .thenReturn(HlrResult.OK);
        String event = """{"eventId":"e-1","orderId":"o-1","msisdn":"996555000001"}""";

        kafka.send("esim.profile-downloaded", "o-1", event);
        kafka.send("esim.profile-downloaded", "o-1", event);  // duplicate delivery

        await().atMost(Duration.ofSeconds(15)).untilAsserted(() -> {
            Activation a = activations.findByOrderId("o-1").orElseThrow();
            assertThat(a.status()).isEqualTo(ActivationStatus.ACTIVE);
            assertThat(a.activatedAt()).isEqualTo(clock.instant());   // time came from the injected Clock
        });
        await().during(Duration.ofSeconds(2)).atMost(Duration.ofSeconds(3))
               .until(() -> activations.countByOrderId("o-1") == 1);  // no second row from the duplicate
        verify(hlr, times(2)).activate("996555000001");               // one failure + one success
    }
}
```
`MutableClock` is a ten-line `Clock` subclass with `advance(Duration)`; `activatedAt` assertions become exact instead of "within the last few seconds".

## Pitfalls
- Asserting after `kafka.send(...)` without `await`: passes on a fast laptop, fails in CI. Also `send()` is async; `send(...).get()` only guarantees the broker ack, not consumption.
- Reusing a consumer group across test classes: offsets committed by class A make class B's listener start after its record. Random group ids or `earliest` with a fresh topic per class.
- `@DirtiesContext` to "fix" cross-test leaks: it rebuilds the context and restarts containers, turning a 10 s suite into minutes. Fix the leak (unique topics, cleanup, reset mocks).
- `@Transactional` on async tests, `@EmbeddedKafka` and a Testcontainers Kafka in the same context (two `bootstrap-servers`), or a `Thread.sleep` "just to be safe" before the first await.
- Tests that depend on wall-clock time (`Instant.now()` in production code): flaky at midnight and untestable for expiry logic; inject the `Clock`.
- Related: [[contract-testing-and-wiremock]] for the synchronous HTTP side of the same flows, [[exactly-once-myth]] for why the duplicate-delivery test above is the one that matters.
