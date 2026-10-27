---
title: Наблюдаемость — логи, метрики, трейсы и как они связаны
tags: [observability, opentelemetry, grafana, logging, metrics]
learned: 2026-10-27
---
## Кратко
Наблюдаемость (observability) — это способность объяснить поведение системы по тому, что она излучает, без деплоя нового кода. Три сигнала отвечают на разные вопросы: **метрики** говорят, *что* происходит и *сколько* (p99 задержки вырос, доля ошибок 3 %), **трейсы** говорят, *где* (какой сервис, какой span), **логи** говорят, *почему* (текст исключения, входные данные). Единым инструментом они становятся только тогда, когда разделяют **идентификаторы**: `traceId` в каждой строке лога и как exemplar в метриках, `service.name` и `env` как общие метки. Практический стек за этой заметкой: OpenTelemetry SDK/агент → OTel Collector → Mimir (метрики), Loki (логи), Tempo (трейсы) → Grafana, плюс Sentry для исключений ([[error-tracking-with-sentry]]).

## Основное

### Три сигнала
| Сигнал | Форма | Стоимость | Сильная сторона | Слабая сторона |
|---|---|---|---|---|
| Метрики | Числа на временной интервал с метками | Дёшево, фиксировано на ряд | Алертинг, дашборды, тренды, SLO | «Почему», отдельные запросы, высокая кардинальность |
| Логи | Текстовые/JSON-события с временной меткой | Пропорционально объёму | Детали, отладка, аудит | Агрегация, стоимость на масштабе |
| Трейсы | Дерево span на каждый запрос | Пропорционально сэмплированным запросам | Разбивка задержки по сервисам | Всё, что не попало в выборку |
- Типы метрик и именование: [[otel-metric-types]]. Механика трассировки и сэмплирование: [[distributed-tracing]]. Стеки логирования: [[logging-stacks]].
- Четвёртый сигнал, **профили** (Pyroscope, непрерывное профилирование), отвечает на вопрос «какая строка кода жжёт CPU»; используй его после того, как трейсы указали на медленный span.

### Смысл — в корреляции
- `traceId` и `spanId` в MDC в каждой строке лога (Spring Boot 3 делает это через Micrometer Tracing или агент OTel), чтобы Grafana переходила из Loki в Tempo и обратно.
- **Exemplars**: бакет гистограммы Prometheus/Mimir несёт пример `traceId`, поэтому всплеск задержки на дашборде открывает медленный трейс. Включается через `management.metrics.distribution.percentiles-histogram` в Micrometer плюс поддержку exemplars в OTLP/Prometheus в Spring Boot 3.x.
- Общие атрибуты ресурса: `service.name`, `service.version`, `deployment.environment`, `k8s.pod.name` задаются один раз в SDK (`OTEL_RESOURCE_ATTRIBUTES`) и появляются во всех трёх сигналах; дашборды и алерты фильтруют по тем же меткам.
- Бизнес-ключи как baggage/MDC (`orderId`, хешированный `msisdn`) связывают обращение в поддержку с трейсом; никогда как метки метрик (про кардинальность см. [[otel-metric-types]]).

### Конвейер: OpenTelemetry Collector
- Приложения отправляют OTLP (gRPC 4317 / HTTP 4318); collector принимает, обрабатывает (пакетирование, memory limiter, очистка атрибутов, tail sampling, `k8sattributes`) и экспортирует в бэкенды. Приложения знают только адрес collector; бэкенды меняются без передеплоя приложений.
- Развёртывание: агент на каждом узле (DaemonSet или sidecar в Compose) плюс необязательный слой шлюза для tail sampling и разветвления (fan-out). Grafana Alloy — дистрибутив collector с компонентами в духе Loki/Prometheus.
- Бэкенды в self-hosted стеке LGTM: **Mimir** (горизонтально масштабируемое хранилище Prometheus, PromQL), **Loki** (метки + grep, LogQL), **Tempo** (трейсы в объектном хранилище, TraceQL), **Grafana** (один UI, один движок алертинга). Все хранят данные в объектном хранилище (MinIO/S3) и масштабируются одинаково, поэтому и живут вместе (именно это работает в [[sebep-incident-analysis]]).

### Что инструментировать в Spring Boot сервисе
- Автоматически: таймеры HTTP-сервера/клиента, память/GC/потоки JVM, пул HikariCP, Kafka-клиент, breakers Resilience4j через Actuator + Micrometer ([[spring-boot-starters-and-autoconfiguration]], [[hikaricp-pool-sizing]], [[circuit-breaker]]).
- Вручную: один бизнес-счётчик на доменный исход (`esim_activation_total{result="ok|rejected|failed"}`), один таймер на внешнюю зависимость, один gauge на глубину очереди. Этого достаточно для дашбордов RED/USE ([[grafana-dashboards-and-red-use]]) и SLO ([[slo-sli-and-alerting]]).
- Логи: JSON, `level`, `logger`, `message`, `traceId`, `spanId`, исключение со стеком вызовов как одно событие; без PII ([[owasp-top-10-for-backend]]).

### Мониторинг и наблюдаемость (вопрос с собеседования)
- Мониторинг = известные режимы отказа, дашборды и алерты, настроенные заранее. Наблюдаемость = достаточно сигналов, чтобы расследовать *неизвестные* отказы. На практике: мониторинг — это сработавший алерт, наблюдаемость — возможность ответить на вопрос, поднятый алертом, не добавляя строку лога и не передеплоивая.

## Пример
Свойства management в Spring Boot 3.x, включающие все три сигнала с отправкой по OTLP в один collector, плюс конвейер collector, который разводит их по бэкендам.

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
    logging:                                    # Spring Boot 3.4+: экспорт логов по OTLP
      endpoint: http://otel-collector:4318/v1/logs
  metrics:
    distribution:
      percentiles-histogram:
        http.server.requests: true              # гистограммы + exemplars для дашбордов p99
    tags:
      application: ${spring.application.name}
      env: ${ENV:dev}
  observations:
    key-values:
      service.version: ${APP_VERSION:dev}
logging:
  structured:
    format:
      console: ecs                              # JSON с полями trace.id / span.id
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

## Подводные камни
- Три сигнала в трёх UI без общих идентификаторов: у тебя мониторинг, а не наблюдаемость. Прежде чем считать стек готовым, проверь, что `traceId` из строки лога действительно открывает трейс.
- Логировать всё на уровне INFO «для наблюдаемости»: объём Loki взрывается, а полезная строка тонет. Метрики — для счётчиков, логи — для контекста, трейсы — для задержки.
- Сэмплировать трейсы на 100 % в production, а потом удивляться, почему объектное хранилище Tempo занимает 2 ТБ; head sampling на 10–20 % плюс tail-правила для ошибок.
- Collector без `memory_limiter`: всплеск span убивает collector по OOM и разом выключает все три сигнала.
- Связанные заметки: [[logging-stacks]], [[otel-metric-types]], [[distributed-tracing]], [[grafana-dashboards-and-red-use]], [[slo-sli-and-alerting]], [[error-tracking-with-sentry]], [[kubernetes-basics]].
