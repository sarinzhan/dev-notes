---
title: Kafka consumer — groups, rebalancing, offsets, lag
tags: [kafka, messaging, consumer-groups, delivery-semantics]
learned: 2026-10-22
---
## TL;DR
A consumer **pulls** batches of records from partition leaders by offset, deserializes and processes them, and then **commits** the offset so a restart continues where it left off. Consumers with the same `group.id` form a **consumer group**: the broker-side group coordinator assigns every partition to exactly one member, so parallelism is capped by the partition count. Any change of membership or partitions triggers a **rebalance**. When you commit relative to processing decides between at-least-once and at-most-once; **lag** (log end offset minus committed offset) is the first metric to watch.

## Key points

### Poll loop
- `KafkaConsumer` is single-threaded and not thread-safe. The loop is `subscribe(topics)` → `poll(timeout)` → process records → commit. `poll()` also sends heartbeats, joins the group and fetches metadata from brokers (not from ZooKeeper, as older material says).
- Fetch tuning: `max.poll.records` (500), `fetch.min.bytes` / `fetch.max.wait.ms` (batching on the broker side), `max.partition.fetch.bytes`. The consumer prefetches the next batch while you process the current one.
- Records are only visible up to the **high watermark** (committed by all ISR replicas), and with `isolation.level=read_committed` only committed transactional records ([[kafka-producer]]).
- Reading is pull-based: a slow consumer just falls behind; it never gets overwhelmed by the broker, unlike push-based brokers ([[rabbitmq-basics]]).

### Consumer groups and assignment
- `group.id` groups consumers that share the work of a topic. Each partition belongs to **exactly one** consumer in the group; one consumer may own many partitions from several topics. More consumers than partitions → idle consumers, so choose the partition count with scaling headroom ([[kafka-architecture]]).
- Different groups are independent: a `billing` group and an `analytics` group both read every record of the topic with their own offsets — that is pub/sub on top of a queue.
- Membership protocol (classic): one broker is the **group coordinator** for the group (chosen by hashing `group.id` onto `__consumer_offsets`). On join, the coordinator picks one member as **group leader**, which runs the `partition.assignment.strategy` and sends the assignment back through the coordinator. Strategies: `RangeAssignor` (default, co-partitions topics, can be uneven), `RoundRobinAssignor`, `StickyAssignor` (minimises moves), `CooperativeStickyAssignor` (sticky + incremental).
- **KIP-848, the new consumer group protocol** (preview in 3.7, GA in **4.0**, `group.protocol=consumer`): assignment moves fully to the coordinator, members get incremental assignments via heartbeats and there is no global synchronisation barrier any more. Much faster rebalances for large groups.
- Why a group beats a standalone consumer with `assign()`: offsets are stored broker-side per group, membership and failover are automatic, and you can scale by starting another instance.

### Heartbeats, liveness and rebalancing
- Each consumer has a background heartbeat thread (`heartbeat.interval.ms` 3 s). If the coordinator hears nothing for `session.timeout.ms` (45 s since 3.0) the member is declared dead and removed.
- Separately, if the application does not call `poll()` within `max.poll.interval.ms` (5 min) — processing is too slow or stuck — the consumer **leaves the group itself**, and its partitions are reassigned. Processing then continues in parallel somewhere else: duplicates.
- A **rebalance** happens on any change: a member joins or leaves (deploy, crash, scale), a subscribed topic gains partitions, a regex subscription matches a new topic. During an **eager (stop-the-world)** rebalance every member revokes all partitions, nobody consumes until the new assignment is stable, and uncommitted work is redone.
- Mitigations: `CooperativeStickyAssignor` (2.4+, incremental cooperative rebalancing: only moved partitions are revoked), **static membership** (`group.instance.id`, 2.3+: a restarted pod with the same id gets its old partitions back without a rebalance as long as it returns within `session.timeout.ms`), and the KIP-848 protocol in 4.0.
- Rebalance is the usual cause of "we deployed and got duplicates": commit in `onPartitionsRevoked` of a `ConsumerRebalanceListener` or let the framework do it ([[spring-kafka-consumer]]).

```anim
kafka-consumer-group
```

### Offsets and commits
- The committed offset is the **next offset to read**, stored in the compacted internal topic `__consumer_offsets` keyed by (group, topic, partition). Not committing is legal: the next consumer of the partition re-reads the same records.
- `enable.auto.commit=true` (default, every `auto.commit.interval.ms` = 5 s) commits whatever `poll()` returned last time, on the next `poll()`. Simple, but a crash between commit and finishing processing loses records, and a crash before commit duplicates them.
- Manual: `commitSync()` (blocks, retries, safe in a shutdown hook) and `commitAsync()` (fire-and-forget with callback; do not retry an async commit by hand, a later commit may already have a higher offset). Commit specific offsets with `commitSync(Map<TopicPartition, OffsetAndMetadata>)` after a DB write.
- `auto.offset.reset` applies only when the group has **no** committed offset for a partition (new group, or the offset expired after `offsets.retention.minutes`, default 7 days): `latest` (default) skips everything already in the topic, `earliest` replays from the beginning, `none` throws.
- Replaying: `seek()` / `seekToBeginning()` on the consumer, or `kafka-consumer-groups.sh --reset-offsets --to-datetime ...` while the group is stopped.

### Delivery semantics on the consumer side
| Pattern | Commit when | Failure between process and commit | Result |
|---|---|---|---|
| **At-most-once** | before processing (or auto-commit with fast processing) | record is skipped | loss, no duplicates |
| **At-least-once** | after processing | record is re-read | duplicates, no loss — the default you should design for |
| **Exactly-once** inside Kafka | offsets are sent in the producer transaction (`sendOffsetsToTransaction`) | both roll back | no loss/duplicates for consume→produce |
| "Exactly-once" with a DB | store offset in the same DB transaction as the result, `seek()` to it on start | both roll back | effectively once, Kafka itself is still at-least-once |

- At-least-once plus an **idempotent handler** is the practical answer for most services; see [[idempotency]] and the consumer side of the [[outbox-pattern]].

### Lag
- **Consumer lag** = log end offset − committed offset, per partition. It is the distance between what producers wrote and what the group has processed. Lag growing steadily means consumers cannot keep up (scale out up to the partition count, speed up processing, or batch); lag spiking on deploy means rebalances.
- Watch it with `kafka-consumer-groups.sh --describe --group <g>`, the `records-lag-max` client metric, Burrow, or the Kafka exporter in Prometheus/Grafana.

## Example
At-least-once with manual commit after the business write, committing per partition so a failure on one partition does not block the others.

```java
Properties p = new Properties();
p.put(BOOTSTRAP_SERVERS_CONFIG, "kafka-1:9092");
p.put(GROUP_ID_CONFIG, "provisioning");
p.put(GROUP_INSTANCE_ID_CONFIG, System.getenv("POD_NAME"));        // static membership
p.put(PARTITION_ASSIGNMENT_STRATEGY_CONFIG, CooperativeStickyAssignor.class.getName());
p.put(ENABLE_AUTO_COMMIT_CONFIG, false);
p.put(AUTO_OFFSET_RESET_CONFIG, "earliest");
p.put(MAX_POLL_RECORDS_CONFIG, 200);
p.put(KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
p.put(VALUE_DESERIALIZER_CLASS_CONFIG, KafkaJsonDeserializer.class);

try (KafkaConsumer<String, ActivationRequested> consumer = new KafkaConsumer<>(p)) {
    consumer.subscribe(List.of("esim.activation"));
    while (running) {
        ConsumerRecords<String, ActivationRequested> records = consumer.poll(Duration.ofMillis(500));
        for (TopicPartition tp : records.partitions()) {
            long last = -1;
            for (ConsumerRecord<String, ActivationRequested> r : records.records(tp)) {
                provisioning.activateIdempotently(r.value());   // safe to repeat
                last = r.offset();
            }
            if (last >= 0) {
                consumer.commitSync(Map.of(tp, new OffsetAndMetadata(last + 1))); // next offset to read
            }
        }
    }
}
```

## Pitfalls
- Committing offset `N` instead of `N + 1`: the committed value is the *next* record to read, so the last processed record is redelivered after every restart.
- Processing one `poll()` batch for longer than `max.poll.interval.ms` (default 5 min) makes the consumer leave the group; the symptom is endless rebalances and duplicates, not an error in your code. Reduce `max.poll.records` or hand work to another thread and pause partitions.
- Auto-commit is not "commit after each record": it commits the whole last poll on the next poll. Treat it as at-least-once with a possibility of loss if you crash right after the commit.
- `auto.offset.reset=latest` on a brand-new group silently skips the backlog; `earliest` on a huge topic replays everything into your database.
- A consumer group that stops for longer than `offsets.retention.minutes` (7 days) loses its offsets and restarts according to `auto.offset.reset`.
- Related: [[spring-kafka-consumer]] (container, `AckMode`, error handlers, DLT), [[kafka-producer]], [[kafka-architecture]], [[idempotency]].
