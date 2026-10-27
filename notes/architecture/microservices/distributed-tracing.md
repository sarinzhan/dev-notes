---
title: Distributed tracing with OpenTelemetry in Spring Boot 3
tags: [observability, opentelemetry, tracing, spring-boot, kafka]
learned: 2026-10-27
---
## TL;DR
A **trace** is the full story of one request across services; it is a tree of **spans**, each a timed unit of work with attributes. The trace id travels between services in the W3C `traceparent` header (HTTP) or a Kafka record header, so each service can attach its spans to the same tree. In Spring Boot 3 you get tracing either from the **OpenTelemetry Java agent** (zero code, attach at startup) or from **Micrometer Tracing** inside the app; both export to an OTel Collector that forwards to Tempo or Jaeger. Put the `traceId` into log MDC and you can jump from a log line to the whole trace.

## Key points

### Vocabulary
- **Span**: name, start/end time, `traceId`, `spanId`, `parentSpanId`, status, attributes (`http.route`, `db.statement`), events, links. A span without a parent is the root.
- **Context propagation**: the current span lives in a thread-local / `Context`; crossing a thread, an HTTP call or a Kafka record requires explicitly carrying it (instrumentation does this for `RestClient`, `WebClient`, `KafkaTemplate`, `@KafkaListener`, executors wrapped by `ContextExecutorService`).
- **W3C Trace Context**: `traceparent: 00-<32 hex traceId>-<16 hex spanId>-<flags>`; flags `01` = sampled. `tracestate` carries vendor data. **Baggage** (`baggage: tenant=beeline,orderId=42`) is arbitrary key/values that propagate with the trace and can be copied into span attributes and MDC — useful for `subscriberId` across the eSIM flow. Baggage is sent to every downstream service, so never put secrets or large values there.
- Kafka: the producer interceptor writes `traceparent` into record headers; the consumer creates a child span (or a span *linked* to the producer span, because consumption is async and may happen much later). Spring Kafka 3.x has observation support (`spring.kafka.template.observation-enabled`, `...listener.observation-enabled`) — see [[spring-kafka-consumer]].

### Agent vs Micrometer Tracing
| | OpenTelemetry Java agent | Micrometer Tracing (Spring Boot 3 Observation API) |
|---|---|---|
| Setup | `-javaagent:opentelemetry-javaagent.jar`, env vars `OTEL_*` | Dependencies `micrometer-tracing-bridge-otel` + `opentelemetry-exporter-otlp`, YAML config |
| Coverage | ~200 libraries instrumented by bytecode, including JDBC, Kafka clients, gRPC, HTTP clients, executors | Whatever Spring instruments via `Observation` (MVC, WebFlux, RestClient, Kafka, JDBC via `datasource-micrometer`) |
| Custom spans | `@WithSpan` from `opentelemetry-instrumentation-annotations` or the OTel `Tracer` API | `@Observed`, `Tracer`/`ObservationRegistry` beans, `@NewSpan` |
| Operations fit | Ops attaches it in the Dockerfile/Helm, same for all JVM apps | App owns it; metrics and traces share one `Observation`, so one `@Observed` gives timer + span |
| Overhead | Slightly higher startup; runtime a few percent | Low |
- Do not run both: you get duplicate spans. The agent is the usual choice in a Kubernetes estate; Micrometer when the app team wants control and unified metrics ([[otel-metric-types]]).
- Export path: app → **OTLP** (gRPC 4317 / HTTP 4318) → **OTel Collector** (batch, tail-sample, add `k8s.*` attributes, fan out) → **Tempo** or **Jaeger** (storage + UI via Grafana). The collector is the place to change backends without touching apps.

### Sampling
- **Head sampling**: decided at the root span, before anything is known. `parentbased_traceidratio` with ratio 0.1 keeps 10 % of traces, and children follow the parent's flag so traces are never half-sampled. Cheap, but drops the error traces you wanted.
- **Tail sampling**: the collector buffers the whole trace and decides at the end: keep every trace with an error or latency > 2 s, 5 % of the rest. Needs all spans of a trace on the same collector instance (load-balancing exporter by trace id) and memory.
- Development: `always_on`. Production: head 10–20 % plus tail rules for errors.

### Logs, metrics, traces
- **Metrics** say *that* something is wrong (p99 latency up); **traces** say *where* (which span); **logs** say *why* (exception text). Correlation: both Micrometer Tracing and the agent inject `traceId` and `spanId` into SLF4J MDC, so the JSON log line carries them and Loki/Grafana can link log ↔ trace ([[logging-stacks]]). Spring Boot 3.x default console pattern already prints `[app,traceId,spanId]`.
- Exemplars: Prometheus histograms can carry a sample trace id per bucket, so a latency spike in a dashboard links straight to a slow trace.
- Sentry picks up the same `traceId` when its OpenTelemetry integration is enabled, which is how an exception in Sentry links to the Tempo trace ([[sebep-incident-analysis]]).

## Example
Micrometer Tracing with OTLP export in Spring Boot 3.x, baggage propagated into MDC, and a manual span around the SM-DP+ call.

```yaml
management:
  tracing:
    enabled: true
    sampling:
      probability: 0.2
    baggage:
      remote-fields: [orderId]
      correlation:
        fields: [orderId]      # also goes into MDC
  otlp:
    tracing:
      endpoint: http://otel-collector:4318/v1/traces
  observations:
    key-values:
      service.version: ${APP_VERSION:dev}
spring:
  application:
    name: esim-provisioning
  kafka:
    template.observation-enabled: true
    listener.observation-enabled: true
logging:
  pattern:
    level: "%5p [${spring.application.name},%X{traceId:-},%X{spanId:-},%X{orderId:-}]"
```

```java
@Service
@RequiredArgsConstructor
public class ProfileDownloadService {
    private final Tracer tracer;            // io.micrometer.tracing.Tracer
    private final SmdpGateway smdp;

    public DownloadOrder order(String iccid, String orderId) {
        Span span = tracer.nextSpan().name("smdp.downloadOrder")
                .tag("esim.iccid", iccid)
                .tag("order.id", orderId)
                .start();
        try (Tracer.SpanInScope ws = tracer.withSpan(span)) {
            return smdp.downloadOrder(iccid, orderId).join();  // child spans from RestClient attach here
        } catch (RuntimeException e) {
            span.error(e);
            throw e;
        } finally {
            span.end();
        }
    }
}
```

## Pitfalls
- A span that is started and never ended (missed `finally`) leaks and never appears in Tempo; prefer `@Observed`/`@WithSpan` on a method when you can.
- Context is lost in `CompletableFuture.supplyAsync`, `@Async`, `parallelStream` and virtual-thread executors unless the executor is context-propagating (`ContextExecutorService.wrap`, `micrometer-context-propagation`).
- High-cardinality tags (`subscriberId` on every span) are fine for traces but deadly if the same tag leaks into metrics via `@Observed` (metric cardinality explosion).
- The Kafka consumer span's parent may be hours old; dashboards that compute "trace duration" from root to last span show absurd numbers for async flows. Use links or separate traces per consumer with a shared baggage key.
- Spring Boot 3 removed Spring Cloud Sleuth; Sleuth configs (`spring.sleuth.*`) are silently ignored.
- Related: [[logging-stacks]], [[otel-metric-types]], [[spring-kafka-consumer]], [[kubernetes-basics]] (collector as DaemonSet/sidecar), [[system-design-basics]].
