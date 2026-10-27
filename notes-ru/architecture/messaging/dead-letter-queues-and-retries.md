---
title: Очереди недоставленных сообщений, retry-топики и ядовитые сообщения
tags: [messaging, kafka, spring-kafka, reliability]
learned: 2026-10-27
---
## Кратко
У consumer-а, упавшего на сообщении, три варианта: retry на месте (блокирует партицию), retry позже через **retry-топик** с backoff (неблокирующий) или сдаться и отложить сообщение в **очередь/топик недоставленных сообщений** (dead letter queue/topic, DLQ/DLT) вместе с ошибкой. Временные ошибки (зависимость недоступна, таймаут блокировки) заслуживают ограниченного числа retry с экспоненциальным backoff; постоянные ошибки (плохой payload, нарушение бизнес-правила) идут прямо в DLT. DLT без алерта и инструмента повторного воспроизведения (replay) — чёрная дыра; retry без ограничения — бесконечный цикл на **ядовитом сообщении** (poison message).

## Основное

### Сначала классифицировать ошибку
| Ошибка | Пример | Действие |
|---|---|---|
| Временная | HTTP 503 от SM-DP+, `SQLTransientConnectionException`, Kafka `RetriableException` | Retry с backoff, затем DLT |
| Постоянная, техническая | Ошибка десериализации, несовпадение схемы, `NullPointerException` в обработчике | Сразу в DLT; исправить код, сделать replay |
| Постоянная, бизнесовая | «профиль уже выпущен», валидация не прошла | Обычно не случай для DLT: записать результат, ack, возможно, выпустить событие `...Rejected` |
| Зависящая от порядка | `ProfileActivated` пришло раньше `SimRegistered` | Короткий retry (предшественник, вероятно, ещё в пути), затем DLT |
- `DefaultErrorHandler.addNotRetryableExceptions(...)` и `retryExceptions`/`ignoreExceptions` в Resilience4j — места, где эта классификация живёт в коде ([[circuit-breaker]]).

### Блокирующий retry и retry-топики
- **Блокирующий (на месте)**: `DefaultErrorHandler` из Spring Kafka с `BackOff` делает seek назад и заново poll-ит ту же запись. Просто, порядок сохраняется, но каждая запись за ней в этой партиции ждёт; backoff 3 × 10 с останавливает партицию на 30 с ([[spring-kafka-consumer]]).
- **Неблокирующий (retry-топики)**: при ошибке запись публикуется в `orders-retry-1000` (потребляется с задержкой 1 с), затем в `orders-retry-10000`, затем в `orders-dlt`. Основная партиция идёт дальше. Порядок между записями, ушедшими на retry, и остальными теряется, поэтому consumer должен это выдерживать ([[idempotency]]). `@RetryableTopic` в Spring Kafka создаёт топики и отложенных consumer-ов за тебя.
- RabbitMQ: очередь с `x-dead-letter-exchange` и `x-message-ttl` на каждую очередь даёт такую же лестницу отложенных retry; `nack(requeue=false)` отправляет в DLX ([[rabbitmq-basics]]). В ActiveMQ есть политика повторной доставки + `ActiveMQ.DLQ` ([[activemq-basics]]).

### Что должна нести запись в DLT
- Исходные топик, партицию, offset и timestamp; класс исключения, сообщение и стек (обрезанный); consumer group; число retry; исходные заголовки (`traceparent`, `event-id`). `DeadLetterPublishingRecoverer` в Spring добавляет заголовки `kafka_dlt-*` ровно с этим.
- Сохранять исходный ключ, чтобы replay попал в ту же партицию и сохранил порядок по агрегату.
- Retention у DLT дольше, чем у основного топика (недели), потому что replay происходит после исправления.

### Эксплуатация DLT
- **Алерт** на скорость поступления сообщений в DLT и на consumer lag retry-топиков ([[slo-sli-and-alerting]]); одна запись в DLT для платёжного процесса — это вызов дежурного, сотня для аналитики — тикет.
- **Инструмент replay**: небольшой админский endpoint или CLI, который читает диапазон DLT и заново публикует в исходный топик (тот же ключ, заголовок `replayed-from`), при необходимости после deploy исправления. Replay в consumer, который всё ещё сломан, просто снова наполняет DLT, поэтому сначала стоит воспроизвести одну запись.
- **Ядовитое сообщение**: запись, которая роняет consumer каждый раз (битый JSON, гигантский payload). Без `ErrorHandlingDeserializer` она падает внутри `poll()`, и listener её никогда не видит, поэтому consumer бесконечно крутится на одном offset. Ошибки десериализации — первое, что нужно направлять в DLT.
- **Повторная доставка и идемпотентность**: сообщение, ушедшее на retry, могло уже быть наполовину обработано; обработчик должен быть идемпотентным по id события в той же транзакции, что и его эффект.

### Числа для backoff
- Экспоненциальный с jitter и ограничением: 1 с, 2 с, 4 с ... до 1–5 минут; всего 3–6 попыток для процессов, видимых пользователю, больше (часы) для пакетной интеграции, где у партнёра плановый простой.
- Retry чаще, чем зависимость успевает восстановиться, только создаёт нагрузку; лестницу нужно согласовать с длительностью открытого состояния [[circuit-breaker]].

## Пример
Неблокирующие retry с `@RetryableTopic` для consumer-а активации: три отложенных retry на временных ошибках, сразу в DLT на ошибках валидации, и DLT listener, который записывает сбой для инструмента replay.

```yaml
spring:
  kafka:
    consumer:
      group-id: provisioning
      value-deserializer: org.springframework.kafka.support.serializer.ErrorHandlingDeserializer
      properties:
        spring.deserializer.value.delegate.class: org.springframework.kafka.support.serializer.JsonDeserializer
        spring.json.trusted.packages: kg.beeline.esim.events
    listener:
      ack-mode: record
```

```java
@Component
@RequiredArgsConstructor
public class ActivationConsumer {
    private final ActivateProfileHandler activate;
    private final DeadLetterRepository deadLetters;

    @RetryableTopic(
            attempts = "4",                                        // 1 основная + 3 retry
            backoff = @Backoff(delay = 2_000, multiplier = 3.0, maxDelay = 60_000, random = true),
            topicSuffixingStrategy = TopicSuffixingStrategy.SUFFIX_WITH_INDEX_VALUE,   // -retry-0, -retry-1, -retry-2
            dltStrategy = DltStrategy.FAIL_ON_ERROR,
            exclude = { ActivationValidationException.class, DeserializationException.class },  // сразу в DLT
            autoCreateTopics = "false")
    @KafkaListener(topics = "esim.activation.requested", groupId = "provisioning")
    public void on(ActivationRequestedV1 evt,
                   @Header(KafkaHeaders.RECEIVED_TOPIC) String topic,
                   @Header(name = KafkaHeaders.DELIVERY_ATTEMPT, required = false) Integer attempt) {
        activate.handle(new ActivateProfile(evt.simId(), new Iccid(evt.iccid()), "kafka"));   // идемпотентно по evt.id()
    }

    @DltHandler
    public void dead(ActivationRequestedV1 evt,
                     @Header(KafkaHeaders.ORIGINAL_TOPIC) String origin,
                     @Header(KafkaHeaders.EXCEPTION_MESSAGE) String error,
                     @Header(KafkaHeaders.ORIGINAL_OFFSET) long offset) {
        deadLetters.save(DeadLetter.of(origin, offset, evt.id(), error));   // питает UI для replay и алерт
    }
}
```

Replay: `kafka-console-consumer` или админский endpoint читает `esim.activation.requested-dlt`, убирает заголовки `kafka_dlt-*` и заново публикует в `esim.activation.requested` с тем же ключом.

## Подводные камни
- `attempts`, оставленный по умолчанию 3, с фиксированной задержкой 1 с: через три секунды зависимость всё ещё недоступна, и при каждом коротком сбое всё попадает в DLT.
- Retry-топики с `autoCreateTopics` в продакшене: топики появляются с настройками брокера по умолчанию — одна партиция и replication factor 1; их нужно создавать явно ([[kafka-architecture]]).
- Перехват `Exception` внутри listener-а с логированием: ни retry, ни DLT, offset коммитится, сообщение пропало.
- Replay всего DLT после исправления без проверки дубликатов: воспроизведённые записи перемешиваются с новым трафиком; ключ идемпотентности consumer-а спасает, только если он есть.
- DLT, который никто не читает: на дашборд нужно выводить число сообщений по топику и возраст самой старой записи, а не только скорость ([[grafana-dashboards-and-red-use]]).
- Связанные заметки: [[spring-kafka-consumer]], [[idempotency]], [[kafka-consumer]] (lag и offset-ы), [[event-driven-architecture]], [[outbox-pattern]].
