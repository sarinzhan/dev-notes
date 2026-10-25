---
title: Reactive Spring: WebFlux, WebClient and Server-Sent Events
tags: [reactive, webflux, webclient, sse, reactor]
learned: 2026-10-22
---
## TL;DR
**Spring WebFlux** is the non-blocking web stack built on **Project Reactor** (`Mono<T>` = 0..1 value, `Flux<T>` = 0..N) and Netty: a handful of event-loop threads (about one per CPU core) serve thousands of concurrent connections because no thread ever waits on I/O. **`WebClient`** is its HTTP client: fluent, immutable, thread-safe, backed by reactor-netty. **Server-Sent Events** stream a one-way sequence of events from server to browser over a plain HTTP response; both MVC (`SseEmitter`) and WebFlux (`Flux<ServerSentEvent>`) support it. Since Java 21 virtual threads make blocking MVC scale almost as well, so choose reactive for streaming and back-pressure, not just for throughput.

## Key points
### Reactive model
- Reactive Streams spec: `Publisher`, `Subscriber`, `Subscription`, `Processor`; **back-pressure** lets the consumer say how many items it can take.
- Reactor types: `Mono` and `Flux`, with operators (`map`, `flatMap`, `zip`, `retryWhen`, `timeout`, `onErrorResume`). Nothing happens until `subscribe()`; the framework subscribes for you when you return a `Mono`/`Flux` from a controller.
- Threading: a small event-loop pool (`reactor-http-nio-N`), never block on it. Blocking code (JDBC, a legacy library) must be moved to `Schedulers.boundedElastic()` via `subscribeOn`. BlockHound detects accidental blocking in tests.
- Data access must be reactive too: R2DBC instead of JPA, reactive Redis/Mongo/Kafka drivers. A reactive controller calling a JPA repository is the worst of both worlds.
- Boot picks the stack from the starter: `spring-boot-starter-web` → MVC on Tomcat; `spring-boot-starter-webflux` → WebFlux on Netty. With both present, MVC wins.

### WebClient
- Reactor-based fluent API for HTTP; wraps reactor-netty (can also use Jetty or JDK `HttpClient`).
- Non-blocking, one event-loop thread per CPU core; instances are **immutable** and **thread-safe**, so build one per downstream and share it (`WebClient.Builder` injected by Boot carries metrics and tracing).
- Usage: `client.get().uri(...).retrieve().bodyToMono(T.class)`; `.exchangeToMono()` when you need the raw status; `.onStatus()` to map errors; `.timeout(Duration)`, `.retryWhen(Retry.backoff(3, Duration.ofMillis(200)))`.
- Usable from an MVC app too: `spring-boot-starter-webflux` on the classpath adds the client. Calling `.block()` is acceptable in MVC code but then prefer [[rest-clients|RestClient]] for clarity.
- Streaming bodies: `bodyToFlux(Event.class)` for NDJSON or SSE responses.

### Server-Sent Events
- One-way stream from backend to frontend over a long-lived HTTP response with `Content-Type: text/event-stream`; the browser `EventSource` API reconnects automatically (`Last-Event-ID` header) and the protocol is just text lines (`id:`, `event:`, `data:`, `retry:`).
- The client subscribes to specific actions (order status, provisioning progress, notifications) and receives events as soon as they occur, without polling.
- Media types: `text/event-stream` (`MediaType.TEXT_EVENT_STREAM_VALUE`) for browsers; `application/x-ndjson` (`MediaType.APPLICATION_NDJSON_VALUE`) for service-to-service streaming where you want one JSON object per line without SSE framing.
- SSE vs WebSocket: SSE is HTTP, one direction, works through proxies and with HTTP/2 multiplexing, trivial to secure with the usual headers; WebSocket is bidirectional and binary-capable but needs its own protocol handling and infrastructure support. Pick SSE for notifications/progress, WebSocket for chat-like interaction.
- In MVC: return `SseEmitter`, keep a registry of emitters, `send()` from another thread, handle `onCompletion`/`onTimeout`. In WebFlux: return `Flux<ServerSentEvent<T>>` and let Reactor push. Behind a load balancer SSE needs sticky sessions or a shared bus (Kafka/Redis pub-sub) so every instance can deliver events ([[spring-kafka-consumer]]).

## Example
```java
@RestController
@RequestMapping("/api/v1/provisioning")
public class ProvisioningEventsController {

    private final Sinks.Many<ProvisioningEvent> sink =
            Sinks.many().multicast().onBackpressureBuffer();
    private final WebClient vendor;

    ProvisioningEventsController(WebClient.Builder builder, EsimVendorProps props) {
        this.vendor = builder.baseUrl(props.baseUrl()).build();
    }

    @GetMapping(value = "/{iccid}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<ServerSentEvent<ProvisioningEvent>> events(@PathVariable String iccid) {
        return sink.asFlux()
                .filter(e -> e.iccid().equals(iccid))
                .map(e -> ServerSentEvent.builder(e).id(e.id()).event(e.type()).build())
                .mergeWith(Flux.interval(Duration.ofSeconds(15))     // heartbeat keeps proxies open
                        .map(t -> ServerSentEvent.<ProvisioningEvent>builder().comment("ping").build()));
    }

    @KafkaListener(topics = "esim.provisioning.events")
    void onEvent(ProvisioningEvent event) { sink.tryEmitNext(event); }

    public Mono<ProfileStatus> vendorStatus(String iccid) {
        return vendor.get().uri("/profiles/{iccid}", iccid)
                .retrieve()
                .bodyToMono(ProfileStatus.class)
                .timeout(Duration.ofSeconds(3))
                .retryWhen(Retry.backoff(2, Duration.ofMillis(300)));
    }
}
```

## Pitfalls
- Blocking inside a reactive pipeline (`.block()`, JDBC, `Thread.sleep`) on an event-loop thread stalls all requests on that loop. Use `boundedElastic` or stay on MVC.
- Forgetting to subscribe: a `Mono` that is built but never returned or subscribed does nothing (no HTTP call is sent).
- Reactive code is harder to debug (stack traces are operator chains); enable `Hooks.onOperatorDebug()` in tests or use the Reactor debug agent.
- SSE through Nginx needs `proxy_buffering off` and a long `proxy_read_timeout`, otherwise events are buffered or the connection is cut; always send heartbeats.
- `SseEmitter` in MVC holds a Tomcat async context per client; thousands of subscribers are fine, but each emitter must be removed on completion/timeout or it leaks.
- Related: [[rest-clients]], [[spring-mvc-request-lifecycle]], [[virtual-threads]] (the main reason to reconsider reactive in 2024+), [[completable-future]].
