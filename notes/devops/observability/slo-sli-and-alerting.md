---
title: SLOs, SLIs, error budgets and alerting that does not wake you for nothing
tags: [observability, sre, alerting, grafana, prometheus]
learned: 2026-10-27
---
## TL;DR
An **SLI** is a measured ratio of good events to all events (successful requests / all requests; requests under 300 ms / all requests). An **SLO** is the target for that ratio over a window (99.9 % over 30 days). The **error budget** is what is left (0.1 % of requests, roughly 43 minutes of full outage per month) and is spent on incidents, risky deploys and experiments. Alerts should fire on **symptoms** users feel (budget burning too fast, availability dropping), not on causes (CPU 80 %, one pod restarted); cause-level signals belong on dashboards. A page must be actionable, rare and urgent; everything else is a ticket.

## Key points

### Vocabulary
| Term | Meaning | Example |
|---|---|---|
| SLI | Service level indicator, a measured ratio | `sum(rate(http_requests_total{status!~"5.."}[5m])) / sum(rate(http_requests_total[5m]))` |
| SLO | Target for the SLI over a window | 99.9 % of activation requests succeed over 30 days |
| SLA | Contract with consequences (credits) | 99.5 % monthly, defined looser than the SLO |
| Error budget | `1 - SLO` of the window's events | 0.1 % of ~30 M requests = 30 k failed requests per month |
| Burn rate | How fast the budget is spent relative to "exactly on target" | Burn 1 = budget lasts the full window; burn 14.4 = gone in ~2 days |
- Pick SLIs from the user's point of view: availability (non-5xx), latency (under a threshold), correctness (activation really happened, measured by a probe), freshness for pipelines (lag under N minutes, [[kafka-consumer]]).
- Measure at the edge (gateway, [[load-balancers-l4-vs-l7|load balancer]]) or by synthetic probe, not inside the service that might be the thing that is down.

### Error budgets as a decision tool
- Budget healthy: ship features, run chaos tests, deploy on Friday. Budget burning or exhausted: freeze risky changes, prioritise reliability work. This turns "is the system reliable enough" from an argument into a number both developers and product accept.
- 100 % is not a target: each extra nine costs roughly 10x and every dependency (cloud, DNS, the SM-DP+ partner) caps you anyway.

### Symptom-based alerting and multi-window burn rate
- Cause alerts (`cpu > 80%`, `pod restarted`, `disk 70%`) fire often, are frequently harmless, and train people to ignore pages. Symptom alerts (`error budget burn rate > 14.4 over 1 h AND over 5 m`) fire when users are affected and stop firing when they are not.
- **Multi-window, multi-burn-rate** (Google SRE workbook): page when burn rate is 14.4 over 1 h (and still over 5 m, to avoid flapping), page when 6 over 6 h, ticket when 1 over 3 days. The short window confirms it is still happening; the long window gives the severity.
- Keep a few cause-level alerts where the symptom arrives too late: disk will fill in 4 h (`predict_linear`), certificate expires in 7 days, Kafka consumer lag growing, Postgres replication slot not consumed ([[outbox-pattern]]), DLT receiving messages ([[dead-letter-queues-and-retries]]).

### Alert hygiene
- Every alert has: a severity (page vs ticket), an owner, a runbook link, and a dashboard link. If nobody can say what to do when it fires, delete it.
- Labels `severity`, `service`, `team` route it: pages to on-call (Telegram/PagerDuty), tickets to a channel. The Sentry→Telegram bridge is the same idea for exceptions ([[error-tracking-with-sentry]], [[sebep-incident-analysis]]).
- `for:` duration and `keep_firing_for` stop flapping; grouping (`group_by: [service]`) stops 40 pods producing 40 pages; inhibition suppresses child alerts when the parent fires (whole cluster down → do not page per service).
- Review monthly: alerts that fired and nobody acted → delete or demote; incidents with no alert → add a symptom alert.

### Where it runs in the Grafana stack
- Grafana unified alerting evaluates PromQL against Mimir (and LogQL against Loki) on a schedule, groups through its built-in Alertmanager, and notifies via contact points (Telegram, email, webhook, OnCall). Alert rules can be provisioned as YAML files or through the API, so they live in git next to the dashboards ([[grafana-dashboards-and-red-use]]).
- Recording rules precompute SLI ratios (`sli:activation:success_ratio_5m`) so alert queries and dashboards stay cheap.

## Example
Grafana-provisioned alert rule: a fast-burn page for the activation API's 99.9 % availability SLO, using the 1 h / 5 m window pair, with a runbook annotation.

```yaml
# grafana/provisioning/alerting/esim-slo.yaml
apiVersion: 1
groups:
  - orgId: 1
    name: esim-slo
    folder: eSIM
    interval: 1m
    rules:
      - uid: esim-activation-fast-burn
        title: eSIM activation availability: fast error-budget burn
        condition: C
        for: 2m
        labels: { severity: page, service: esim-provisioning, team: esim-backend }
        annotations:
          summary: "Activation API burning error budget at >14.4x (1h and 5m windows)"
          runbook_url: https://wiki.internal/runbooks/esim-activation-availability
          dashboard_uid: esim-service-red
        data:
          - refId: A                                   # 1h burn rate
            datasourceUid: mimir
            relativeTimeRange: { from: 3600, to: 0 }
            model:
              expr: |
                (1 - (
                  sum(rate(http_server_requests_seconds_count{application="esim-provisioning",uri="/sims/{id}/activate",status!~"5.."}[1h]))
                  / sum(rate(http_server_requests_seconds_count{application="esim-provisioning",uri="/sims/{id}/activate"}[1h]))
                )) / (1 - 0.999)
              instant: true
          - refId: B                                   # 5m burn rate, must also be high (still happening)
            datasourceUid: mimir
            relativeTimeRange: { from: 300, to: 0 }
            model:
              expr: |
                (1 - (
                  sum(rate(http_server_requests_seconds_count{application="esim-provisioning",uri="/sims/{id}/activate",status!~"5.."}[5m]))
                  / sum(rate(http_server_requests_seconds_count{application="esim-provisioning",uri="/sims/{id}/activate"}[5m]))
                )) / (1 - 0.999)
              instant: true
          - refId: C
            datasourceUid: __expr__
            model:
              type: math
              expression: "$A > 14.4 && $B > 14.4"
```

Ticket-level rule: same queries with `[6h]`/`[30m]` windows and threshold 6, `severity: ticket`. Over a 30-day window a burn of 14.4 sustained for 1 h consumes 2 % of the month's budget, which is the usual threshold for waking somebody.

## Pitfalls
- SLO on a metric the service reports about itself: when the pod is down it reports nothing and the ratio looks perfect. Use the gateway's metrics or a blackbox probe, and alert on `absent()`.
- Averages as SLIs (`avg latency < 200 ms`): one user waiting 10 s is invisible. Use ratios of requests under the threshold, from histograms ([[otel-metric-types]]).
- Alerting on every pod restart, every 5xx, every GC pause: the pager becomes noise and the real outage is missed. Promote to pages only what has a runbook and a user impact.
- An SLO nobody reviews is a dashboard. Put the budget in the sprint review.
- Related: [[grafana-dashboards-and-red-use]], [[observability-three-pillars]], [[requirements-and-constraints]] (SLOs as non-functional requirements), [[failure-modes-and-timeouts]], [[leases-heartbeats-and-health]].
