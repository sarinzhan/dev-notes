---
title: Реактивный Spring: WebFlux, WebClient и Server-Sent Events
tags: [reactive, webflux, webclient, sse, reactor]
learned: 2026-10-22
---
## Кратко
**Spring WebFlux** — неблокирующий веб-стек на основе **Project Reactor** (`Mono<T>` = 0..1 значение, `Flux<T>` = 0..N) и Netty: горстка потоков цикла событий (event loop, примерно по одному на ядро CPU) обслуживает тысячи одновременных соединений, потому что ни один поток никогда не ждёт ввода-вывода. **`WebClient`** — его HTTP-клиент: fluent, неизменяемый, потокобезопасный, работает поверх reactor-netty. **Server-Sent Events** передают однонаправленный поток событий от сервера в браузер через обычный HTTP-ответ; поддерживаются и в MVC (`SseEmitter`), и в WebFlux (`Flux<ServerSentEvent>`). С Java 21 виртуальные потоки позволяют блокирующему MVC масштабироваться почти так же хорошо, поэтому реактивный подход стоит выбирать ради стриминга и обратного давления (back-pressure), а не только ради пропускной способности.

## Основное
### Реактивная модель
- Спецификация Reactive Streams: `Publisher`, `Subscriber`, `Subscription`, `Processor`; **back-pressure** позволяет потребителю сообщить, сколько элементов он может принять.
- Типы Reactor: `Mono` и `Flux` с операторами (`map`, `flatMap`, `zip`, `retryWhen`, `timeout`, `onErrorResume`). Ничего не происходит до `subscribe()`; когда контроллер возвращает `Mono`/`Flux`, фреймворк подписывается сам.
- Потоки: небольшой пул циклов событий (`reactor-http-nio-N`), блокировать его нельзя никогда. Блокирующий код (JDBC, legacy-библиотека) нужно переносить на `Schedulers.boundedElastic()` через `subscribeOn`. BlockHound находит случайные блокировки в тестах.
- Доступ к данным тоже должен быть реактивным: R2DBC вместо JPA, реактивные драйверы Redis/Mongo/Kafka. Реактивный контроллер, вызывающий JPA-репозиторий, сочетает худшее из обоих миров.
- Boot выбирает стек по стартеру: `spring-boot-starter-web` → MVC на Tomcat; `spring-boot-starter-webflux` → WebFlux на Netty. Если есть оба, побеждает MVC.

### WebClient
- Fluent API для HTTP на основе Reactor; оборачивает reactor-netty (может также использовать Jetty или JDK `HttpClient`).
- Неблокирующий, один поток цикла событий на ядро CPU; экземпляры **неизменяемые** и **потокобезопасные**, поэтому создавай один на каждый downstream и переиспользуй его (`WebClient.Builder`, внедрённый Boot, несёт метрики и трассировку).
- Использование: `client.get().uri(...).retrieve().bodyToMono(T.class)`; `.exchangeToMono()`, когда нужен «сырой» статус; `.onStatus()` для маппинга ошибок; `.timeout(Duration)`, `.retryWhen(Retry.backoff(3, Duration.ofMillis(200)))`.
- Можно использовать и из MVC-приложения: `spring-boot-starter-webflux` на classpath добавляет клиент. Вызов `.block()` допустим в MVC-коде, но тогда для ясности лучше выбрать [[rest-clients|RestClient]].
- Потоковые тела: `bodyToFlux(Event.class)` для ответов NDJSON или SSE.

### Server-Sent Events
- Однонаправленный поток от бэкенда к фронтенду через долгоживущий HTTP-ответ с `Content-Type: text/event-stream`; браузерный API `EventSource` переподключается автоматически (заголовок `Last-Event-ID`), а протокол — это просто текстовые строки (`id:`, `event:`, `data:`, `retry:`).
- Клиент подписывается на конкретные действия (статус заказа, прогресс провижининга, уведомления) и получает события сразу, как только они происходят, без опроса (polling).
- Media types: `text/event-stream` (`MediaType.TEXT_EVENT_STREAM_VALUE`) для браузеров; `application/x-ndjson` (`MediaType.APPLICATION_NDJSON_VALUE`) для стриминга между сервисами, когда нужен один JSON-объект на строку без SSE-обрамления.
- SSE или WebSocket: SSE — это HTTP, одно направление, работает через прокси и с мультиплексированием HTTP/2, легко защищается обычными заголовками; WebSocket двунаправленный и умеет передавать бинарные данные, но требует собственной обработки протокола и поддержки инфраструктуры. Для уведомлений/прогресса выбирай SSE, для взаимодействия в стиле чата — WebSocket.
- В MVC: вернуть `SseEmitter`, вести реестр эмиттеров, вызывать `send()` из другого потока, обрабатывать `onCompletion`/`onTimeout`. В WebFlux: вернуть `Flux<ServerSentEvent<T>>`, и Reactor сам будет отправлять события. За балансировщиком нагрузки SSE требует sticky sessions или общей шины (Kafka/Redis pub-sub), чтобы любой экземпляр мог доставить события ([[spring-kafka-consumer]]).

## Пример
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
                .mergeWith(Flux.interval(Duration.ofSeconds(15))     // heartbeat не даёт прокси закрыть соединение
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

## Подводные камни
- Блокировка внутри реактивного конвейера (`.block()`, JDBC, `Thread.sleep`) на потоке цикла событий останавливает все запросы этого цикла. Используй `boundedElastic` или оставайся на MVC.
- Забытая подписка: `Mono`, который собран, но не возвращён и не подписан, ничего не делает (HTTP-вызов не отправляется).
- Реактивный код сложнее отлаживать (стектрейсы — это цепочки операторов); включай `Hooks.onOperatorDebug()` в тестах или используй Reactor debug agent.
- SSE через Nginx требует `proxy_buffering off` и длинного `proxy_read_timeout`, иначе события буферизуются или соединение обрывается; всегда отправляй heartbeat.
- `SseEmitter` в MVC держит асинхронный контекст Tomcat на каждого клиента; тысячи подписчиков — это нормально, но каждый эмиттер нужно удалять при завершении/таймауте, иначе будет утечка.
- Связанные заметки: [[rest-clients]], [[spring-mvc-request-lifecycle]], [[virtual-threads]] (главная причина пересмотреть реактивный подход в 2024+), [[completable-future]].
