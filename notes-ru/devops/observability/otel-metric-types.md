---
title: Типы метрик OpenTelemetry и именование
tags: [observability, metrics, opentelemetry, prometheus]
learned: 2026-10-22
---
## Кратко
OpenTelemetry (а также Prometheus и Micrometer) предоставляет три семейства инструментов: **Counter** только растёт (запросы, ошибки, обработанные сообщения), **Histogram** записывает распределение значений (задержка, размер полезной нагрузки), **Gauge** сообщает текущее значение, которое может расти и падать (активные соединения, размер очереди, горутины или потоки). Атрибуты (метки, labels) разбивают метрику по измерениям; имена пишутся в `snake_case`, счётчики заканчиваются на `_total`, гистограммы длительности — на `_duration_seconds` (или `_ms`, если иначе нельзя).

## Основное
### Типы инструментов
| Тип | API (Go SDK) | Семантика | Для чего |
|---|---|---|---|
| Counter | `metric.Int64Counter`, `.Add(ctx, 1)` | Монотонный, накопительный; запросы через `rate()`/`increase()` | Число запросов, число ошибок, обработанные сообщения |
| UpDownCounter | `metric.Int64UpDownCounter`, `.Add(ctx, ±n)` | Накопительный, но может уменьшаться | Элементы в очереди, когда наблюдаются добавления и удаления |
| Histogram | `metric.Int64Histogram` / `Float64Histogram`, `.Record(ctx, value)` | Бакеты + сумма + количество; перцентили через `histogram_quantile` | Длительность запроса, размер полезной нагрузки, задержка |
| Gauge | `metric.Int64Gauge` (синхронный, с otel-go 1.27) или `Int64ObservableGauge` (callback) | Последнее значение, может расти и падать | Активные соединения, использование пула, работающие горутины/потоки, длина очереди |
- Counter: `h.metrics.TransactionStatusCounter.Add(ctx, 1)` увеличивает на единицу. Никогда не сбрасывай его сам; частоты вычисляет бэкенд.
- Histogram: измеряй вокруг операции (`start := time.Now()` ... `Record(ctx, time.Since(start).Milliseconds())`), обычно в `defer`, чтобы записывался каждый путь выхода.
- Gauge: `h.metrics.ActiveConnections.Record(ctx, current)` записывает текущее значение; observable gauge вместо этого регистрирует callback, который SDK вызывает во время сбора, — это дешевле, когда значение уже доступно (статистика пула; [[hikaricp-pool-sizing|HikariCP]] отдаёт именно такие).
- В Java/Spring те же формы даёт Micrometer: `Counter`, `Timer` (гистограмма с семантикой длительности), `Gauge`, `DistributionSummary`; Spring Boot Actuator автоматически регистрирует метрики JVM, HTTP-сервера, datasource и Kafka-клиента и экспортирует их в Prometheus или через OTLP ([[spring-boot-starters-and-autoconfiguration]]).

### Атрибуты (метки)
- Атрибуты группируют метрику по измерениям: `metric.WithAttributes(attribute.String("error_type", "not_found"), attribute.String("endpoint", "status"))`. В Prometheus это выглядит как `transaction_status_error_total{error_type="not_found", endpoint="status"} 42`.
- Каждая отдельная комбинация атрибутов — это отдельный временной ряд. Держи кардинальность ограниченной: endpoint, класс статуса, тип ошибки — да; id пользователя, id транзакции, сырой URL с идентификаторами — нет.

### Соглашения об именовании
1. `snake_case`.
2. Счётчики заканчиваются на `_total`.
3. Гистограммы длительности заканчиваются на `_duration_seconds` (базовая единица Prometheus) или `_duration_ms`, когда вся система договорилась о миллисекундах.
4. Gauge описывают текущее состояние и несут единицу измерения в суффиксе (`_bytes`, `_connections`).
- Хорошо: `transaction_status_total`, `transaction_processing_duration_ms`, `active_database_connections`, `queue_size_bytes`.
- Плохо: `TransactionStatus` (не snake_case), `status_counter` (не очевидно, что это total), `duration` (слишком общее, без субъекта, без единицы).
- Добавляй префикс сервиса или домена (`esim_`, `sebep_`), когда несколько сервисов используют один Prometheus, и используй семантические соглашения OpenTelemetry (`http.server.request.duration`) для стандартных сигналов, чтобы дашборды можно было переиспользовать.

## Пример
```go
// metrics.go: определить один раз, внедрять в обработчики
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
            attribute.String("error_type", classify(err)),   // ограниченный набор: not_found, timeout, db
            attribute.String("endpoint", "status"),
        ))
        c.JSON(http.StatusNotFound, gin.H{"error": err.Error()})
        return
    }
    c.JSON(http.StatusOK, tx)
}
```

## Подводные камни
- Запись длительности через Gauge теряет распределение; используй Histogram и вычисляй p95/p99 в бэкенде.
- Бакеты гистограммы по умолчанию (OTel: 0, 5, 10, 25, 50, 75, 100, 250, 500, 750, 1000, 2500, 5000, 7500, 10000) рассчитаны на миллисекунды; гистограмме в секундах нужны явные границы бакетов или `View`, иначе каждое значение попадёт в первый бакет.
- Счётчик с неограниченным атрибутом (`transaction_id`) создаёт по ряду на каждую транзакцию и обрушивает память Prometheus.
- Создание инструментов на каждый запрос вместо одного раза при старте приводит к утечкам и медленно; создавай их в конструкторе, как выше.
- Связанные заметки: [[logging-stacks]], [[kafka-consumer]] (лаг consumer как gauge), [[executors-and-thread-pools]] (gauge для размера пула и очереди).
