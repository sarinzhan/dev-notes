---
title: Дашборды Grafana — методы RED и USE для сервиса
tags: [observability, grafana, prometheus, dashboards, metrics]
learned: 2026-10-27
---
## Кратко
Два чек-листа покрывают почти любой дашборд. **RED** — для сервисов, работающих на запросах: **R**ate (запросов в секунду), **E**rrors (неудачных в секунду или в %), **D**uration (перцентили задержки). **USE** — для ресурсов: **U**tilisation (% занятости), **S**aturation (длина очереди, ожидание), **E**rrors (ошибки железа/драйверов). Дашборд сервиса показывает наверху RED для сервиса и по каждому endpoint, затем RED для каждой вызываемой зависимости (БД, Kafka, SM-DP+), затем USE для ресурсов, которыми он владеет (куча JVM, потоки, пул HikariCP, лаг consumer). Дашборды разворачиваются (provisioning) из JSON в git, используют те же метки, что и алерты ([[slo-sli-and-alerting]]), и ссылаются на Loki и Tempo по `traceId` ([[observability-three-pillars]]).

## Основное

### RED для сервисов, работающих на запросах
| Буква | Вопрос | PromQL (имена Micrometer в Spring Boot) |
|---|---|---|
| Rate | Сколько трафика | `sum(rate(http_server_requests_seconds_count{application="esim-provisioning"}[5m]))` |
| Errors | Сколько падает | `sum(rate(http_server_requests_seconds_count{status=~"5.."}[5m])) / sum(rate(http_server_requests_seconds_count[5m]))` |
| Duration | Насколько медленно | `histogram_quantile(0.99, sum by (le) (rate(http_server_requests_seconds_bucket[5m])))` |
- Разбивай по `uri` (шаблон маршрута, а не сырой путь) и классу `status`; по `method` — только если это важно. Никогда — по пользователю или идентификатору ([[otel-metric-types]]).
- Kafka consumers тоже работают на запросах: rate = потреблённых записей в секунду (`spring_kafka_listener_seconds_count`), errors = неудачные записи + частота DLT ([[dead-letter-queues-and-retries]]), duration = время listener; добавь **лаг** (`kafka_consumer_fetch_manager_records_lag_max`) как сигнал насыщения ([[kafka-consumer]]).
- Зависимости получают собственную строку RED: `http_client_requests_seconds_*` по `client.name`/`uri` для вызовов SM-DP+ и биллинга, состояние breaker в Resilience4j ([[circuit-breaker]]), `hikaricp_connections_acquire_seconds` для базы данных.

### USE для ресурсов
| Ресурс | Utilisation | Saturation | Errors |
|---|---|---|---|
| CPU (контейнер) | `rate(container_cpu_usage_seconds_total[5m]) / limit` | CPU throttling `container_cpu_cfs_throttled_periods_total` | — |
| Куча JVM | `jvm_memory_used_bytes{area="heap"} / jvm_memory_max_bytes` | время пауз GC `rate(jvm_gc_pause_seconds_sum[5m])` | убийства по OOM (счётчик перезапусков) ([[garbage-collection]]) |
| Потоки / Tomcat | `tomcat_threads_busy_threads / tomcat_threads_config_max_threads` | очередь запросов, `executor_queued_tasks` | отклонённые задачи ([[executors-and-thread-pools]]) |
| HikariCP | `hikaricp_connections_active / hikaricp_connections_max` | `hikaricp_connections_pending` | `hikaricp_connections_timeout_total` ([[hikaricp-pool-sizing]]) |
| Диск / volume | занято %, `predict_linear` до заполнения | ожидание IO | ошибки IO ([[linux-memory-filesystems-io]]) |
| Kafka consumer | — | лаг | частота DLT |
- Из USE берутся алерты на уровне причин (диск заполнится через 4 ч); из RED — алерты на уровне симптомов.

### Структура дашборда сервиса
1. Строка заголовка: переменные `env`, `application`, `instance`; ссылки на runbook, на Loki Explore для `{app="$application"}`, на поиск в Tempo по `service.name="$application"`.
2. Строка «Service RED»: три stat-панели (rate, error %, p99) с цветовыми порогами по SLO, затем timeseries для тех же метрик с разбивкой по `uri`.
3. Строка «Dependencies»: RED для каждой нижестоящей зависимости, состояние breaker в виде state timeline.
4. Строка «Resources (USE)»: JVM, потоки, пул, лаг consumer.
5. Строка «Business»: доменные счётчики (`esim_activation_total` по `result`), глубина DLT, бэклог outbox (gauge `outbox_pending` от relay, [[outbox-pattern]]).
- Один дашборд на сервис из общего шаблона (library panels / шаблон Grafonnet или JSON с `$application`), чтобы все сервисы выглядели одинаково и дежурному не приходилось изучать десять разных раскладок.
- Держи не больше ~20 панелей; дашборд — для диагностики, Explore — для разовых запросов.

### Мастерство панелей
- Для задержки — гистограммы, а не средние; `histogram_quantile` поверх `sum by (le)` и окно `[5m]`, которое минимум в 4 раза больше интервала сбора (scrape interval).
- Для счётчиков — `rate()`, никогда не сырое значение; `increase()` для «сколько за последний час».
- Единицы измерения заданы на каждой панели (секунды, проценты 0–1, байты); пороги те же, что в алерте, чтобы панель была красной, когда срабатывает пейджер.
- На панелях задержки включены exemplars: кликни по точке и открой медленный трейс ([[distributed-tracing]]).
- Аннотации деплоев (смена `APP_VERSION` или webhook из CI) объясняют ступенчатые изменения.

### Provisioning
- Дашборды и источники данных хранятся как файлы в `grafana/provisioning/` (или через Grafana Operator в Kubernetes); JSON, экспортированный из UI, с шаблонизированными `__inputs`, источник данных — по uid. Изменения дашбордов проходят ревью в PR, как код.
- Смешанные источники данных на одном дашборде: Mimir для панелей, Loki для панели логов ошибок, отфильтрованной по тому же `$application`, Tempo для панели поиска трейсов.

## Пример
Скелет разворачиваемого дашборда со строкой RED сервиса; JSON — это то, что экспортирует Grafana, урезанное до основных панелей.

```yaml
# grafana/provisioning/dashboards/dashboards.yaml
apiVersion: 1
providers:
  - name: esim
    folder: eSIM
    type: file
    allowUiUpdates: false
    options: { path: /etc/grafana/dashboards, foldersFromFilesStructure: true }
```

```json
{
  "uid": "esim-service-red",
  "title": "Service RED / USE",
  "templating": { "list": [
    { "name": "application", "type": "query", "datasource": { "uid": "mimir" },
      "query": "label_values(http_server_requests_seconds_count, application)" }
  ]},
  "panels": [
    { "type": "stat", "title": "Requests/s", "gridPos": { "x": 0, "y": 0, "w": 8, "h": 4 },
      "targets": [{ "expr": "sum(rate(http_server_requests_seconds_count{application=\"$application\"}[5m]))" }] },
    { "type": "stat", "title": "Error %", "gridPos": { "x": 8, "y": 0, "w": 8, "h": 4 },
      "fieldConfig": { "defaults": { "unit": "percentunit",
        "thresholds": { "steps": [ { "color": "green", "value": null }, { "color": "red", "value": 0.001 } ] } } },
      "targets": [{ "expr": "sum(rate(http_server_requests_seconds_count{application=\"$application\",status=~\"5..\"}[5m])) / sum(rate(http_server_requests_seconds_count{application=\"$application\"}[5m]))" }] },
    { "type": "stat", "title": "p99 latency", "gridPos": { "x": 16, "y": 0, "w": 8, "h": 4 },
      "fieldConfig": { "defaults": { "unit": "s",
        "thresholds": { "steps": [ { "color": "green", "value": null }, { "color": "red", "value": 0.3 } ] } } },
      "targets": [{ "expr": "histogram_quantile(0.99, sum by (le) (rate(http_server_requests_seconds_bucket{application=\"$application\"}[5m])))", "exemplar": true }] },
    { "type": "timeseries", "title": "p99 by route", "gridPos": { "x": 0, "y": 4, "w": 24, "h": 8 },
      "targets": [{ "expr": "histogram_quantile(0.99, sum by (le, uri) (rate(http_server_requests_seconds_bucket{application=\"$application\"}[5m])))", "legendFormat": "{{uri}}" }] },
    { "type": "timeseries", "title": "HikariCP active / pending", "gridPos": { "x": 0, "y": 12, "w": 12, "h": 8 },
      "targets": [
        { "expr": "hikaricp_connections_active{application=\"$application\"}", "legendFormat": "active {{instance}}" },
        { "expr": "hikaricp_connections_pending{application=\"$application\"}", "legendFormat": "pending {{instance}}" } ] },
    { "type": "timeseries", "title": "Kafka consumer lag", "gridPos": { "x": 12, "y": 12, "w": 12, "h": 8 },
      "targets": [{ "expr": "max by (client_id, topic) (kafka_consumer_fetch_manager_records_lag_max{application=\"$application\"})" }] }
  ]
}
```

## Подводные камни
- Дашборды, построенные на средних и сырых значениях счётчиков: `avg(latency)` прячет медленный хвост, а сырой счётчик — это лестница. Везде `rate()` и `histogram_quantile()`.
- `uri="/sims/8999..."` вместо шаблона маршрута: по одному ряду на SIM. Micrometer использует шаблон для маршрутов `@RequestMapping`, но для несопоставленных путей откатывается к `UNKNOWN`/сырому пути; задай `management.metrics.web.server.max-uri-tags` и отфильтруй 404.
- Дашборды, редактируемые только в UI: теряются при следующей пересборке Grafana, без ревью, без истории. Разворачивай из git.
- Красивый дашборд на 60 панелей, которым никто не пользуется во время инцидента; дежурному нужны три числа и ссылки на логи и трейсы.
- Связанные заметки: [[slo-sli-and-alerting]], [[observability-three-pillars]], [[otel-metric-types]], [[logging-stacks]], [[kubernetes-basics]] (метрики cAdvisor/kube-state для строки USE).
