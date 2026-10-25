---
title: OpenTelemetry metric types and naming
tags: [observability, metrics, opentelemetry, prometheus]
learned: 2026-10-22
---
## TL;DR
OpenTelemetry (and Prometheus, and Micrometer) expose three instrument families: a **Counter** only grows (requests, errors, processed messages), a **Histogram** records the distribution of values (latency, payload size), a **Gauge** reports a current value that can go up or down (active connections, queue size, goroutines or threads). Attributes (labels) split a metric by dimension; names follow `snake_case`, counters end in `_total`, duration histograms in `_duration_seconds` (or `_ms` if you must).

## Key points
### Instrument types
| Type | API (Go SDK) | Semantics | Use for |
|---|---|---|---|
| Counter | `metric.Int64Counter`, `.Add(ctx, 1)` | Monotonic, cumulative; query with `rate()`/`increase()` | Request count, error count, messages processed |
| UpDownCounter | `metric.Int64UpDownCounter`, `.Add(ctx, ±n)` | Cumulative but may decrease | Items in a queue when you observe adds and removes |
| Histogram | `metric.Int64Histogram` / `Float64Histogram`, `.Record(ctx, value)` | Buckets + sum + count; percentiles via `histogram_quantile` | Request duration, payload size, latency |
| Gauge | `metric.Int64Gauge` (sync, since otel-go 1.27) or `Int64ObservableGauge` (callback) | Last value, can rise and fall | Active connections, pool usage, running goroutines/threads, queue length |
- Counter: `h.metrics.TransactionStatusCounter.Add(ctx, 1)` increments by one. Never reset it yourself; the backend computes rates.
- Histogram: measure around the operation (`start := time.Now()` ... `Record(ctx, time.Since(start).Milliseconds())`), typically in a `defer` so every exit path is recorded.
- Gauge: `h.metrics.ActiveConnections.Record(ctx, current)` writes the current value; an observable gauge instead registers a callback that the SDK calls at collection time, which is cheaper when the value is already available (pool stats, [[hikaricp-pool-sizing|HikariCP]] exposes exactly these).
- In Java/Spring the same shapes come from Micrometer: `Counter`, `Timer` (a histogram with duration semantics), `Gauge`, `DistributionSummary`; Spring Boot Actuator registers JVM, HTTP server, datasource and Kafka client metrics automatically and exports to Prometheus or via OTLP ([[spring-boot-starters-and-autoconfiguration]]).

### Attributes (labels)
- Attributes group a metric by dimensions: `metric.WithAttributes(attribute.String("error_type", "not_found"), attribute.String("endpoint", "status"))`. In Prometheus this appears as `transaction_status_error_total{error_type="not_found", endpoint="status"} 42`.
- Every distinct attribute combination is a separate time series. Keep cardinality bounded: endpoint, status class, error type, yes; user id, transaction id, raw URL with ids, no.

### Naming conventions
1. `snake_case`.
2. Counters end with `_total`.
3. Duration histograms end with `_duration_seconds` (Prometheus base unit) or `_duration_ms` when the whole system agrees on milliseconds.
4. Gauges describe a current state and carry the unit as a suffix (`_bytes`, `_connections`).
- Good: `transaction_status_total`, `transaction_processing_duration_ms`, `active_database_connections`, `queue_size_bytes`.
- Bad: `TransactionStatus` (not snake_case), `status_counter` (not obviously a total), `duration` (too generic, no subject, no unit).
- Prefix with the service or domain (`esim_`, `sebep_`) when several services share one Prometheus, and use OpenTelemetry semantic conventions (`http.server.request.duration`) for standard signals so dashboards are reusable.

## Example
```go
// metrics.go: define once, inject into handlers
type Metrics struct {
    TransactionStatusErrorCounter metric.Int64Counter
    TransactionStatusDuration     metric.Int64Histogram
    ActiveConnections             metric.Int64Gauge
}

func NewMetrics(m metric.Meter) (*Metrics, error) {
    var err error
    x := &Metrics{}
    if x.TransactionStatusErrorCounter, err = m.Int64Counter("transaction_status_error_total",
        metric.WithDescription("Total number of errors when getting transaction status")); err != nil {
        return nil, fmt.Errorf("create transaction_status_error_total: %w", err)
    }
    if x.TransactionStatusDuration, err = m.Int64Histogram("transaction_status_duration_ms",
        metric.WithDescription("Duration of getting transaction status in milliseconds"),
        metric.WithUnit("ms")); err != nil {
        return nil, fmt.Errorf("create transaction_status_duration_ms: %w", err)
    }
    if x.ActiveConnections, err = m.Int64Gauge("active_database_connections"); err != nil {
        return nil, fmt.Errorf("create active_database_connections: %w", err)
    }
    return x, nil
}

// handler.go
func (h *Handler) Status(c *gin.Context) {
    ctx := c.Request.Context()
    start := time.Now()
    defer func() {
        h.metrics.TransactionStatusDuration.Record(ctx, time.Since(start).Milliseconds())
    }()

    tx, err := h.repo.FindStatus(ctx, c.Param("id"))
    if err != nil {
        h.metrics.TransactionStatusErrorCounter.Add(ctx, 1, metric.WithAttributes(
            attribute.String("error_type", classify(err)),   // bounded set: not_found, timeout, db
            attribute.String("endpoint", "status"),
        ))
        c.JSON(http.StatusNotFound, gin.H{"error": err.Error()})
        return
    }
    c.JSON(http.StatusOK, tx)
}
```

## Pitfalls
- Recording a duration with a Gauge loses the distribution; use a Histogram and compute p95/p99 in the backend.
- Default histogram buckets (OTel: 0, 5, 10, 25, 50, 75, 100, 250, 500, 750, 1000, 2500, 5000, 7500, 10000) assume milliseconds; a histogram in seconds needs explicit bucket boundaries or a `View`, or every value lands in the first bucket.
- A counter with an unbounded attribute (`transaction_id`) creates one series per transaction and takes down Prometheus memory.
- Creating instruments per request instead of once at startup leaks and is slow; build them in a constructor, as above.
- Related: [[logging-stacks]], [[kafka-consumer]] (consumer lag as a gauge), [[executors-and-thread-pools]] (pool size and queue gauges).
