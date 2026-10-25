---
title: Logging stacks: Loki, ELK and EFK
tags: [observability, logging, loki, elasticsearch, grafana]
learned: 2026-10-22
---
## TL;DR
A centralised logging stack has three parts: a **shipper** on every host or container that tails logs and forwards them (Promtail, Fluent Bit, Filebeat, Logstash), a **store** that indexes and keeps them (Loki, Elasticsearch, OpenSearch), and a **UI** to search them (Grafana, Kibana). **Loki + Promtail/Fluent Bit + Grafana** indexes only labels and is cheap; **ELK** (Elasticsearch, Logstash, Kibana) and **EFK** (Fluentd/Fluent Bit instead of Logstash) index full text and give rich search at a much higher resource cost. Logs are one of the three observability signals next to metrics and traces.

## Key points
### Why a stack at all
- With several replicas across several nodes ([[docker-swarm]]) `docker logs` on one container is useless; you need all logs in one place, searchable by service, level, trace id and time, retained for days or weeks, with alerts on error patterns.
- Applications should write structured (JSON) logs to stdout; the platform, not the app, decides where they go ([[docker-containers-and-resources]]). Spring Boot 3.4+ has built-in structured logging (`logging.structured.format.console=ecs` or `logstash`); earlier versions use `logstash-logback-encoder`.
- Correlation: put `traceId`/`spanId` (Micrometer Tracing / OpenTelemetry) and business keys (`subscriberId`, `orderId`) in MDC so every line carries them; the UI then jumps from a trace to its logs ([[spring-boot-properties-and-profiles]]).

### Loki + Promtail / Fluent Bit + Grafana
- **Loki** (Grafana Labs) is "Prometheus for logs": it indexes only a small set of **labels** (`{app="esim-api", env="prod", level="error"}`), stores the log lines compressed in chunks on object storage or disk, and greps the chunks at query time. Cheap to run (one binary, no cluster for small setups), horizontally scalable in microservices mode, retention by time.
- **LogQL**: `{app="esim-api"} |= "SM-DP+" | json | status >= 500` filters by label, then by text, then parses JSON and filters by field; metric queries like `sum by (level) (rate({app="esim-api"} | json | level="ERROR" [5m]))` feed Grafana alerts.
- **Promtail**: the original Loki agent; tails files and the Docker socket/journald, adds labels from the container metadata, pipeline stages (regex, json, timestamp, drop). Deprecated in favour of **Grafana Alloy** (OpenTelemetry Collector distribution) since 2024, end of life March 2026; existing configs still work and Alloy's `loki.source.*` components are equivalent.
- **Fluent Bit**: a small (C, ~1 MB) vendor-neutral shipper with inputs (`tail`, `docker`, `systemd`), parsers, filters and outputs to Loki, Elasticsearch, Kafka, S3. Use it when one agent must feed several destinations or node resources matter.
- **Grafana**: queries Loki (Explore, dashboards, alert rules) next to Prometheus metrics and Tempo traces, with log <-> trace links by `traceId`.
- Fit: Docker/Swarm/Kubernetes platforms, teams that already run Grafana, budgets that cannot afford Elasticsearch's RAM. Weak spot: ad-hoc full-text search across huge volumes without good labels is slow, and high-cardinality labels (user id as a label) kill it.

### ELK: Elasticsearch, Logstash, Kibana
- **Elasticsearch**: a distributed search engine (Lucene) that builds an inverted index over every field of every log document; any field is searchable and aggregatable immediately. Needs JVM heap (recommended half of node RAM, up to ~30 GB), fast disks, index lifecycle management (hot/warm/cold, rollover, delete), and shard planning. Licence changed to SSPL/Elastic License in 2021 (AGPL added again in 2024); **OpenSearch** is the Apache-2 fork (AWS) with the same API for most purposes.
- **Logstash**: a JVM pipeline server: inputs (Beats, Kafka, syslog), filters (grok parsing, mutate, geoip, date) and outputs (Elasticsearch). Powerful but heavy (JVM, hundreds of MB), so the usual topology is lightweight **Beats** (Filebeat for files/containers, Metricbeat) on hosts shipping to a few central Logstash nodes, or Filebeat straight to Elasticsearch with ingest pipelines. A Kafka buffer between shipper and Logstash absorbs bursts ([[kafka-architecture]]).
- **Kibana**: Discover (KQL / Lucene queries), dashboards, alerting, ILM management.
- Fit: large volumes with free-form search requirements (security, audit, support looking for a phone number in any field), existing Elastic expertise. Cost: resources and operations: cluster health, shard counts, mapping explosions when JSON fields vary.

### EFK: Elasticsearch, Fluentd / Fluent Bit, Kibana
- Same store and UI as ELK with **Fluentd** (Ruby/C, CNCF, plugin ecosystem of hundreds of inputs/outputs, Kubernetes-native) or **Fluent Bit** (its lightweight sibling, often as a DaemonSet/global service shipping to a Fluentd aggregator) replacing Logstash.
- Fluentd config is tag-based routing (`<source>` -> `<filter>` -> `<match app.**>`); disk buffering with retry gives at-least-once delivery.
- Fit: Kubernetes clusters (Fluentd/Fluent Bit are the de facto collectors there), multi-destination routing (Elasticsearch for search, S3 for archive, Kafka for processing) with one agent.

### Choosing
| | Loki stack | ELK | EFK |
|---|---|---|---|
| Index | Labels only; grep at query time | Full inverted index on all fields | Same as ELK |
| Resource cost | Low | High (JVM heap, disks) | High store, light shippers |
| Query | LogQL, label-first | KQL/Lucene, any field, aggregations | Same as ELK |
| Shipper | Promtail/Alloy, Fluent Bit | Filebeat, Logstash | Fluentd, Fluent Bit |
| Integrates with | Grafana, Prometheus, Tempo | Elastic APM, Beats | Kubernetes ecosystem |
| Good for | Docker/K8s platforms, cost-sensitive, metrics+traces in Grafana | Full-text search, audit, mature analytics | K8s with multi-destination routing |
- Retention and volume decide the bill either way: drop debug logs at the shipper, keep structured fields few and stable.

## Example
```yaml
# compose.yaml: Loki + Promtail + Grafana for a Docker host; app logs are JSON on stdout
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
      - /var/run/docker.sock:/var/run/docker.sock:ro      # discover containers (read-only use)
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
# promtail.yml: label by compose service and parse the JSON level, keep the rest as the line
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
      - labels: { level: }            # low cardinality -> label
      - drop: { source: level, expression: "DEBUG" }
```
LogQL in Grafana: `{app="esim-api", level="ERROR"} | json | line_format "{{.traceId}} {{.message}}"`.

## Pitfalls
- High-cardinality labels in Loki (`traceId`, `userId`, `requestId` as labels) explode the index and make queries crawl; keep them inside the line and filter with `| json | traceId="..."`.
- Elasticsearch mapping explosion: every new JSON key becomes a field; cap `index.mapping.total_fields.limit`, use `dynamic: false` for payload blobs.
- Logs written to files inside containers are invisible to Docker-socket discovery; log to stdout or mount the directory.
- Multi-line stack traces split into one event per line unless the shipper uses a `multiline` stage or the app emits one JSON object per event (the fix: JSON logging).
- Shipper buffering to memory loses logs on restart; enable disk buffers (Fluent Bit `storage.type filesystem`, Filebeat registry, Promtail positions file).
- Related: [[docker-containers-and-resources]], [[docker-swarm]] (global services for shippers), [[kafka-architecture]] (log buffering), [[devops-resources]].
