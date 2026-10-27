---
title: Grafana dashboards — RED and USE methods for a service
tags: [observability, grafana, prometheus, dashboards, metrics]
learned: 2026-10-27
---
## TL;DR
Two checklists cover almost every dashboard. **RED** is for request-driven services: **R**ate (requests/s), **E**rrors (failed/s or %), **D**uration (latency percentiles). **USE** is for resources: **U**tilisation (% busy), **S**aturation (queue length, waiting), **E**rrors (hardware/driver errors). A service dashboard shows RED at the top for the service and per endpoint, then RED for each dependency it calls (DB, Kafka, SM-DP+), then USE for the resources it owns (JVM heap, threads, HikariCP pool, consumer lag). Dashboards are provisioned from JSON in git, use the same labels as the alerts ([[slo-sli-and-alerting]]), and link to Loki and Tempo by `traceId` ([[observability-three-pillars]]).

## Key points

### RED for request-driven services
| Letter | Question | PromQL (Spring Boot Micrometer names) |
|---|---|---|
| Rate | How much traffic | `sum(rate(http_server_requests_seconds_count{application="esim-provisioning"}[5m]))` |
| Errors | How much fails | `sum(rate(http_server_requests_seconds_count{status=~"5.."}[5m])) / sum(rate(http_server_requests_seconds_count[5m]))` |
| Duration | How slow | `histogram_quantile(0.99, sum by (le) (rate(http_server_requests_seconds_bucket[5m])))` |
- Split by `uri` (route template, not raw path) and `status` class; `method` only if it matters. Never by user or id ([[otel-metric-types]]).
- Kafka consumers are request-driven too: rate = records consumed/s (`spring_kafka_listener_seconds_count`), errors = failed records + DLT rate ([[dead-letter-queues-and-retries]]), duration = listener time; add **lag** (`kafka_consumer_fetch_manager_records_lag_max`) as the saturation signal ([[kafka-consumer]]).
- Dependencies get their own RED row: `http_client_requests_seconds_*` by `client.name`/`uri` for the SM-DP+ and billing calls, Resilience4j breaker state ([[circuit-breaker]]), `hikaricp_connections_acquire_seconds` for the database.

### USE for resources
| Resource | Utilisation | Saturation | Errors |
|---|---|---|---|
| CPU (container) | `rate(container_cpu_usage_seconds_total[5m]) / limit` | CPU throttling `container_cpu_cfs_throttled_periods_total` | — |
| JVM heap | `jvm_memory_used_bytes{area="heap"} / jvm_memory_max_bytes` | GC pause time `rate(jvm_gc_pause_seconds_sum[5m])` | OOM kills (restart count) ([[garbage-collection]]) |
| Threads / Tomcat | `tomcat_threads_busy_threads / tomcat_threads_config_max_threads` | request queue, `executor_queued_tasks` | rejected tasks ([[executors-and-thread-pools]]) |
| HikariCP | `hikaricp_connections_active / hikaricp_connections_max` | `hikaricp_connections_pending` | `hikaricp_connections_timeout_total` ([[hikaricp-pool-sizing]]) |
| Disk / volume | used %, `predict_linear` to full | IO wait | IO errors ([[linux-memory-filesystems-io]]) |
| Kafka consumer | — | lag | DLT rate |
- USE is where cause-level alerts come from (disk full in 4 h); RED is where symptom alerts come from.

### Layout of a service dashboard
1. Header row: variables `env`, `application`, `instance`; links to the runbook, the Loki explore for `{app="$application"}`, the Tempo search for `service.name="$application"`.
2. Row "Service RED": three stat panels (rate, error %, p99) with SLO thresholds coloured, then timeseries for the same split by `uri`.
3. Row "Dependencies": RED per downstream, breaker state as a state timeline.
4. Row "Resources (USE)": JVM, threads, pool, consumer lag.
5. Row "Business": domain counters (`esim_activation_total` by `result`), DLT depth, outbox backlog (`outbox_pending` gauge from the relay, [[outbox-pattern]]).
- One dashboard per service from a shared template (library panels / a Grafonnet or JSON template with `$application`), so every service looks the same and on-call does not learn ten layouts.
- Keep it under ~20 panels; a dashboard is for diagnosis, Explore is for ad-hoc.

### Panel craft
- Histograms, not averages, for latency; `histogram_quantile` over `sum by (le)` and a `[5m]` window that is at least 4x the scrape interval.
- `rate()` for counters, never the raw value; `increase()` for "how many in the last hour".
- Units set on every panel (seconds, percent 0–1, bytes); thresholds the same as the alert so the panel is red when the pager fires.
- Exemplars enabled on latency panels: click the dot, open the slow trace ([[distributed-tracing]]).
- Annotations from deploys (`APP_VERSION` change, or a webhook from CI) explain the step changes.

### Provisioning
- Dashboards and data sources as files under `grafana/provisioning/` (or the Grafana Operator in Kubernetes); JSON exported from the UI with `__inputs` templated, data source by uid. Review dashboard changes in PRs like code.
- Mixed data sources on one dashboard: Mimir for panels, Loki for an error-log panel filtered by the same `$application`, Tempo for a trace search panel.

## Example
Provisioned dashboard skeleton with the service RED row; the JSON is what Grafana exports, trimmed to the essential panels.

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

## Pitfalls
- Dashboards built from averages and raw counter values: `avg(latency)` hides the slow tail and a raw counter is a staircase. `rate()` and `histogram_quantile()` everywhere.
- `uri="/sims/8999..."` instead of the route template: one series per SIM. Micrometer uses the template for `@RequestMapping` routes but falls back to `UNKNOWN`/raw for unmatched paths; set `management.metrics.web.server.max-uri-tags` and filter 404s.
- Dashboards edited in the UI only: lost on the next Grafana rebuild, no review, no history. Provision from git.
- A beautiful 60-panel dashboard that nobody uses during an incident; the on-call wants three numbers and links to logs and traces.
- Related: [[slo-sli-and-alerting]], [[observability-three-pillars]], [[otel-metric-types]], [[logging-stacks]], [[kubernetes-basics]] (cAdvisor/kube-state metrics for the USE row).
