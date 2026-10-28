---
title: Back-of-envelope estimation — QPS, storage and bandwidth math
tags: [system-design, interview, capacity-planning, estimation]
learned: 2026-10-27
---
## TL;DR
Estimation turns "10 million users" into numbers a database or a Kafka cluster can be compared against: requests per second, bytes per day, bytes per year, connections, memory for a cache. The arithmetic is deliberately coarse: round everything to a power of ten, use `1 day ≈ 10⁵ s`, `1 month ≈ 2.5 M s`, `1 year ≈ 3 × 10⁷ s`, and keep one significant digit. The goal is not precision but the order of magnitude, because the decisions (one Postgres vs sharding, in-memory cache vs not, one broker vs ten) change only between orders of magnitude.

## Key points

### Constants to memorise
| Quantity | Value to use |
|---|---|
| Seconds per day | 86 400 ≈ 10⁵ (use 100 000) |
| Seconds per month | ≈ 2.6 × 10⁶ |
| Seconds per year | ≈ 3.15 × 10⁷ |
| Peak factor | 2–5 × average; telecom and chat spike at 20:00–22:00 |
| ASCII char | 1 B; UUID as text 36 B, as `uuid` column 16 B |
| `bigint`, `timestamp` | 8 B each; a row has ~24 B of tuple header in Postgres |
| Typical JSON API request | 0.5–2 KB; HTTP headers alone ~0.5–1 KB |
| Photo | 200 KB–2 MB; 1 min of 720p video ≈ 50 MB |
| 1 K / 1 M / 1 G | 10³ / 10⁶ / 10⁹ (ignore 1024) |

Capacity anchors (per node, "comfortable", not benchmark records):
- Postgres 17 primary: 5–10 K simple writes/s, 20–50 K indexed point reads/s; one connection pool of ~20–50 per pod ([[hikaricp-pool-sizing]]).
- Redis: ~100 K simple ops/s single-threaded, memory ≈ sum of keys + values × ~1.5 overhead.
- Kafka broker: 50–100 MB/s in, tens of thousands of small messages/s per partition; latency ladder in [[latency-numbers]].
- Stateless Spring Boot pod (2 vCPU): 1–3 K rps of simple JSON handling, far less if each request waits on a slow downstream (thread per request) unless using [[virtual-threads]].
- Network: 1 Gbit/s ≈ 125 MB/s; a 10 Gbit NIC ≈ 1.25 GB/s.

### The procedure
1. **Users → actions**: DAU × actions per user per day = events/day.
2. **Events/day → QPS**: divide by 10⁵. Multiply by the peak factor.
3. **Reads vs writes**: apply the ratio; reads and writes hit different components.
4. **Storage**: event size × events/day → per day, per year, with retention and replication factor (×3 for Kafka and most replicated stores).
5. **Bandwidth**: QPS × payload size, ingress and egress separately. Egress usually dominates (responses are larger) and costs money.
6. **Memory for cache**: hot set = working data for 20 % of users or last N hours; compare with a single Redis node (tens of GB) to see whether a cluster is needed.
7. **Sanity-check** against the anchors and say what the number implies: "1 K writes/s fits one Postgres primary; 50 K does not".

### Reading the result
- Under ~1 K rps and ~100 GB: one Postgres with replicas, Redis for hot reads, no sharding ([[database-scaling-path]]).
- 1–10 K writes/s or multi-TB per year: partitioning by time ([[postgres-partitioning]]), a log for ingestion ([[message-queues-and-streams]]), cold data to blob storage ([[file-and-blob-storage]]).
- 10 K+ writes/s sustained: sharding or a wide-column store ([[sharding-strategies]], [[storage-choices]]).

## Example
Sizing a notification service for **10 M registered users**, 30 % daily active, average 4 notifications per active user per day, 3 channels, history kept 90 days.

```text
1. Volume
   DAU               = 10 M × 0.3                      = 3 M
   notifications/day = 3 M × 4                         = 12 M
   average rate      = 12 M / 10^5 s                   = 120 /s
   peak (×5, evening campaigns)                        = 600 /s
   channel deliveries/day = 12 M × 1.5 channels avg    = 18 M  (~180 /s avg, ~900 /s peak)

2. Reads
   history reads ≈ 2 per DAU per day                   = 6 M/day = 60 /s, peak 300 /s
   each read = last 20 notifications = 20 rows × 500 B = 10 KB  → trivial

3. Storage
   notification row: id 16 B + user_id 8 B + template 32 B + params JSONB ~300 B
                     + status/channel/timestamps ~60 B + tuple header 24 B ≈ 450 B → round to 0.5 KB
   per day           = 12 M × 0.5 KB                   = 6 GB
   90-day retention  = 6 GB × 90                       = 540 GB (+ ~30 % indexes → ~700 GB)
   per-channel delivery attempts: 18 M × 200 B         = 3.6 GB/day, keep 30 days → ~110 GB
   → one Postgres can hold it, but partition by day and drop old partitions instead of DELETE.

4. Kafka
   event ≈ 1 KB; 18 M events/day × 1 KB               = 18 GB/day, ×3 replication = 54 GB/day
   7-day retention                                     = ~380 GB across the cluster
   peak ingress 900 msg/s × 1 KB                       = 0.9 MB/s  → one broker is bored; 3 brokers for HA
   partitions: target ≤ 100 msg/s per consumer thread  → 12 partitions per channel topic, room to grow

5. Provider throughput
   SMS share 40 % → 7.2 M/day → 72 /s avg, 360 /s peak; provider limit 100 /s
   → queue must absorb (360 − 100) /s × 2 h peak = ~1.9 M backlog; drain time ≈ 1.9 M / 100 ≈ 5.2 h
   → either buy a second provider or shape traffic (priority queue, defer marketing SMS to off-peak).

6. Cache
   preferences + quiet hours per user ≈ 200 B; all 10 M users = 2 GB → fits one Redis node;
   cache only DAU (3 M × 200 B = 600 MB) with TTL 1 h and warm on miss.

7. Bandwidth
   API ingress 600 rps × 1 KB = 0.6 MB/s;  webhooks from providers 900 /s × 0.5 KB = 0.45 MB/s;
   history egress 300 rps × 10 KB = 3 MB/s  → nothing near a 1 Gbit link (125 MB/s).
```

Conclusion for the whiteboard: compute and bandwidth are small; the design is driven by **durability** (write before `202`, [[outbox-pattern]]), **provider rate limits** (queue depth and [[rate-limiting]]), and **storage lifecycle** (daily partitions, 90-day drop). Compare with [[design-notification-service]] for the resulting architecture.

## Pitfalls
- Forgetting the peak factor: a design sized for the average falls over at 21:00 every day.
- Counting storage without indexes, replication and retention: raw rows × days is usually a third of the real disk usage.
- Confusing registered users with DAU, and requests with user actions (one screen open = 5–10 API calls).
- Sizing a thread-per-request service by CPU alone: with a 200 ms downstream call, 200 Tomcat threads give ~1 K rps max regardless of cores ([[socket-management]]).
- Quoting a number without the conclusion. "600 rps" is useless; "600 rps, so one Postgres primary and no sharding" is the answer.
- Related: [[latency-numbers]] for the time side of the same estimates; [[requirements-and-constraints]] for where the inputs come from.
