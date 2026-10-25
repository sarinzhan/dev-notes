---
title: RabbitMQ basics — AMQP model, exchanges, queues, acks
tags: [rabbitmq, amqp, messaging, message-broker]
learned: 2026-10-22
---
## TL;DR
RabbitMQ is an open-source broker written in Erlang that implements AMQP 0-9-1 (plus MQTT, STOMP and, since 3.9, its own Streams protocol). A producer never writes to a queue directly: it publishes to an **exchange** with a **routing key**, the exchange routes the message to queues whose **binding key** matches, the broker **pushes** the message to a consumer, and the consumer **acks** it so the broker can delete it. It is a "smart broker, dumb consumer" design, the opposite of Kafka's "dumb broker, smart consumer" log.

## Key points

### Message flow
1. The producer publishes a message to a named exchange with a routing key (and optional headers/properties).
2. The exchange routes it to zero or more queues according to the bindings. The producer often has no idea whether any queue received it (`mandatory` flag + return callback, or publisher confirms, if it needs to know).
3. The queue stores a reference; the body lives in RAM and/or on disk depending on persistence.
4. When a consumer is ready (or has free prefetch slots), the broker pushes a copy of the message to it.
5. The consumer processes it and sends an **ack** (or a nack/reject).
6. The broker deletes the message from the queue, memory and disk. Unlike Kafka there is no replay after an ack — a message is consumed once by one consumer.

### Why Erlang
- Erlang/OTP was built by Ericsson in the 1980s for telecom switches that need "five nines" availability: lightweight processes, message passing, supervision trees and hot code reload. RabbitMQ maps one queue to one Erlang process, which is why a single node handles tens of thousands of queues and why a node restart loads queue contents (historically the first 16 384 messages of each queue) into memory.

### AMQP 0-9-1 model
| Component | Role |
|---|---|
| **Producer** | Opens a connection (TCP) and a lightweight **channel** inside it, publishes to an exchange |
| **Exchange** | Routing component; compares the message's routing key with each binding's key (exact or pattern) and copies the message into matching queues. Exchanges exist on every node |
| **Binding** | Link between an exchange and a queue, carries the binding key (or header arguments) |
| **Queue** | FIFO buffer that holds messages until a consumer acks them; lives on one node (classic) or replicated by Raft (quorum) |
| **Consumer** | Subscribes to a queue (`basic.consume`, push) or pulls one message (`basic.get`, avoid in hot paths) |

- Message = **headers** (AMQP-level key/value, used by the headers exchange), **properties** (application metadata: `content_type`, `correlation_id`, `reply_to`, `message_id`, `delivery_mode`, `expiration`, `priority`) and a **body** (byte array, opaque to the broker). Messages are framed on the wire; the frame size is negotiated (default max 131 072 bytes = 128 KiB).

### Exchange types
| Type | Routing rule | Typical use |
|---|---|---|
| **Default** (nameless, `""`) | Direct exchange pre-bound to every queue by queue name: publish with `routing_key = queue name` and it looks like writing to the queue | Simple work queues |
| **Direct** | Routing key must equal the binding key | Route by task type, point-to-point |
| **Fanout** | Ignores the routing key, copies to all bound queues | Pub/sub broadcast |
| **Topic** | Binding key is a dot-separated pattern: `*` = exactly one word, `#` = zero or more words (`order.*.created`, `payment.#`) | Routing by category |
| **Headers** | Matches message headers against binding arguments with `x-match = all|any`; routing key ignored | Multi-attribute routing (rare) |
| **Dead letter** | Not a type — any exchange configured as a queue's `x-dead-letter-exchange` | Rejected/expired messages |
| Plugins | `x-consistent-hash`, `x-delayed-message` | Sharding, delayed delivery |

### Queue types and properties
- **Classic**: single-node, fast, the original. Classic mirrored queues (HA policy) were the old replication mechanism; **deprecated in 3.9 and removed in 4.0**.
- **Quorum** (3.8+, default choice for anything durable since 3.13/4.0): replicated across nodes with Raft, always durable, safer under network partitions, supports poison-message handling via `x-delivery-limit` (default 20 in 4.0) and dead-lettering. Does not support priorities beyond 2 levels, `x-max-priority`, exclusive or transient flags.
- **Stream** (3.9+): an append-only, replicated log with offset-based, non-destructive consumption and retention by size/time — Kafka-like semantics inside RabbitMQ, read via AMQP or the dedicated stream protocol.
- Queue properties: `durable` (survives broker restart; messages survive only if also published with `delivery_mode=2`/persistent), `auto-delete` (gone when the last consumer unsubscribes), `exclusive` (one connection, deleted when it closes), `x-max-priority` (classic only), `x-message-ttl` / `x-expires` (message and queue TTL), `x-max-length` / `x-overflow` (bounded queues: `drop-head` or `reject-publish`), `x-queue-type`, `x-dead-letter-exchange` / `x-dead-letter-routing-key`. Lazy queues (page everything to disk) became the only behaviour for classic queues in 3.12.
- Properties are set at declaration (code), by **policies** (operator-defined patterns in the management UI/CLI, preferred), or by server defaults.

### Acknowledgements, prefetch, redelivery
- **Consumer acks**: `autoAck=true` means the broker forgets the message as soon as it is sent (at-most-once). With manual acks, `basic.ack` after processing gives **at-least-once**; `basic.nack`/`basic.reject` with `requeue=true` puts it back (and marks `redelivered`), with `requeue=false` drops it or dead-letters it.
- **Prefetch** (`basic.qos(prefetch_count)`): how many unacked messages the broker may push to one consumer. Unlimited prefetch lets one fast connection hoard everything and blows up memory; `1` gives perfect fairness but a round-trip per message. Typical: 10–300 depending on processing time. Set per channel or per consumer (`global=false`, the default in Java).
- **Publisher confirms** (`confirmSelect`): the broker acks the *publisher* once the message is persisted/replicated — the producer-side equivalent of Kafka `acks=all`. Without confirms and persistence, a broker crash loses in-flight messages.
- **Dead letter exchange (DLX)**: a message is dead-lettered when it is rejected without requeue, its TTL expires, the queue overflows, or (quorum) it exceeds the delivery limit. The `x-death` header records why and how many times. Combine with a TTL on the DLQ and a binding back to the work queue to build retry with delay.
- Consumer acks deal with at-least-once; duplicates still happen (consumer crashes after processing, before ack), so handlers need [[idempotency]], and reliable publishing from a DB transaction still needs the [[outbox-pattern]].

### Patterns the model gives you
- **Work queue / point-to-point**: one queue, N competing consumers, round-robin with prefetch for fairness.
- **Pub/sub**: fanout exchange, one queue per subscriber.
- **Routing / topic**: direct or topic exchange, selective subscriptions.
- **RPC**: publish with `reply_to` (often a per-client exclusive queue or the `amq.rabbitmq.reply-to` pseudo-queue) and `correlation_id`; the server replies to `reply_to`.
- **Dead-letter / retry**: DLX + TTL queues.
- **Delayed message**: TTL + DLX trick, or the `rabbitmq_delayed_message_exchange` plugin.

### RabbitMQ vs Kafka
| | RabbitMQ | Kafka |
|---|---|---|
| Model | Smart broker routes, pushes, deletes on ack | Dumb broker keeps a log, consumers pull by offset |
| Message lifetime | Until acked (or TTL) | Retention/compaction; replay possible |
| Routing | Rich: exchange types, headers, per-message TTL, priorities | By partition key only |
| Ordering | Per queue with one consumer; lost on requeue/competing consumers | Per partition, guaranteed |
| Throughput | Tens of thousands msg/s per node typical | Millions msg/s per cluster, sequential disk I/O |
| Consumers | Competing consumers scale freely | Parallelism capped by partition count |
| Fit | Task queues, RPC, complex routing, many small queues, protocols (MQTT/STOMP), low-latency commands | Event streaming, event sourcing, multiple independent readers, analytics, CDC |

Choose RabbitMQ when you need *commands* delivered once to one worker with flexible routing and per-message features; choose [[kafka-architecture|Kafka]] when you need an *event log* several teams read at their own pace, or very high throughput. Streams narrow the gap for small-to-medium volumes.

## Example
Durable work queue with a dead-letter queue and bounded prefetch (Java client, which Spring AMQP wraps).

```java
try (Connection conn = factory.newConnection(); Channel ch = conn.createChannel()) {
    ch.exchangeDeclare("esim", BuiltinExchangeType.TOPIC, true);
    ch.exchangeDeclare("esim.dlx", BuiltinExchangeType.FANOUT, true);
    ch.queueDeclare("esim.activation.dlq", true, false, false, Map.of());
    ch.queueBind("esim.activation.dlq", "esim.dlx", "");

    ch.queueDeclare("esim.activation", true, false, false, Map.of(
            "x-queue-type", "quorum",
            "x-dead-letter-exchange", "esim.dlx",
            "x-delivery-limit", 5));
    ch.queueBind("esim.activation", "esim", "activation.*");   // activation.requested, activation.retry

    ch.confirmSelect();                                        // publisher confirms
    ch.basicPublish("esim", "activation.requested",
            new AMQP.BasicProperties.Builder().deliveryMode(2).contentType("application/json")
                    .messageId(evt.id()).build(),
            json.writeValueAsBytes(evt));
    ch.waitForConfirmsOrDie(5_000);

    ch.basicQos(50);                                           // prefetch per consumer
    ch.basicConsume("esim.activation", false, (tag, delivery) -> {
        try {
            provisioning.activateIdempotently(json.readValue(delivery.getBody(), ActivationRequested.class));
            ch.basicAck(delivery.getEnvelope().getDeliveryTag(), false);
        } catch (Exception e) {
            ch.basicNack(delivery.getEnvelope().getDeliveryTag(), false, true); // requeue; DLX after 5 tries
        }
    }, tag -> {});
}
```

## Pitfalls
- `durable=true` on the queue alone does not survive a restart: the message must also be published persistent (`delivery_mode=2`), and you only know it reached disk with publisher confirms.
- No prefetch limit (`basicQos` not called) with a slow consumer means the broker pushes the whole queue into that consumer's memory and other consumers starve.
- Nack with `requeue=true` and no delivery limit creates an infinite poison-message loop at 100 % CPU; use quorum `x-delivery-limit` or a DLX with a retry counter.
- Ordering is weaker than people assume: competing consumers, requeues and priorities all reorder messages. If order matters, one consumer per queue, or use Streams / Kafka.
- Classic mirrored queues are gone in 4.0; migrating to quorum queues requires re-declaring queues (type is immutable) and dropping unsupported features such as `x-max-priority` and exclusive/transient flags.
- Connections are expensive, channels are cheap, but a channel is not thread-safe: one channel per thread.
- Related: [[kafka-architecture]], [[kafka-consumer]], [[activemq-basics]], [[outbox-pattern]], [[idempotency]].
