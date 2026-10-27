---
title: Notifications and push as a building block — fan-out, channels, retries, deduplication
tags: [system-design, notifications, push, kafka, reliability]
learned: 2026-10-27
---
## TL;DR
A notification subsystem turns one domain event ("top-up succeeded", "eSIM activated") into zero or more deliveries over channels with very different providers and guarantees: push (APNs/FCM, fire-and-forget to a device token), SMS (a gateway with a hard rate limit and per-message cost), email (SMTP or an API with bounces), in-app (your own store and a WebSocket). The building block is a **pipeline**: accept and persist, resolve recipients and preferences, fan out per channel, deliver through a provider adapter with retries and a circuit breaker, record status from provider receipts, and deduplicate at every retry boundary so a user never gets the same SMS twice. The full case study is [[design-notification-service]]; this note is the reusable block and its decisions.

## Key points

### Stages of the pipeline
| Stage | Responsibility | Store / tool |
|---|---|---|
| Accept | validate, persist with `Idempotency-Key`, return `202` | Postgres + [[outbox-pattern]] |
| Resolve | recipients (one user, a segment, a topic), preferences, quiet hours, opt-outs, locale, device tokens | Postgres + Redis cache ([[caching]]) |
| Render | template + params → per-channel content (push title, SMS text ≤ 160 chars, email HTML) | template store, cached |
| Fan-out | one message per (recipient, channel) onto per-channel topics | Kafka keyed by recipient ([[message-queues-and-streams]]) |
| Deliver | provider adapter: timeout, retry, [[circuit-breaker]], [[rate-limiting]] per provider | per-channel workers |
| Track | provider receipts (webhooks) → DELIVERED / FAILED / BOUNCED, token cleanup | receipts topic, status table |
| Observe | per-channel success rate, latency, queue lag, provider error mix | [[grafana-dashboards-and-red-use]] |

### Fan-out choices
- **Fan-out on write** (materialise one message per recipient at send time) for targeted notifications and small segments: simple, per-recipient retries, status per delivery. A segment of 5 M users is 5 M messages; fine for Kafka, but throttle the producer so the provider is not flooded.
- **Fan-out on read** for in-app feeds of broadcast content: store the campaign once, each client fetches "campaigns matching me since my last read". Same trade-off as [[design-news-feed]].
- **Topic subscriptions** at the push provider (FCM topics) move broadcast fan-out to the provider; you lose per-device status.
- Order per recipient matters ("activated" before "deactivated"): key everything by recipient id so one partition serialises one user's messages.

### Channels and their quirks
| Channel | Provider contract | Failure signal | Rate / cost | Special care |
|---|---|---|---|---|
| Push (APNs/FCM) | HTTP/2 API, device token | immediate 4xx (invalid token), delayed "unregistered" | high throughput, free | tokens expire: delete on `Unregistered`; collapse keys to replace stale notifications |
| SMS | HTTP gateway or SMPP | sync accept, async DLR webhook | 100–1 000 msg/s per contract, per-message cost | GSM-7 vs UCS-2 length, sender id rules, no retries on "invalid number", quiet hours by law |
| Email | SMTP or API | bounces and complaints via webhook, hours later | cheap, reputation-limited | suppression list for hard bounces, DKIM/SPF, unsubscribe link |
| In-app | your DB + [[websockets-sse-and-long-polling|WebSocket/SSE]] | none | free | read/unread state, badge counts |
| Webhook to partners | HTTP POST to their URL | 5xx / timeout | their limits | signing, retries with backoff, [[dead-letter-queues-and-retries|DLQ]] |

A per-channel **worker** with its own consumer group, thread pool, breaker and limiter keeps an SMS outage from touching push ([[async-processing-and-event-driven-scaling]]).

### Retries, deduplication and ordering
- Delivery is at-least-once everywhere (Kafka redelivery, worker crash after the provider accepted, client retry of the API). Deduplicate with a **unique constraint** on `(notification_id, channel)` in a `delivery` table written before the provider call, and the provider's own idempotency key when it offers one (FCM, most SMS APIs accept a client message id).
- Classify provider errors: **retryable** (timeout, 5xx, 429 → backoff with jitter, max N, then DLQ) vs **permanent** (invalid token, number not in service, opted out → FAILED immediately, fix the data). Retrying a permanent error is the most common waste in these systems ([[error-handling-strategies]]).
- Retries must respect **expiry**: an OTP retried 40 minutes later is harmful; each message carries a `ttl` after which the worker drops it with status EXPIRED.
- Receipts arrive out of order and duplicated: update status with "only move forward" semantics (`ACCEPTED → SENT → DELIVERED`, never back) and idempotent upserts ([[idempotency]]).

### Preferences, compliance, throttling
- Preferences per channel and category (transactional vs marketing), **quiet hours** in the user's time zone (defer, do not drop, for transactional; drop for marketing), opt-out that is checked at resolve time *and* at deliver time (it may change while the message sits in a queue).
- Per-user frequency caps ("max 3 marketing pushes/day") as a sliding window in Redis; per-provider rate limits as token buckets shared by all workers ([[rate-limiting]]).
- Priority: OTP and security alerts bypass caps and go to a dedicated high-priority topic with reserved worker capacity; marketing drains at whatever rate is left.
- Audit: keep what was sent to whom, with the rendered content hash, for support and regulators; retention per channel (SMS 90 days, email longer) ([[requirements-and-constraints]]).

### Sizing reminder
For 10 M users, 12 M notifications/day, the pipeline peaks around 1 K messages/s and the SMS provider's 100 msg/s is the real limit; the queue's job is to absorb the gap and the limiter's job is to protect the contract ([[back-of-envelope-estimation]]).

## Example
The SMS delivery worker: dedupe via a unique row, provider call with a timeout and breaker, error classification, expiry, and a forward-only status update.

```java
@Component
public class SmsDeliveryWorker {
    private final DeliveryRepository deliveries; private final SmsGatewayClient gateway;
    private final RedisTokenBucket providerLimit; private final CircuitBreaker breaker;

    public SmsDeliveryWorker(DeliveryRepository d, SmsGatewayClient g, RedisTokenBucket l, CircuitBreakerRegistry reg) {
        this.deliveries = d; this.gateway = g; this.providerLimit = l; this.breaker = reg.circuitBreaker("sms-gateway");
    }

    @KafkaListener(topics = "notifications.sms", groupId = "sms-worker")
    public void onMessage(SmsMessage msg, Acknowledgment ack) {
        if (Instant.now().isAfter(msg.expiresAt())) {                         // OTPs must not arrive late
            deliveries.markExpired(msg.notificationId(), "SMS");
            ack.acknowledge(); return;
        }
        // 1. Dedup across redeliveries: unique (notification_id, channel). Existing row = already handled.
        if (!deliveries.insertIfAbsent(msg.notificationId(), "SMS", msg.phone())) { ack.acknowledge(); return; }

        // 2. Provider rate limit: wait, do not drop. Pausing here applies backpressure to the partition.
        var decision = providerLimit.tryConsume("rl:provider:sms-gw", 100, 100.0, 1);
        while (!decision.allowed()) {
            LockSupport.parkNanos(Duration.ofMillis(Math.max(100, decision.retryAfterSec() * 1000)).toNanos());
            decision = providerLimit.tryConsume("rl:provider:sms-gw", 100, 100.0, 1);
        }

        try {
            // 3. Provider call behind a breaker with a 3 s timeout; our notificationId is the provider's idempotency key.
            var result = breaker.executeSupplier(() -> gateway.send(msg.phone(), msg.text(), msg.notificationId().toString()));
            deliveries.advance(msg.notificationId(), "SMS", DeliveryStatus.SENT, result.providerRef());
            ack.acknowledge();
        } catch (SmsGatewayException e) {
            if (e.isPermanent()) {                                             // invalid number, opted out, blacklisted
                deliveries.advance(msg.notificationId(), "SMS", DeliveryStatus.FAILED, e.code());
                ack.acknowledge();
            } else {
                deliveries.deleteAttempt(msg.notificationId(), "SMS");       // allow the retry to pass dedup
                throw e;                                                       // retryable: container error handler → retry topic → DLQ
            }
        } catch (CallNotPermittedException open) {
            deliveries.deleteAttempt(msg.notificationId(), "SMS");
            throw open;                                                        // breaker open: back off via the retry tier
        }
    }
}

// Receipt webhook from the gateway, idempotent and forward-only:
// UPDATE delivery SET status = 'DELIVERED', delivered_at = :t WHERE provider_ref = :ref AND status IN ('SENT');
```

The container's error handler (`DefaultErrorHandler` with a `DeadLetterPublishingRecoverer`) routes retryable failures to `notifications.sms.retry.1m` and after the last tier to `notifications.sms.dlq` ([[spring-kafka-consumer]], [[dead-letter-queues-and-retries]]).

## Pitfalls
- Dedup only in memory or only by Kafka offset: a redeploy or rebalance reprocesses the batch and the user gets two SMS; the unique row is the guarantee.
- Synchronous provider call inside the API request: the API inherits the gateway's latency and outages ([[api-design-for-system-design]]).
- One consumer group for all channels: the slow SMS gateway throttles push; separate topics, groups and pools.
- Ignoring provider feedback: unregistered tokens and hard bounces accumulate, costs rise, email reputation sinks; process receipts and prune.
- Quiet hours and opt-out checked only at accept time: a marketing push queued at 21:55 is delivered at 23:10; re-check at delivery.
- Related: [[design-notification-service]] for the end-to-end design and capacity; [[outbox-pattern]] for the accept stage's atomic publish.
