---
title: Kafka producer — partitioning, acks, idempotence, transactions
tags: [kafka, messaging, reliability, delivery-semantics]
learned: 2026-10-22
---
## TL;DR
The producer serializes a record, picks a partition (by key hash, explicitly, or sticky round-robin for null keys), accumulates records into per-partition batches and sends them to the partition leader. `acks`, `retries`, `enable.idempotence` and `min.insync.replicas` together decide whether you get at-most-once, at-least-once or (with the transactional API) exactly-once delivery into the topic. Since Kafka 3.0 the defaults are `acks=all` and `enable.idempotence=true`, so a plain producer is already "at-least-once without duplicates on retry".

## Key points

### Send pipeline
1. On first use the producer fetches **cluster metadata** (brokers, partition leaders) from a bootstrap broker, not from ZooKeeper/KRaft, and caches it; it refreshes on `metadata.max.age.ms` or on a `NotLeaderOrFollower` error. Creating a producer is expensive (threads, metadata, connections) — build **one per application** and reuse it; `KafkaProducer` is thread-safe.
2. The payload is wrapped in a `ProducerRecord(topic, partition?, key?, value, headers?, timestamp?)`.
3. Key and value are **serialized** (`key.serializer`, `value.serializer`): JSON, Avro/Protobuf with a schema registry, or a custom serializer. The broker only sees bytes.
4. The **partitioner** assigns a partition (see below).
5. The record goes into the **RecordAccumulator**, a per-partition batch. Optional **compression** (`compression.type=lz4|zstd|snappy|gzip`) is applied to the whole batch.
6. A background **Sender** thread ships ready batches to the leader brokers, one request per broker containing all its ready batches, and waits for acks according to the config. Failed batches are retried.

### Partitioning
| Situation | Behaviour |
|---|---|
| Explicit partition in the record | Used as is |
| Non-null key | `murmur2(keyBytes) % numPartitions` (**hash-based**): same key → same partition → ordered per key |
| Null key, Kafka ≥ 2.4 | **Sticky partitioner**: fill one batch for a partition, then switch; even spread, better batching. 3.3 made it `partitioner.ignore.keys`-aware and uniform by bytes instead of by batch count |
| Null key, old clients | Plain round-robin, one record per partition in turn |
| Custom `partitioner.class` | Any logic, e.g. route a VIP tenant to a dedicated partition |

- Key choice is the main design decision: it sets ordering (all events of one subscriber in order) and load balance (a hot key = a hot partition). Use the aggregate id as key, as the [[outbox-pattern]] does.
- Changing the partition count changes `hash % n` and breaks ordering for existing keys ([[kafka-architecture]]).

```anim
kafka-partitioning
```

### Batching and latency
- `batch.size` (default 16 KB): a batch is sent when it is full. `linger.ms` (default 0, often set to 5–100 ms): how long the producer waits for more records before sending a non-full batch. Larger batches = fewer requests, better compression, higher throughput, slightly higher latency.
- If several batches to the same broker are ready at once they go in one request; `max.request.size` (1 MB) caps the request, `buffer.memory` (32 MB) caps the accumulator — when it is full, `send()` blocks up to `max.block.ms`.
- `send()` is asynchronous and returns a `Future<RecordMetadata>`; pass a `Callback` or use `.get()` only when you need synchronous confirmation.

### Reliability: `acks`
| `acks` | Leader replies when | Guarantee |
|---|---|---|
| `0` | never — fire and forget | Fastest; records lost on any failure; **at-most-once** |
| `1` | the leader appended to its own log | Lost if the leader dies before followers replicate |
| `all` / `-1` | all **in-sync replicas** have appended | Durable as long as `min.insync.replicas` ≥ 2; **default since 3.0** |

- `acks=all` only means "written to the ISR"; the ISR may shrink to the leader alone unless `min.insync.replicas` forbids it. Durability is `acks=all` **plus** `min.insync.replicas=2` **plus** replication factor 3.
- `retries` (default `Integer.MAX_VALUE`) with `delivery.timeout.ms` (120 s) as the real bound; `retry.backoff.ms` between attempts. Retriable errors: leader election in progress, `NotEnoughReplicas`, network timeouts. Non-retriable: serialization errors, record too large, authorization.

### Delivery semantics (producer → topic)
- **At-most-once**: `acks=0`, or `retries=0`. Messages may be lost, never duplicated. Metrics, logs.
- **At-least-once**: `acks=all` with retries. Nothing is lost, but a retry after a lost ack writes the record twice.
- **Exactly-once**: no loss, no duplicates. Costs latency and requires the idempotent producer (per partition) or transactions (across partitions and together with consuming).

### Idempotent producer
- Even with `acks=all` a duplicate appears when the broker wrote the batch but the ack was lost in the network: the producer retries, the broker appends again.
- `enable.idempotence=true` (default since 3.0; requires `acks=all`, `retries>0`, `max.in.flight.requests.per.connection ≤ 5`): the broker assigns the producer a **PID** and the producer attaches a per-partition **sequence number** to every batch. The broker keeps the last 5 sequences per PID/partition and silently drops a batch it already has, and also rejects out-of-order sequences, so **ordering is kept even with 5 in-flight requests**.
- Scope: one producer session, one partition. A restarted producer gets a new PID, so a duplicate across restarts is not caught — that is what transactions and consumer-side [[idempotency]] are for.

### Transactions and exactly-once
- `transactional.id` turns on the transactional API: `initTransactions()`, `beginTransaction()`, `send()` to any number of partitions, `sendOffsetsToTransaction()` to commit the consumed offsets in the same transaction, then `commitTransaction()` or `abortTransaction()`. A **transaction coordinator** on the broker writes the state to `__transaction_state` and marks records with commit/abort control records.
- The `transactional.id` also fences a zombie instance: after a new producer with the same id calls `initTransactions()`, the old one gets `ProducerFencedException`. Since 2.5 (KIP-447) fencing works through the consumer group generation, so one `transactional.id` per application instance is enough.
- Consumers see transactional records only with `isolation.level=read_committed` (the default is `read_uncommitted`).
- This gives exactly-once for **consume → transform → produce** pipelines inside Kafka (Kafka Streams `processing.guarantee=exactly_once_v2`). It does **not** cover a side effect in Postgres or an HTTP call: there you still need the [[outbox-pattern]] on the write side and [[idempotency]] on the read side.

## Example
```java
Properties p = new Properties();
p.put(BOOTSTRAP_SERVERS_CONFIG, "kafka-1:9092,kafka-2:9092");
p.put(KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
p.put(VALUE_SERIALIZER_CLASS_CONFIG, KafkaJsonSerializer.class);
p.put(ACKS_CONFIG, "all");                      // default since 3.0, explicit for readers
p.put(ENABLE_IDEMPOTENCE_CONFIG, true);
p.put(LINGER_MS_CONFIG, 20);
p.put(BATCH_SIZE_CONFIG, 64 * 1024);
p.put(COMPRESSION_TYPE_CONFIG, "zstd");
p.put(DELIVERY_TIMEOUT_MS_CONFIG, 60_000);

KafkaProducer<String, ActivationRequested> producer = new KafkaProducer<>(p); // one per app

// key = subscriber id: all events of one subscriber stay ordered on one partition
ProducerRecord<String, ActivationRequested> rec =
        new ProducerRecord<>("esim.activation", evt.subscriberId(), evt);
rec.headers().add("trace-id", traceId.getBytes(UTF_8));

producer.send(rec, (meta, ex) -> {
    if (ex != null) log.error("publish failed for {}", evt.id(), ex); // after retries exhausted
    else log.debug("stored at {}-{}@{}", meta.topic(), meta.partition(), meta.offset());
});
```

## Pitfalls
- `acks=all` with the broker default `min.insync.replicas=1` is a false sense of safety; set it on the topic ([[kafka-architecture]]).
- `max.in.flight.requests.per.connection > 1` **without** idempotence reorders records on retry. With idempotence on, up to 5 is safe.
- Calling `producer.send(...).get()` per record kills throughput (one round-trip per record). Use callbacks and let the accumulator batch; flush only on shutdown.
- A producer created per request leaks threads and sockets and re-fetches metadata every time; it is a classic cause of `TimeoutException: Failed to update metadata`.
- Transactions are not a cross-system 2PC: "write to DB and publish exactly once" still needs the [[outbox-pattern]] and idempotent consumers ([[idempotency]]).
- `DeliveryTimeout` with records still in the buffer means the record is lost from the producer's point of view; the callback receives the exception and nothing retries for you. With Spring, `KafkaTemplate` wraps this producer — the same rules apply.
- Related: [[kafka-consumer]], [[spring-kafka-consumer]].
