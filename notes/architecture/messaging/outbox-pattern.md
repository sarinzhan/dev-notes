---
title: Transactional outbox pattern
tags: [messaging, kafka, reliability, postgres]
learned: 2026-08-20
---
## TL;DR
Write the domain change and the event to publish in the **same database transaction** (an `outbox` table). A separate relay reads the outbox and publishes to Kafka. This removes the dual-write problem: the event exists if and only if the business change was committed.

## Key points
- Relay options: polling publisher (simple, adds latency) or CDC with Debezium (reads the WAL, no polling).
- Delivery is **at-least-once**, so consumers must be [[idempotency|idempotent]].
- Mark rows as published instead of deleting them, then purge in batches.
- Index the unpublished rows with a partial index ([[postgres-indexes]]).

## Example
```sql
CREATE TABLE outbox (
  id            uuid PRIMARY KEY,
  aggregate_id  uuid NOT NULL,
  type          text NOT NULL,
  payload       jsonb NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  published_at  timestamptz
);
```

## Pitfalls
- Polling with `SELECT ... FOR UPDATE SKIP LOCKED` lets several relay instances run safely.
- Ordering is per aggregate: use `aggregate_id` as the Kafka key.
- Writing the outbox row outside the [[spring-transactional|@Transactional]] boundary defeats the whole point.
