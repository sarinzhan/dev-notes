---
title: Observability — logs, metrics, traces and how they connect
tags: [observability, opentelemetry, grafana, logging, metrics]
learned: 2026-10-27
---
## TL;DR
Observability is the ability to explain the system's behaviour from what it emits, without deploying new code. The three signals answer different questions: **metrics** say *that* and *how much* (p99 latency up, error rate 3 %), **traces** say *where* (which service, which span), **logs** say *why* (the exception text, the input). They become one tool only when they share **identifiers**: `traceId` in every log line and as an exemplar on metrics, `service.name` and `env` as common labels. The practical stack behind this note: OpenTelemetry SDK/agent → OTel Collector → Mimir (metrics), Loki (logs), Tempo (traces) → Grafana, plus Sentry for exceptions ([[error-tracking-with-sentry]]).

## Key points

### The three signals
| Signal | Shape | Cost | Best at | Weak at |
|---|---|---|---|---|
| Metrics | Numbers per time bucket with labels | Cheap, fixed per series | Alerting, dashboards, trends, SLOs | Why, individual requests, high cardinality |
| Logs | Text/JSON events with timestamp | Proportional to volume | Details, debugging, audit | Aggregation, cost at scale |
| Traces | Tree of spans per request | Proportional to sampled requests | Latency breakdown across services | Everything not sampled |
- Metric types and naming: [[otel-metric-types]]. Tracing mechanics and sampling: [[distributed-tracing]]. Log stacks: [[logging-stacks]].
- A fourth signal, **profiles** (Pyroscope, continuous profiling), answers "which line of code burns CPU"; use it after traces point at a slow span.

### Correlation is the point
- `traceId` and `spanId` in MDC on every log line (Spring Boot 3 does it with Micrometer Tracing or the OTel agent) so Grafana jumps Loki → Tempo and back.
- **Exemplars**: a Prometheus/Mimir histogram bucket carries a sample `traceId`, so a latency spike on the dashboard opens a slow trace. Enabled by Micrometer's `management.metrics.distribution.percentiles-histogram` plus the OTLP/Prometheus exemplar support in Spring Boot 3.x.
- Shared resource attributes: `service.name`, `service.version`, `deployment.environment`, `k8s.pod.name` set once on the SDK (`OTEL_RESOURCE_ATTRIBUTES`) and appearing on all three signals; dashboards and alerts filter by the same labels.
- Business keys as baggage/MDC (`orderId`, `msisdn` hashed) connect a support ticket to a trace; never as metric labels ([[otel-metric-types]] on cardinality).

### The pipeline: OpenTelemetry Collector
- Apps send OTLP (gRPC 4317 / HTTP 4318); the collector receives, processes (batching, memory limiter, attribute scrubbing, tail sampling, `k8sattributes`), and exports to backends. Apps know only the collector address; backends change without redeploying apps.
- Deployment: an agent per node (DaemonSet or Compose sidecar) plus an optional gateway tier for tail sampling and fan-out. Grafana Alloy is a collector distribution with Loki/Prometheus-flavoured components.
- Backends in the self-hosted LGTM stack: **Mimir** (horizontally scalable Prometheus storage, PromQL), **Loki** (labels + grep, LogQL), **Tempo** (object-store traces, TraceQL), **Grafana** (one UI, one alerting engine). All store in object storage (MinIO/S3) and scale the same way, which is why they live together ([[sebep-incident-analysis]] runs exactly this).

### What to instrument in a Spring Boot service
- Automatic: HTTP server/client timers, JVM memory/GC/threads, HikariCP pool, Kafka client, Resilience4j breakers via Actuator + Micrometer ([[spring-boot-starters-and-autoconfiguration]], [[hikaricp-pool-sizing]], [[circuit-breaker]]).
- Manual: one business counter per domain outcome (`esim_activation_total{result="ok|rejected|failed"}`), one timer per external dependency, one gauge per queue depth. Enough for RED/USE dashboards ([[grafana-dashboards-and-red-use]]) and SLOs ([[slo-sli-and-alerting]]).
- Logs: JSON, `level`, `logger`, `message`, `traceId`, `spanId`, exception with stack trace as one event; no PII ([[owasp-top-10-for-backend]]).

### Monitoring vs observability (interview)
- Monitoring = known failure modes, dashboards and alerts you set up in advance. Observability = enough signal to investigate *unknown* failures. In practice: monitoring is the alert that fires, observability is being able to answer the question the alert raised without adding a log line and redeploying.

## Example
Spring Boot 3.x management properties that enable all three signals with OTLP to one collector, plus the collector pipeline that fans them out.

```yaml
# application.yaml
spring:
  application:
    name: esim-provisioning
management:
  endpoints:
    web:
      exposure:
        include: health, prometheus, info
  tracing:
    enabled: true
    sampling:
      probability: 0.2
  otlp:
    tracing:
      endpoint: http://otel-collector:4318/v1/traces
    metrics:
      export:
        url: http://otel-collector:4318/v1/metrics
        step: 30s
    logging:                                    # Spring Boot 3.4+: OTLP log export
      endpoint: http://otel-collector:4318/v1/logs
  metrics:
    distribution:
      percentiles-histogram:
        http.server.requests: true              # histograms + exemplars for p99 dashboards
    tags:
      application: ${spring.application.name}
      env: ${ENV:dev}
  observations:
    key-values:
      service.version: ${APP_VERSION:dev}
logging:
  structured:
    format:
      console: ecs                              # JSON with trace.id / span.id fields
```

```yaml
# otel-collector-config.yaml
receivers:
  otlp: { protocols: { grpc: { endpoint: 0.0.0.0:4317 }, http: { endpoint: 0.0.0.0:4318 } } }
processors:
  memory_limiter: { check_interval: 1s, limit_mib: 512 }
  batch: { timeout: 5s }
  attributes/scrub:
    actions:
      - { key: http.request.header.authorization, action: delete }
exporters:
  otlphttp/tempo: { endpoint: http://tempo:4318 }
  prometheusremotewrite/mimir: { endpoint: http://mimir:9009/api/v1/push }
  otlphttp/loki: { endpoint: http://loki:3100/otlp }
service:
  pipelines:
    traces:  { receivers: [otlp], processors: [memory_limiter, attributes/scrub, batch], exporters: [otlphttp/tempo] }
    metrics: { receivers: [otlp], processors: [memory_limiter, batch], exporters: [prometheusremotewrite/mimir] }
    logs:    { receivers: [otlp], processors: [memory_limiter, attributes/scrub, batch], exporters: [otlphttp/loki] }
```

## Pitfalls
- Three signals in three UIs with no shared ids: you have monitoring, not observability. Check that a log line's `traceId` actually opens a trace before calling the stack done.
- Logging everything at INFO "for observability": Loki volume explodes and the useful line is buried. Metrics for counts, logs for context, traces for latency.
- Sampling traces at 100 % in production, then wondering why Tempo's object store is 2 TB; sample head 10–20 % plus tail rules for errors.
- Collector without `memory_limiter`: a burst of spans OOM-kills the collector and takes all three signals down at once.
- Related: [[logging-stacks]], [[otel-metric-types]], [[distributed-tracing]], [[grafana-dashboards-and-red-use]], [[slo-sli-and-alerting]], [[error-tracking-with-sentry]], [[kubernetes-basics]].
