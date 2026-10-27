---
title: Стеки логирования: Loki, ELK и EFK
tags: [observability, logging, loki, elasticsearch, grafana]
learned: 2026-10-22
---
## Кратко
Централизованный стек логирования состоит из трёх частей: **отправщика (shipper)** на каждом хосте или контейнере, который читает логи и пересылает их (Promtail, Fluent Bit, Filebeat, Logstash), **хранилища**, которое индексирует и хранит их (Loki, Elasticsearch, OpenSearch), и **UI** для поиска (Grafana, Kibana). **Loki + Promtail/Fluent Bit + Grafana** индексирует только метки и обходится дёшево; **ELK** (Elasticsearch, Logstash, Kibana) и **EFK** (Fluentd/Fluent Bit вместо Logstash) индексируют полный текст и дают богатый поиск за гораздо большие ресурсы. Логи — один из трёх сигналов наблюдаемости (observability) наряду с метриками и трейсами.

## Основное
### Зачем вообще нужен стек
- При нескольких репликах на нескольких узлах ([[docker-swarm]]) `docker logs` по одному контейнеру бесполезен; нужны все логи в одном месте, с поиском по сервису, уровню, trace id и времени, с хранением на дни или недели и с алертами по шаблонам ошибок.
- Приложения должны писать структурированные (JSON) логи в stdout; куда они попадут, решает платформа, а не приложение ([[docker-containers-and-resources]]). В Spring Boot 3.4+ есть встроенное структурированное логирование (`logging.structured.format.console=ecs` или `logstash`); в более ранних версиях используется `logstash-logback-encoder`.
- Корреляция: помещай `traceId`/`spanId` (Micrometer Tracing / OpenTelemetry) и бизнес-ключи (`subscriberId`, `orderId`) в MDC, чтобы они были в каждой строке; тогда UI позволяет перейти от трейса к его логам ([[spring-boot-properties-and-profiles]]).

### Loki + Promtail / Fluent Bit + Grafana
- **Loki** (Grafana Labs) — это «Prometheus для логов»: он индексирует только небольшой набор **меток** (`{app="esim-api", env="prod", level="error"}`), хранит строки логов сжатыми в чанках на объектном хранилище или диске и во время запроса делает grep по чанкам. Дёшев в эксплуатации (один бинарник, для небольших установок кластер не нужен), горизонтально масштабируется в режиме микросервисов, хранение ограничивается по времени.
- **LogQL**: `{app="esim-api"} |= "SM-DP+" | json | status >= 500` фильтрует по метке, затем по тексту, затем разбирает JSON и фильтрует по полю; метрические запросы вроде `sum by (level) (rate({app="esim-api"} | json | level="ERROR" [5m]))` питают алерты Grafana.
- **Promtail**: исходный агент Loki; читает файлы и Docker-сокет/journald, добавляет метки из метаданных контейнера, стадии конвейера (regex, json, timestamp, drop). С 2024 года устарел в пользу **Grafana Alloy** (дистрибутив OpenTelemetry Collector), конец жизни — март 2026; существующие конфиги продолжают работать, а компоненты Alloy `loki.source.*` эквивалентны.
- **Fluent Bit**: маленький (C, ~1 МБ) вендорно-нейтральный отправщик со входами (`tail`, `docker`, `systemd`), парсерами, фильтрами и выходами в Loki, Elasticsearch, Kafka, S3. Используй его, когда один агент должен кормить несколько получателей или важны ресурсы узла.
- **Grafana**: запрашивает Loki (Explore, дашборды, правила алертов) рядом с метриками Prometheus и трейсами Tempo, со ссылками лог <-> трейс по `traceId`.
- Где подходит: платформы Docker/Swarm/Kubernetes, команды, у которых уже есть Grafana, бюджеты, которые не потянут RAM для Elasticsearch. Слабое место: произвольный полнотекстовый поиск по огромным объёмам без хороших меток медленный, а метки с высокой кардинальностью (id пользователя как метка) его убивают.

### ELK: Elasticsearch, Logstash, Kibana
- **Elasticsearch**: распределённый поисковый движок (Lucene), который строит инвертированный индекс по каждому полю каждого документа лога; любое поле сразу доступно для поиска и агрегаций. Требует кучу JVM (рекомендуется половина RAM узла, до ~30 ГБ), быстрые диски, управление жизненным циклом индексов (hot/warm/cold, rollover, delete) и планирование шардов. В 2021 году лицензия сменилась на SSPL/Elastic License (в 2024 снова добавлена AGPL); **OpenSearch** — форк под Apache-2 (AWS) с тем же API для большинства задач.
- **Logstash**: JVM-сервер конвейеров: входы (Beats, Kafka, syslog), фильтры (разбор grok, mutate, geoip, date) и выходы (Elasticsearch). Мощный, но тяжёлый (JVM, сотни МБ), поэтому обычная топология — лёгкие **Beats** (Filebeat для файлов/контейнеров, Metricbeat) на хостах, отправляющие данные на несколько центральных узлов Logstash, или Filebeat напрямую в Elasticsearch с ingest pipelines. Буфер Kafka между отправщиком и Logstash поглощает всплески ([[kafka-architecture]]).
- **Kibana**: Discover (запросы KQL / Lucene), дашборды, алертинг, управление ILM.
- Где подходит: большие объёмы с требованиями к свободному поиску (безопасность, аудит, поддержка, ищущая номер телефона в любом поле), существующая экспертиза в Elastic. Цена: ресурсы и эксплуатация: здоровье кластера, количество шардов, взрыв маппинга (mapping explosion), когда поля JSON меняются.

### EFK: Elasticsearch, Fluentd / Fluent Bit, Kibana
- То же хранилище и UI, что в ELK, но вместо Logstash — **Fluentd** (Ruby/C, CNCF, экосистема плагинов из сотен входов/выходов, нативен для Kubernetes) или **Fluent Bit** (его лёгкий собрат, часто как DaemonSet/global-сервис, отправляющий данные в агрегатор Fluentd).
- Конфигурация Fluentd — маршрутизация по тегам (`<source>` -> `<filter>` -> `<match app.**>`); буферизация на диск с повторами даёт доставку at-least-once.
- Где подходит: кластеры Kubernetes (Fluentd/Fluent Bit там фактический стандарт сборщиков), маршрутизация в несколько получателей (Elasticsearch для поиска, S3 для архива, Kafka для обработки) одним агентом.

### Выбор
| | Стек Loki | ELK | EFK |
|---|---|---|---|
| Индекс | Только метки; grep во время запроса | Полный инвертированный индекс по всем полям | Как в ELK |
| Затраты ресурсов | Низкие | Высокие (куча JVM, диски) | Высокие на хранилище, лёгкие отправщики |
| Запросы | LogQL, сначала метки | KQL/Lucene, любое поле, агрегации | Как в ELK |
| Отправщик | Promtail/Alloy, Fluent Bit | Filebeat, Logstash | Fluentd, Fluent Bit |
| Интеграция с | Grafana, Prometheus, Tempo | Elastic APM, Beats | Экосистема Kubernetes |
| Хорош для | Платформ Docker/K8s, экономии, метрик и трейсов в Grafana | Полнотекстового поиска, аудита, зрелой аналитики | K8s с маршрутизацией в несколько получателей |
- В любом случае счёт определяют срок хранения и объём: отбрасывай debug-логи на отправщике, держи структурированных полей мало, и пусть они будут стабильными.

## Пример
```yaml
# compose.yaml: Loki + Promtail + Grafana для Docker-хоста; логи приложения — JSON в stdout
services:
  loki:
    image: grafana/loki:3.2.0
    command: -config.file=/etc/loki/local-config.yaml
    volumes: [loki-data:/loki]
    ports: ["127.0.0.1:3100:3100"]

  promtail:
    image: grafana/promtail:3.2.0
    command: -config.file=/etc/promtail/config.yml
    volumes:
      - ./promtail.yml:/etc/promtail/config.yml:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro      # обнаружение контейнеров (только чтение)
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
    depends_on: [loki]

  grafana:
    image: grafana/grafana:11.2.0
    environment: { GF_AUTH_ANONYMOUS_ENABLED: "false" }
    volumes: [grafana-data:/var/lib/grafana]
    ports: ["3000:3000"]

volumes: { loki-data: {}, grafana-data: {} }
```
```yaml
# promtail.yml: метка по сервису compose и разбор уровня из JSON, остальное остаётся строкой
server: { http_listen_port: 9080 }
positions: { filename: /tmp/positions.yaml }
clients: [{ url: http://loki:3100/loki/api/v1/push }]
scrape_configs:
  - job_name: docker
    docker_sd_configs: [{ host: unix:///var/run/docker.sock, refresh_interval: 10s }]
    relabel_configs:
      - source_labels: [__meta_docker_container_label_com_docker_compose_service]
        target_label: app
      - source_labels: [__meta_docker_container_label_env]
        target_label: env
    pipeline_stages:
      - json: { expressions: { level: level, traceId: traceId } }
      - labels: { level: }            # низкая кардинальность -> метка
      - drop: { source: level, expression: "DEBUG" }
```
LogQL в Grafana: `{app="esim-api", level="ERROR"} | json | line_format "{{.traceId}} {{.message}}"`.

## Подводные камни
- Метки с высокой кардинальностью в Loki (`traceId`, `userId`, `requestId` как метки) раздувают индекс и делают запросы черепашьими; держи их внутри строки и фильтруй через `| json | traceId="..."`.
- Взрыв маппинга в Elasticsearch: каждый новый ключ JSON становится полем; ограничь `index.mapping.total_fields.limit`, используй `dynamic: false` для blob-полезной нагрузки.
- Логи, записанные в файлы внутри контейнеров, не видны при обнаружении через Docker-сокет; логируй в stdout или монтируй каталог.
- Многострочные стеки вызовов разбиваются на отдельное событие на каждую строку, если отправщик не использует стадию `multiline` или приложение не выдаёт один JSON-объект на событие (исправление: логирование в JSON).
- Буферизация отправщика в памяти теряет логи при перезапуске; включи дисковые буферы (Fluent Bit `storage.type filesystem`, реестр Filebeat, файл positions в Promtail).
- Связанные заметки: [[docker-containers-and-resources]], [[docker-swarm]] (global-сервисы для отправщиков), [[kafka-architecture]] (буферизация логов), [[devops-resources]].
