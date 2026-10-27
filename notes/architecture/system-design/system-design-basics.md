---
title: System design basics — framework, building blocks, numbers
tags: [system-design, architecture, distributed-systems, interview]
learned: 2026-10-27
---
## TL;DR
A system design interview is a structured conversation: clarify requirements, estimate load, define the API and data model, draw the high-level boxes, then deep-dive into the two or three hard parts. The boxes are always the same catalogue — load balancer, stateless services, cache, database with replicas and shards, message queue, CDN, rate limiter — and the skill is choosing which trade-off (consistency, latency, cost, complexity) each box buys. Know a handful of numbers by heart so that estimates are grounded.

## Key points

### The framework (45 minutes)
1. **Requirements** (5 min): functional ("send a push/SMS/email to a user"), non-functional (scale, latency, durability, consistency), out of scope. Ask about read/write ratio and peak vs average.
2. **Estimates** (5 min): DAU → QPS → storage/bandwidth, peak = 2–5× average.
3. **API** (5 min): a few REST endpoints or Kafka topics ([[rest-and-http]], [[openapi-swagger]]). Idempotency keys for writes.
4. **Data model** (5 min): main entities, which store for each ([[database-fundamentals]], [[normalization-and-denormalization]]).
5. **High-level design** (10 min): client → LB → services → cache/DB/queue. Keep it boring.
6. **Deep dive** (15 min): the interviewer's favourite bottleneck: hot partition, retries, exactly-once, failover.

### Building blocks
- **Load balancer**: L4 (TCP) or L7 (HTTP, path routing). Algorithms: round-robin, least connections, consistent hashing for sticky routing. Health checks remove bad instances. Behind it, an [[api-gateway-and-service-discovery|API gateway]] does auth and rate limits.
- **Stateless services**: no session on the box, so any replica can handle any request and scaling is "add pods" ([[kubernetes-basics]]). State goes to DB, cache or a token ([[jwt-authentication]]).
- **Cache** (Redis, [[redis-data-structures]]): *cache-aside* (app reads cache, on miss reads DB and writes cache), *write-through* (write cache + DB together), *write-behind* (write cache, flush later, risky). Set a **TTL** always; invalidate on write for correctness-sensitive data. Problems: cache stampede (many misses at once → use a lock or request coalescing), hot keys, stale reads. [[spring-cache-abstraction]] on the app side, [[hibernate-caching]] inside the ORM.
- **CDN**: static assets and cacheable responses served from edge PoPs; `Cache-Control` headers decide what is cacheable.
- **Database**: single primary for writes, **read replicas** for reads (async replication → replica lag → read-your-own-write issues). **Sharding/partitioning** when one node cannot hold writes or data: hash sharding (even, no range scans), range sharding (range scans, hot ranges), directory. Cross-shard joins and transactions are the cost. Postgres: declarative partitioning first, Citus/sharding later ([[postgres-indexes]], [[postgres-isolation-levels]]).
- **Message queue / log** ([[kafka-architecture]], [[rabbitmq-basics]]): decouple producers from consumers, absorb spikes, enable retries and fan-out. Pair with the [[outbox-pattern]] for atomic publish and with [[idempotency]] on the consumer because delivery is at-least-once.
- **Rate limiting**: token bucket (burst allowed), leaky bucket (smooth), fixed/sliding window counters. Store counters in Redis; return `429` + `Retry-After`.
- **Resilience**: timeouts, retries with backoff, [[circuit-breaker]], bulkheads; long multi-step flows as a [[saga]] or a [[temporal-concepts|Temporal workflow]].
- **Observability**: metrics, logs, traces ([[distributed-tracing]], [[logging-stacks]]).

### CAP and PACELC in plain terms
- During a network **partition** a distributed store must choose: keep answering with possibly stale data (**AP**) or refuse/block to stay **consistent** (**CP**). "CA" is a single node.
- **PACELC** adds the normal case: *else* (no partition), choose between **latency** and **consistency**. Synchronous replication = consistent but slower; async = fast but stale. Postgres with sync replicas: PC/EC; Cassandra with quorum tuning: PA/EL.
- Consistency levels: **strong / linearizable** (read sees the latest write), **sequential**, **causal**, **read-your-writes**, **monotonic reads**, **eventual** (converges "eventually"). Pick per use case: balance must be strong; notification history can be eventual.

### Numbers to know
| Operation | Latency |
|---|---|
| L1 cache / branch mispredict | ~1 ns |
| Main memory read | ~100 ns |
| Redis GET in same DC | ~0.2–0.5 ms |
| SSD random read | ~100 µs; sequential 1 GB/s+ |
| Postgres indexed point read | ~1 ms |
| Round trip inside a DC | ~0.5 ms |
| Bishkek ↔ Frankfurt round trip | ~70–90 ms |
| Kafka produce (acks=all, 3 brokers) | ~2–10 ms |

- A day is ~86 400 s ≈ 10⁵ s. 10 M requests/day ≈ 115 QPS; 1 B/day ≈ 11 600 QPS. Peak ×3.
- One Postgres primary on good hardware: thousands of simple writes/s, tens of thousands of indexed reads/s. One Kafka broker: tens of MB/s per partition set comfortably.
- Storage: 1 KB record × 10 M/day ≈ 10 GB/day ≈ 3.6 TB/year.

## Example
"Design a notification service" (push, SMS, email) for a telecom app, 5 M subscribers, 20 M notifications/day (peak 1 000/s), per-channel providers with different SLAs, no duplicates.

```yaml
# API
POST /v1/notifications
  Idempotency-Key: <uuid>            # dedupe client retries
  body: { userId, template, params, channels: [PUSH, SMS], priority }
  -> 202 { notificationId }
GET  /v1/notifications/{id}          # status per channel

# Flow
api -> postgres (notification row + outbox) --relay--> kafka topic notifications.requested (key = userId)
router consumer: resolves user prefs/quiet hours -> fans out to notifications.push / .sms / .email
per-channel workers (separate consumer groups, own concurrency, own circuit breaker per provider)
  - provider call with timeout 3s, retry 3x exponential+jitter on 5xx/timeouts
  - on permanent failure or exhausted retries -> notifications.sms.dlq + status FAILED
  - delivery status written back (processed_events table keyed by notificationId+channel for idempotency)
  - providers' delivery receipts (webhooks) -> notifications.receipts -> status DELIVERED
redis: rate limit per user (no more than N SMS/hour), provider quota counters, template cache (TTL 10 min)
```

Why these choices: Kafka keyed by `userId` keeps one user's notifications ordered and lets per-channel workers scale independently; the outbox makes "row saved" and "event published" atomic; the `Idempotency-Key` plus the `processed_events` table guarantee one SMS per request even when the API client or the consumer retries; a DLQ keeps a poison message from blocking the partition; a breaker per provider keeps an SMS outage from affecting push.

## Pitfalls
- Jumping to boxes before QPS: "we need sharding" for 50 writes/s is the classic fail. One Postgres with replicas covers most interview-sized problems.
- Forgetting failure modes: what if the queue is down, the provider is slow, the consumer crashes after sending but before committing (→ duplicate → idempotency).
- Cache without TTL or invalidation strategy; replica lag ignored for read-after-write flows.
- Claiming "exactly-once" end-to-end over an external SMS provider; be honest: at-least-once plus dedupe.
- Related: [[kafka-producer]], [[kafka-consumer]], [[outbox-pattern]], [[idempotency]], [[circuit-breaker]], [[saga]], [[redis-data-structures]], [[postgres-indexes]], [[transactions-acid-and-isolation]], [[architecture-resources]].
