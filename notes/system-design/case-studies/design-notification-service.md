---
title: Design a notification service
tags: [system-design, case-study, kafka, notifications, reliability]
learned: 2026-10-27
---
## TL;DR
A notification service accepts "tell user X about Y" and delivers it over push, SMS and email through third-party providers with different speeds and failure modes, without duplicates and without losing requests when a provider is down. The design is an API that persists and acknowledges fast, a Kafka log that absorbs peaks, per-channel worker groups with their own retries and circuit breakers, and idempotency at three points: client request, consumer, provider call. The hard part is the delivery guarantee: honest at-least-once plus deduplication, never "exactly-once". Building blocks are described in [[message-queues-and-streams]], [[rate-limiting]], [[caching]]; general fan-out patterns in [[notifications-and-push]].

## Key points

### Requirements and estimates
- Functional: send to a user over one or more channels; templates with parameters; user preferences and quiet hours; delivery status per channel; bulk campaigns.
- Non-functional: no duplicate SMS (they cost money and annoy); no lost notifications; OTP must arrive in seconds, marketing can wait minutes; provider outages must not fill the API with errors.
- Numbers (telecom, 5 M subscribers): 20 M notifications/day ≈ 230/s average, campaign peak 2 000/s. Row of ~1 KB → 20 GB/day; keep 90 days hot (~2 TB), archive older. Provider latency 100 ms–5 s; SMS provider throughput capped at, say, 500/s.

### API
```
POST /v1/notifications          Idempotency-Key: <uuid>
  { userId, template, params, channels: [PUSH, SMS], priority: HIGH|NORMAL|BULK, scheduleAt? }
  -> 202 { notificationId }                      (same key again -> same 202, same id)
GET  /v1/notifications/{id}     -> { status per channel, attempts, lastError }
PUT  /v1/users/{id}/preferences -> { channels, quietHours, locale }
POST /v1/providers/{p}/receipts  (webhook from provider: DELIVERED / FAILED)
```
- `Idempotency-Key` is stored with the notification; a retry returns the existing id ([[idempotency]], [[api-design-for-system-design]]).
- Priority is a separate topic or partition set so campaigns cannot delay OTPs.

### Data model
- `notification(id, user_id, template, params jsonb, priority, created_at, idempotency_key unique)`
- `delivery(notification_id, channel, status, attempts, provider_msg_id, last_error, updated_at)` — primary key `(notification_id, channel)`.
- `user_preference(user_id, channels, quiet_hours, locale)` — cached in Redis with TTL.
- `outbox` for publishing `NotificationRequested` ([[outbox-pattern]]); `processed_event(event_id, processed_at)` on the consumer side.
- Postgres for all of it; partition `notification` and `delivery` by month for retention ([[postgres-partitioning]]).

### High-level design
```
clients ──▶ API (stateless, N pods) ──tx: notification + outbox──▶ Postgres
                                                                   │ relay
                                                                   ▼
                              Kafka notifications.requested (key = userId, priority topics)
                                                                   │
                        router: prefs (Redis cache) + quiet hours + template render
                          ├─▶ notifications.push ──▶ push workers ──▶ FCM/APNs
                          ├─▶ notifications.sms  ──▶ sms workers  ──▶ SMS gateway (rate-limited)
                          └─▶ notifications.email ─▶ email workers ─▶ SMTP/SES
                                      each: own consumer group, retry topics, DLQ, breaker
provider receipts ──webhook──▶ API ──▶ notifications.receipts ──▶ status updater ──▶ delivery
```

### Deep dive: delivery guarantee without duplicates
- Three places where duplicates arise: the client retries the POST (network), the consumer redelivers after a crash between "sent" and "offset committed", and the provider times out after actually sending.
- Three dedup points: `idempotency_key` unique index on the API; `processed_event` row inserted in the same transaction as the `delivery` update on the consumer; provider-side idempotency (FCM/APNs accept a message id, many SMS gateways accept a client reference). Where the provider has none, use a short-lived "sending" state: mark `SENDING` before the call, and on redelivery of a `SENDING` row with a recent timestamp, query the provider status instead of resending.
- Ordering: key by `userId` so a user's "code is 1234" and "code expired" arrive in order ([[kafka-architecture]]).
- Retries: transient provider errors go through retry topics with backoff (2 s, 10 s, 60 s); permanent errors (invalid number, unsubscribed) go to the DLQ and mark `FAILED` ([[dead-letter-queues-and-retries]]). A per-provider [[circuit-breaker]] opens on error rate and the worker pauses its partitions instead of burning retries.
- Provider throttling: token bucket per provider in Redis; workers acquire a token before sending, so 2 000/s campaign traffic drains at the contracted 500/s without 429 storms ([[rate-limiting]], [[redis-patterns-and-persistence]]).
- Observability: lag per consumer group, send latency per provider, DLQ rate, end-to-end "requested → delivered" histogram; trace id from API to provider call ([[distributed-tracing]], [[slo-sli-and-alerting]]).

### Trade-offs
| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Queue | Kafka log, per-channel topics | RabbitMQ per-channel queues | replay, ordering per user, campaign fan-out; Rabbit is fine at lower scale |
| Status storage | Postgres partitioned | Cassandra | query by user and id, modest write rate; wide-column when > 50k writes/s ([[storage-choices]]) |
| Priority | separate topics | priority field in one topic | Kafka has no priority; OTP must never queue behind bulk |
| Template rendering | at router | at API | preferences and locale resolved once, closer to send time |

## Example
Consumer-side dedup and the "sending" guard in one transaction:

```java
@Transactional
public void dispatchSms(String eventId, SmsRequested msg) {
    if (processed.existsById(eventId)) return;                       // Kafka redelivery
    Delivery d = deliveries.lockByKey(msg.notificationId(), SMS);    // SELECT ... FOR UPDATE
    if (d.status() == SENDING && d.updatedAt().isAfter(now().minusSeconds(60))) {
        d.apply(provider.status(d.providerMsgId()));                 // timed out earlier: ask, do not resend
    } else if (d.status() == PENDING) {
        d.markSending();
        var ack = provider.send(msg.msisdn(), msg.text(), msg.notificationId().toString()); // client ref = dedup key
        d.markSent(ack.providerMsgId());
    }
    processed.save(new ProcessedEvent(eventId));
}
```

## Pitfalls
- Interviewers probe: "the worker sent the SMS and died before committing — what happens?" Answer with the `processed_event` + provider reference + `SENDING` guard, not "Kafka transactions".
- "A campaign of 1 M messages starts at 9:00 — what breaks?" Partition lag for OTPs if priorities share a topic; the SMS provider's quota; Postgres write bursts. Separate topics, token bucket, batch status writes.
- "Provider is down for an hour" — breaker open, lag grows, retention must cover it, nothing is lost; alert on lag, not on error rate alone.
- Quiet hours and preferences fetched from the primary per message: cache them ([[caching]]) and invalidate on preference change.
- Related: [[async-processing-and-event-driven-scaling]], [[system-design-basics]], [[design-rate-limiter]].
