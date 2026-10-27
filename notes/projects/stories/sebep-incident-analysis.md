---
title: Project story — Sebep, AI-powered incident analysis
tags: [project-story, interview, observability, sentry, llm]
learned: 2026-10-27
---
## TL;DR
"Sebep" (Kyrgyz for "cause") is an internal tool I built at Beeline that turns a Sentry alert into a root-cause explanation in Telegram within a minute: Sentry webhook → Spring Boot bridge → Python pipeline that collects the stack trace and the related GitLab code → Claude writes the explanation → Telegram. Alongside it I set up the observability stack (OTel Collector, Mimir, Grafana, Loki, Tempo) and got management and information security to approve sending code context to an LLM.

## Context and goal
- Before: a production error meant 15–40 minutes of a developer digging through Sentry, commits and code before anyone knew whether it was serious.
- Goal: for each new or regressed Sentry issue, post a short Telegram message with the probable cause, the suspicious lines and the commit that introduced them, so triage starts from a hypothesis.
- Scope: [~12] <!-- fill in real number --> backend services, self-hosted Sentry, GitLab; I was the only developer on Sebep for most of it, with a [~2-month] <!-- fill in real number --> side-project budget.

## Architecture
- Self-hosted Sentry (its own Kafka, ClickHouse, Redis, Postgres) emits issue webhooks.
- Spring Boot bridge: verifies the HMAC signature with the Sentry client secret, deduplicates and debounces (one issue id → at most one analysis per window), persists the request in Postgres, and publishes a job to an internal queue.
- Python FastAPI pipeline: fetches the event and stack trace via the Sentry API, maps frames to files in GitLab, pulls the relevant snippets and recent commits touching them (`git blame` through the GitLab API), builds a bounded prompt and calls Claude.
- Result posted to Telegram with a link back to Sentry and GitLab; feedback buttons ("useful / wrong") stored for quality tracking.
- Observability stack for all services: OTel Collector → Mimir (metrics), Loki (logs), Tempo (traces), Grafana dashboards and alerts ([[logging-stacks]], [[otel-metric-types]], [[distributed-tracing]]).
- Everything in Docker Compose on internal VMs behind a corporate FortiGate proxy ([[docker-fundamentals]]).

```
Sentry ──webhook (HMAC)──▶ Bridge (Spring) ──job──▶ Pipeline (FastAPI)
                              │ debounce,                 ├─▶ Sentry API (event, frames)
                              │ Postgres                  ├─▶ GitLab API (code, blame)
                              ▼                           ├─▶ Claude (root-cause draft)
                          Grafana / Loki / Tempo ◀─OTel──┴─▶ Telegram
```

## My role and decisions
- Proposed the project, wrote the one-page design, and presented it to management and information security. Approval hinged on data minimisation: only stack frames, ±30 lines around them and commit metadata; no configs, secrets or customer data; a file allow-list and a regex scrubber for tokens and MSISDNs before anything leaves the network.
- Chose a two-language split on purpose: Spring for the parts that must be robust and audited (signature check, persistence, retry), Python for the parts that iterate fast (prompting, parsing).
- Built the Grafana dashboards and alert rules the team uses daily; Sebep itself reports metrics (analyses per hour, LLM latency, token count, feedback ratio).

## Hardest problem and how I solved it
The first week in production was dominated by three practical issues rather than AI quality.
1. **Alert storms.** A Kafka consumer bug produced thousands of identical Sentry events and the bridge queued an analysis for each. I added a two-level debounce: an in-memory window keyed by issue id plus a Postgres row with `last_analysed_at` and a unique constraint, so even two bridge instances cannot double-analyse. Only "new issue" and "regression" webhook actions trigger now; plain "event" actions are ignored.
2. **Corporate TLS inspection.** The FortiGate proxy re-signs outbound TLS, so Python failed with certificate errors and Java with `PKIX path building failed`. The fix was importing the corporate CA into both trust stores at image build time, not disabling verification. Secrets moved from `.env` files into Docker secrets.
3. **Hallucinated causes.** Claude sometimes blamed a file not in the trace. I constrained the prompt: the model must quote the frame and line it relies on, may answer "insufficient context", and the pipeline rejects answers referencing a path outside the supplied snippets. Prompt size is capped by frame depth and a token budget. The feedback buttons give a measurable "useful" rate of [~70%] <!-- fill in real number -->.
Operating self-hosted Sentry was its own job: ClickHouse ran out of disk once and its Kafka consumers lagged after a restart; I added retention settings and Grafana alerts on both.

## What I would do differently
- Build an evaluation set of past incidents with known causes before tuning prompts by feel.
- Put a queue between bridge and pipeline from the start instead of HTTP with retries ([[rabbitmq-basics]]).
- Write the data-handling policy before the demo; the approval round-trip cost more time than the code.
- Reconsider two runtimes: one Spring service with a Java LLM client would be simpler to deploy and monitor.

## Likely interview questions
- How do you verify the webhook is really from Sentry? HMAC-SHA256 of the raw body with the shared secret compared in constant time to the `sentry-hook-signature` header.
- How do you handle 1000 alerts in a minute? Trigger only on new/regressed issues, in-memory window plus a DB unique constraint per issue and cooldown, global rate limit.
- How did you get security approval for sending code to an LLM? Data minimisation, scrubbing, allow-list, no customer data, written policy, demo with real anonymised cases.
- How do you limit hallucinations? Force citations to supplied frames, allow "insufficient context", reject answers referencing unknown files, measure with feedback.
- Why Spring and Python together? Robust audited edge in Java; fast iteration on prompts in Python; each runs with its own OTel instrumentation.
- What broke in production? TLS-inspecting proxy, secret handling, Sentry's own Kafka/ClickHouse disk and lag.
- How did you measure value? Time from alert to first hypothesis, feedback ratio, number of analyses that pointed at the right commit.
- What is in your observability stack and why not Elastic? OTel Collector to Mimir/Loki/Tempo, Grafana on top; one query language family, cheaper storage, and traces linked to logs by trace id.

## Related notes
- [[logging-stacks]], [[otel-metric-types]], [[distributed-tracing]]
- [[idempotency]], [[rest-and-http]], [[spring-security-basics]]
- [[docker-fundamentals]], [[kafka-consumer]], [[circuit-breaker]]
