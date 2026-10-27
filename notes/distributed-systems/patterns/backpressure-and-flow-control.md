---
title: Backpressure and flow control
tags: [distributed-systems, reliability, kafka, reactive]
learned: 2026-10-27
---
## TL;DR
When a producer is faster than a consumer, something has to give: **buffer** (bounded, then what?), **drop** (which messages?), or **slow the producer down**. Backpressure is the third option: the consumer signals how much it can take and the producer respects it. Unbounded queues are not a strategy, they are a delayed out-of-memory error. TCP does flow control with its receive window, Reactive Streams with `request(n)`, Kafka with pull-based consumers whose lag is visible, thread pools with bounded queues and rejection policies.

## Key points

### The three options when load exceeds capacity
| Option | Mechanism | Fails how |
|---|---|---|
| **Buffer** | queue in memory or on disk | memory grows, latency grows (every item waits behind the whole queue), then OOM or disk full |
| **Drop / shed** | reject new work, sample, keep newest or oldest | lost work; acceptable for metrics, presence, duplicate notifications |
| **Backpressure** | consumer tells producer to slow down; blocking or credit-based | producer's own callers back up; the pressure propagates to the edge where it belongs |

- Little's law: items in system = arrival rate × time in system. A bounded buffer caps the time in system; an unbounded one lets latency grow without limit while throughput looks fine ([[amdahls-law-and-parallel-speedup]]).
- The right place to shed load is the entry point (API gateway, rate limiter) where you can return `429`/`503` with `Retry-After`, not deep inside a pipeline where the request has already consumed resources ([[rate-limiting]], [[api-gateway-and-service-discovery]]).

### Where flow control already exists
- **TCP**: the receiver advertises a window; a slow reader makes the sender stop. This is why a blocked consumer thread eventually blocks the producer's `write()` across the network ([[tcp-vs-udp]]).
- **HTTP/2** has per-stream windows; **gRPC** streaming exposes `isReady()`/`onReady` for the same purpose ([[grpc-and-protobuf]]).
- **Reactive Streams** (Project Reactor, RxJava): `Subscriber.request(n)` is the credit; `Flux` operators like `onBackpressureBuffer(n)`, `onBackpressureDrop`, `onBackpressureLatest` make the strategy explicit ([[spring-reactive-webclient-sse]]).
- **Kafka**: consumers *pull* with `max.poll.records`; a slow consumer simply polls less often and lag grows in the broker, where it is cheap and visible. The broker never pushes faster than the consumer asks. Producer side: `buffer.memory` full → `send()` blocks for `max.block.ms` then throws, which is backpressure onto the application ([[kafka-producer]], [[kafka-consumer]]).
- **RabbitMQ**: push-based, so `basic.qos` prefetch count limits unacked messages per consumer; with no prefetch a fast queue drowns a slow consumer ([[rabbitmq-basics]]).
- **Thread pools**: `ThreadPoolExecutor` with a bounded `ArrayBlockingQueue` and `CallerRunsPolicy` makes the submitting thread do the work, which slows the submitter: backpressure in one line ([[executors-and-thread-pools]]).
- **Java streams and `BlockingQueue`**: `put()` blocks when full; that blocking is the signal.

### Kafka consumer lag as the system's pressure gauge
- Lag = latest offset − committed offset per partition. Rising lag means the consumer is slower than the producer. It is *good* that the backlog sits in Kafka (durable, retention-bounded) rather than in a consumer's heap.
- Responses: scale consumers up to the partition count, batch the downstream work, make the handler faster, or accept lag with a retention long enough to survive it. Beyond the partition count, more consumers do nothing ([[kafka-architecture]]).
- If the handler blocks longer than `max.poll.interval.ms` (5 min default) the broker kicks the consumer out, triggers a rebalance, and the same records are redelivered to another instance that will also be slow: a rebalance storm. Use `pause()`/`resume()` on the consumer, or process in a bounded pool and commit on completion ([[spring-kafka-consumer]]).

### Designing a pipeline with backpressure
1. Every queue bounded, with a documented policy on full: block, drop oldest, reject.
2. Deadlines propagate so work that already missed its SLO is dropped instead of processed ([[failure-modes-and-timeouts]]).
3. Expose queue depth, lag and rejection counts as metrics ([[grafana-dashboards-and-red-use]]).
4. Prefer pull over push between services; or if push, use credits (prefetch).
5. Isolate slow consumers (bulkheads): one slow downstream should not drain the pool used for everything ([[circuit-breaker]]).

## Example
A Kafka listener that hands work to a bounded pool and pauses the partition when the pool is full, instead of blocking inside `poll()` or buffering without limit.
```java
@Component
public class SmsDispatchListener {
    private final ThreadPoolExecutor pool = new ThreadPoolExecutor(
        8, 8, 0, TimeUnit.SECONDS,
        new ArrayBlockingQueue<>(200),                        // bounded: 200 in flight max
        new ThreadPoolExecutor.AbortPolicy());               // full -> RejectedExecutionException

    @KafkaListener(topics = "sms.outbound", groupId = "sms-gateway", containerFactory = "manualAckFactory")
    public void on(ConsumerRecord<String, SmsCommand> rec, Acknowledgment ack, Consumer<?, ?> consumer) {
        try {
            pool.execute(() -> { gateway.send(rec.value()); ack.acknowledge(); });
        } catch (RejectedExecutionException full) {
            // backpressure: stop fetching this partition until the pool drains, then redeliver
            consumer.pause(Set.of(new TopicPartition(rec.topic(), rec.partition())));
            resumeLater(consumer, rec);                      // scheduler resumes when queue < 50%
            throw full;                                      // not acked -> redelivered after resume
        }
    }
}
```
With `max.poll.records=50` and `max.poll.interval.ms` left at default, the consumer stays alive during an SMS-gateway slowdown; lag grows in Kafka, memory does not grow in the pod ([[dead-letter-queues-and-retries]] for what to do when the gateway is down, not slow).

## Pitfalls
- `Executors.newFixedThreadPool`: fixed threads, **unbounded** queue; it will take every task and run out of heap under sustained overload.
- `onBackpressureBuffer()` without a size, or `Flux.create` with the default `BUFFER` strategy: the same unbounded queue with a reactive name.
- Retrying a slow dependency inside the consumer thread: retries add load exactly when capacity is lowest.
- Scaling consumers past the partition count and calling it "horizontal scaling".
- Related: [[kafka-consumer]] for poll/commit mechanics, [[rate-limiting]] for shedding at the edge, [[leases-heartbeats-and-health]] for why a paused consumer must keep heartbeating.
