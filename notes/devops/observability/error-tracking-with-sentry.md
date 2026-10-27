---
title: Error tracking with Sentry — events, issues, sampling, self-hosting
tags: [observability, sentry, spring-boot, incidents]
learned: 2026-10-27
---
## TL;DR
Sentry captures **events** (an exception with stack trace, breadcrumbs, request context, user, tags) and groups them into **issues** by fingerprint, so 10 000 identical `NullPointerException`s are one issue with a count, a first/last seen, and a regression state. It fills the gap between logs (too noisy to watch) and metrics (no detail): a new issue or a regression is a push notification with the exact frame. In Spring Boot it is `sentry-spring-boot-starter-jakarta` plus a DSN; it also joins the trace by sharing the OpenTelemetry `traceId`. Self-hosting is a real workload (Kafka, ClickHouse, Redis, Postgres, Symbolicator); the operational rules are retention, sampling and PII scrubbing. The Sentry → Telegram bridge with LLM root-cause drafts is [[sebep-incident-analysis]].

## Key points

### Concepts
| Term | Meaning |
|---|---|
| Event | One captured error or message with context (stack, breadcrumbs, tags, user, release, environment) |
| Issue | Group of events with the same **fingerprint** (by default: stack frames + exception type); has state new / unresolved / resolved / ignored / regressed |
| Breadcrumbs | Last N log lines, HTTP calls, DB queries before the error, attached to the event |
| Release | Version (`APP_VERSION`, git SHA) on every event: "first seen in 1.42.0", regressions after a resolve |
| Environment | `prod` / `staging` separation for alert rules |
| Alert rule | Issue alerts (new issue, regression, > N events in M minutes) and metric alerts (error rate) → email, Slack, Telegram, webhook |
- Fingerprinting decides noise level: a message with an id inside (`"order 42 not found"`) produces one issue per order unless the message is parameterised or a custom fingerprint is set (`event.setFingerprint(List.of("order-not-found"))`).
- Unhandled exceptions reaching the Spring MVC `ExceptionHandler` are captured automatically (`sentry.exception-resolver-order`); handled business exceptions you map to 4xx should not be sent (`ignored-exceptions-for-type`).

### Spring Boot integration
- Dependencies: `sentry-spring-boot-starter-jakarta` (+ `sentry-logback` to turn `ERROR` logs into events and `WARN`/`INFO` into breadcrumbs). Config in `application.yaml`: `sentry.dsn`, `environment`, `release`, `traces-sample-rate`, `send-default-pii`.
- Context: `Sentry.configureScope(s -> s.setTag("msisdn_hash", h))`, `setUser` with an internal id only, `Sentry.addBreadcrumb(...)`. MDC values are attached automatically by the Logback integration.
- Tracing: `sentry-opentelemetry-agent` or the OTel integration lets Sentry performance spans share `traceId` with Tempo; with `traces-sample-rate` low (0.05) Sentry's own performance product stays cheap and you still get the link ([[distributed-tracing]]).
- Kafka listeners and `@Scheduled` jobs: exceptions do not pass through MVC, so capture explicitly in the error handler or rely on `sentry-logback` catching the `ERROR` log from `DefaultErrorHandler` ([[spring-kafka-consumer]]).

### Sampling and quotas
- `sample-rate` (errors) default 1.0; keep it unless an error storm costs you quota. Better: `before-send` callback that drops known-noisy exceptions and rate limits by fingerprint in the app.
- Server side: per-project rate limits and spike protection stop a storm from consuming the retention disk; self-hosted has no billing but has disk.
- An error storm (a consumer bug producing thousands of events per minute) is the normal case, not the exception; design the alerting for it: alert on *new* issue and *regression*, not on every event ([[sebep-incident-analysis]] debounce story).

### PII and data scrubbing
- `send-default-pii: false` (default) drops cookies, request bodies and user IPs. The server-side **data scrubber** masks fields named `password`, `secret`, `token`, card-number-shaped values; add custom `[Mm]sisdn|iccid|eid` patterns in project settings.
- `before-send` in the app can strip request headers (`Authorization`) and breadcrumb data before anything leaves the service. Treat Sentry as a log sink from the compliance point of view ([[owasp-top-10-for-backend]], [[secrets-management]]).
- Source context: Sentry shows ±5 lines of code around the frame only if source bundles are uploaded (`sentry-maven-plugin` / `sentry-cli`); the JVM stack trace alone has file and line, not text.

### Self-hosted operations
- Docker Compose from `getsentry/self-hosted`: web, workers, cron, relay, Snuba, **Kafka** (event pipeline), **ClickHouse** (event storage/search), **Redis**, **Postgres** (issues, users, metadata), Symbolicator, nginx. 16 GB RAM minimum in practice, more with traffic.
- Retention: `SENTRY_EVENT_RETENTION_DAYS` (default 90) drives the cleanup cron; ClickHouse disk is the first thing that fills. Watch `sentry.yml`/`config.yml` quotas and ClickHouse free space.
- Upgrades run `install.sh` with migrations; back up Postgres and ClickHouse volumes first. Kafka consumer lag inside Sentry (Snuba consumers) after a restart shows as "events arrive 20 minutes late"; monitor the Sentry stack with the same Grafana as everything else ([[grafana-dashboards-and-red-use]], [[logging-stacks]]).
- Outbound integrations (Telegram, GitLab) go through the corporate proxy; the TLS-inspecting proxy needs its CA in Sentry's containers too.

### Sentry vs logs vs metrics
- Logs: everything, searchable, no grouping. Metrics: counts, no detail. Sentry: deduplicated exceptions with context and workflow (assign, resolve, regress). Use all three; alert on new/regressed issues from Sentry, on rates from metrics, dig in Loki ([[observability-three-pillars]]).

## Example
Spring Boot configuration with scrubbing and noise control, plus a `before-send` callback that drops expected exceptions and masks subscriber data.

```yaml
sentry:
  dsn: ${SENTRY_DSN}
  environment: ${ENV:dev}
  release: esim-provisioning@${APP_VERSION:dev}
  send-default-pii: false
  traces-sample-rate: 0.05                 # performance spans; errors are still 100 %
  max-breadcrumbs: 50
  in-app-includes: [kg.beeline.esim]       # frames from our code are "in app" for grouping and display
  ignored-exceptions-for-type:
    - kg.beeline.esim.domain.ProfileNotFoundException   # maps to 404, not an incident
  logging:
    minimum-event-level: error
    minimum-breadcrumb-level: info
```

```java
@Configuration
class SentryConfig {
    private static final Pattern MSISDN = Pattern.compile("996\\d{9}");

    @Bean
    SentryOptions.BeforeSendCallback beforeSend() {
        return (event, hint) -> {
            Throwable t = event.getThrowable();
            if (t instanceof CallNotPermittedException) return null;            // breaker open: metric alert covers it
            if (event.getRequest() != null) event.getRequest().setHeaders(Map.of());   // no Authorization leaks
            if (event.getMessage() != null && event.getMessage().getFormatted() != null) {
                event.getMessage().setFormatted(MSISDN.matcher(event.getMessage().getFormatted()).replaceAll("996*********"));
            }
            event.setTag("component", "provisioning");
            return event;
        };
    }
}
```

## Pitfalls
- Logging `log.error("failed for {}", msisdn, e)` in a Kafka retry loop: each retry is an event, and the MSISDN lands in Sentry; log once at the recoverer and scrub.
- Every handled 4xx captured as an error: thousands of "validation failed" issues bury the real one. Capture unexpected exceptions only.
- Release not set: "regressed" never works and you cannot tell which deploy broke it. Set `release` from the build and upload source bundles.
- Self-hosted Sentry with no retention or disk alerts: ClickHouse fills the volume and the whole stack stops ingesting silently.
- Related: [[sebep-incident-analysis]], [[observability-three-pillars]], [[slo-sli-and-alerting]], [[exceptions]] (what to throw), [[docker-fundamentals]].
