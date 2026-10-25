---
title: ActiveMQ basics — JMS model, Classic vs Artemis
tags: [activemq, jms, messaging, message-broker]
learned: 2026-10-22
---
## TL;DR
Apache ActiveMQ is the classic Java message broker and the reference implementation most people have in mind when they say "JMS". It exists in two flavours: **ActiveMQ Classic** (the original 5.x line) and **ActiveMQ Artemis** (the HornetQ-derived broker that is the future "ActiveMQ 6"). The JMS/Jakarta Messaging API gives you **queues** (point-to-point, one consumer per message) and **topics** (pub/sub, every subscriber gets a copy) behind a vendor-neutral Java API. It is still chosen in Java shops with existing JMS code, when a standard API matters more than raw throughput, or when a lightweight embedded broker is needed.

## Key points

### JMS / Jakarta Messaging model
- **JMS** (Java Message Service, now **Jakarta Messaging 3.x** under the `jakarta.jms` package since Jakarta EE 9) is an **API**, not a wire protocol. Each broker ships its own client; ActiveMQ additionally speaks open protocols (OpenWire, AMQP 1.0, MQTT, STOMP, and in Artemis the HornetQ core protocol) so non-Java clients can connect.
- Core objects: `ConnectionFactory` → `JMSContext` (JMS 2.0 simplified API) or `Connection`/`Session` → `JMSProducer` / `JMSConsumer` on a `Destination`, which is a `Queue` or a `Topic`. Spring's `JmsTemplate` and `@JmsListener` wrap exactly this, with `DefaultJmsListenerContainerFactory` playing the role `ConcurrentKafkaListenerContainerFactory` plays in [[spring-kafka-consumer]].
- Message types: `TextMessage`, `BytesMessage`, `MapMessage`, `ObjectMessage` (avoid: Java serialization), `StreamMessage`. Headers (`JMSMessageID`, `JMSCorrelationID`, `JMSReplyTo`, `JMSExpiration`, `JMSPriority` 0–9, `JMSDeliveryMode` PERSISTENT/NON_PERSISTENT) plus arbitrary properties.
- **Message selectors**: a consumer subscribes with an SQL-92-like filter on headers/properties (`region = 'KG' AND priority > 5`), evaluated on the broker. This is JMS's substitute for RabbitMQ's exchange routing ([[rabbitmq-basics]]).

### Queues vs topics
| | Queue (point-to-point) | Topic (publish/subscribe) |
|---|---|---|
| Delivery | Each message to **one** consumer; several consumers compete | Each message to **every** active subscriber |
| Consumer offline | Message waits in the queue | Lost, unless the subscription is **durable** (survives disconnect) or **shared durable** (JMS 2.0: several consumers share one subscription — the equivalent of a Kafka consumer group) |
| Load balancing | Natural: add consumers | Only via shared subscriptions |
| Typical use | Commands, work distribution, request/reply (`JMSReplyTo` + temporary queue) | Event broadcast to several services |
| Artemis mapping | Address with an **anycast** queue | Address with **multicast** queues, one per subscription |

- **Virtual topics** (Classic) / anycast+multicast addresses (Artemis) give "publish once, each service gets its own queue with competing consumers" — the pattern you otherwise build in RabbitMQ with a fanout exchange and per-service queues.

### Acknowledgement and transactions
- `AUTO_ACKNOWLEDGE` (ack when `receive()`/`onMessage` returns — at-least-once if you crash mid-processing, since the message is redelivered), `CLIENT_ACKNOWLEDGE` (explicit `message.acknowledge()`, acks all messages received so far on the session), `DUPS_OK_ACKNOWLEDGE` (lazy acks, duplicates possible, faster), `SESSION_TRANSACTED` (ack with `session.commit()`).
- **Local transactions** group sends and acks on one session. **XA / JTA** lets a JMS session join a distributed transaction with a database — the one thing JMS brokers do that Kafka does not. XA is slow and fragile in practice; the [[outbox-pattern]] plus [[idempotency|idempotent]] consumers is the modern alternative even with ActiveMQ.
- Redelivery policy: `maximumRedeliveries` and backoff on the client (Classic) or address settings (Artemis); exhausted messages go to the **DLQ** (`ActiveMQ.DLQ` by default in Classic, `DLQ`/`ExpiryQueue` per address in Artemis).
- Flow control: **prefetch** (Classic default 1000 per queue consumer, which hoards messages like unlimited RabbitMQ prefetch — lower it for slow consumers), and **producer flow control** / paging when a destination hits its memory limit.

### ActiveMQ Classic vs Artemis
| | ActiveMQ Classic (5.x / 6.x) | ActiveMQ Artemis (2.x) |
|---|---|---|
| Origin | Original ActiveMQ, 2004 | HornetQ (JBoss), donated 2015 |
| Storage | KahaDB, or JDBC store | Append-only journal with libaio/NIO; fast on persistent messages |
| Threading | Thread per connection, blocking I/O | Netty, non-blocking, far higher throughput |
| HA | Master/slave on shared storage or JDBC lease; network of brokers for scale-out | Live/backup pairs with shared store or replication; clustering with message redistribution |
| Protocols | OpenWire (native), AMQP 1.0, MQTT, STOMP | Core, AMQP 1.0, OpenWire, MQTT, STOMP, HornetQ |
| Address model | Destinations with wildcards and virtual topics | Addresses + routing types (anycast / multicast) + queues |
| Status | Maintained; 6.x requires Java 17 and Jakarta Messaging 3.1 | The designated successor; the default embedded broker in Spring Boot (`spring-boot-starter-artemis`), WildFly and Red Hat AMQ 7 |

- New projects pick **Artemis**; Classic stays where the OpenWire/JDBC-store setup already works. Both are far behind Kafka on throughput and horizontal scale, and behind RabbitMQ on protocol breadth and operability tooling.

### When ActiveMQ is still the right choice
- Existing Java EE / Spring applications already written against JMS; switching brokers is a config change, switching to Kafka is a redesign.
- You need JMS features natively: message selectors, per-message priority and TTL, request/reply with temporary queues, XA with a database.
- Embedded broker for tests or a single-node deployment (Artemis in-VM in Spring Boot).
- Kafka's model does not fit: few messages with heavy per-message semantics, strict once-to-one-worker delivery without consumer-side offset management, many short-lived destinations.
- Not the right choice for event streaming, replay, multi-team fan-out of the same data, or sustained very high volume — that is [[kafka-architecture|Kafka]]; for flexible routing with many protocols and strong ops tooling, [[rabbitmq-basics|RabbitMQ]].

## Example
Spring Boot 3 with embedded Artemis: a queue consumer with local transactions and a DLQ, the JMS counterpart of the Kafka listener in [[spring-kafka-consumer]].

```yaml
spring:
  artemis:
    mode: embedded            # or mode: native + broker-url: tcp://mq:61616
    embedded:
      queues: esim.activation
  jms:
    listener:
      session:
        transacted: true      # redelivered on exception, DLQ after max attempts
      concurrency: 3-10
```

```java
@Component
public class ActivationJmsListener {

    @JmsListener(destination = "esim.activation", selector = "region = 'KG'")
    public void onActivation(ActivationRequested evt,
                             @Header(JmsHeaders.REDELIVERED) boolean redelivered) {
        if (redelivered && processed.exists(evt.id())) return;   // idempotency guard
        provisioning.activate(evt);
    }
}

// producer side
jmsTemplate.convertAndSend("esim.activation", evt, m -> {
    m.setStringProperty("region", evt.region());
    m.setJMSCorrelationID(evt.id());
    return m;
});
```

## Pitfalls
- A plain (non-durable) topic subscriber that is offline loses messages silently. Use shared durable subscriptions or map topics to per-service queues.
- `ObjectMessage` relies on Java serialization: a security hole and a versioning nightmare; both brokers whitelist allowed packages for it. Send JSON in `TextMessage`/`BytesMessage`.
- Default prefetch 1000 in Classic means one slow consumer holds a thousand messages others could process; set it low for long-running work.
- A transacted session with an exception in the listener redelivers immediately and forever unless `maximumRedeliveries`/a DLQ is configured — a poison message spins the consumer.
- `javax.jms` vs `jakarta.jms`: Spring Boot 3 and ActiveMQ Classic 6 / Artemis 2.2x+ are Jakarta-only; mixing old client jars gives `ClassNotFoundException` at startup.
- Related: [[rabbitmq-basics]], [[kafka-architecture]], [[kafka-consumer]], [[outbox-pattern]], [[idempotency]].
