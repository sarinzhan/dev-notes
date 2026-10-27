---
title: Асинхронная обработка и масштабирование на событиях
tags: [system-design, scaling, kafka, async, reliability]
learned: 2026-10-27
---
## Кратко
Всё, чего пользователю не нужно ждать, должно уйти с пути запроса: принять команду, сохранить её, вернуть `202` и дать worker-ам забрать её из очереди или лога. Это развязывает пик входящего трафика и пропускную способность медленных зависимостей (SMS-провайдеры, HLR, биллинг), позволяет каждому этапу масштабироваться отдельно и превращает сбои в отставание (backlog), а не в ошибки. Цена — новый набор гарантий, которые нужно спроектировать: доставка at-least-once, поэтому consumer-ы должны быть идемпотентными; порядок по ключу; путь в очередь недоставленных сообщений (dead letter) для «ядовитых» сообщений; видимость lag. Выбор между очередью и логом — в [[message-queues-and-streams]]; эта заметка про то, как с их помощью масштабироваться.

## Основное

### Почему async масштабируется там, где sync — нет
- Задержки и доступности синхронной цепочки перемножаются: три зависимости по 99.9% дают 99.7%, а одна медленная занимает поток в каждом вышестоящем сервисе ([[service-communication-sync-vs-async]]).
- С очередью посередине ёмкость API — это «насколько быстро я могу записать строку и опубликовать», то есть тысячи в секунду на одном узле. Сторона consumer-а работает со скоростью зависимости; разрыв превращается в **lag**, который можно измерить и который ограничен сроком хранения (retention).
- Каждый этап становится отдельной consumer group со своим числом реплик, пулом потоков, таймаутами и [[circuit-breaker]]; медленный email-провайдер больше не тормозит push.

### Форма асинхронного конвейера
```
client ──POST──▶ API ──tx: row + outbox──▶ Postgres ──relay──▶ topic (key = aggregate id)
                                                                     │
                                            consumer group (N ≤ partitions) ──▶ dependency
                                                                     │ fail permanently
                                                                     └─▶ DLQ topic + alert
```
- [[outbox-pattern]] делает «принято» и «опубликовано» атомарными; без него API либо теряет команды, либо публикует фантомные.
- Ключ партиции = сущность, для которой важен порядок (пользователь, заказ, SIM). Параллелизм = число партиций; реплики consumer-а сверх этого простаивают ([[kafka-architecture]], [[kafka-consumer]]).

### Идемпотентные consumer-ы
- Доставка at-least-once: rebalance, падения после побочного эффекта, но до commit offset-а, повторные запуски relay. Consumer дедуплицирует по id события в той же транзакции, что и его эффект, или делает эффект идемпотентным по природе ([[idempotency]], [[exactly-once-myth]]).
- Внешние вызовы несут тот же ключ идемпотентности (`orderId:step`), чтобы провайдер тоже дедуплицировал ([[esim-provisioning-platform]]).

### Retry и dead letter
- Временные ошибки (таймауты, 5xx, 429): retry с экспоненциальной задержкой и jitter. Блокирующий retry внутри consumer-а держит партицию; неблокирующий retry использует retry-топики с растущими задержками ([[dead-letter-queues-and-retries]]).
- Постоянные ошибки (валидация, 4xx, неизвестный абонент): без retry, сразу в DLQ с причиной ошибки в заголовках, статус `FAILED` в строке, алерт по доле DLQ.
- DLQ нужен свой consumer: инструмент replay, который заново публикует выбранные сообщения после исправления бага.

### Обратное давление и сигналы для масштабирования
- Lag consumer-а (`records-lag-max`, Burrow, Kafka exporter) — метрика автомасштабирования worker-ов: KEDA масштабирует deployment по lag на партицию ([[kubernetes-basics]]).
- Ограниченные очереди внутри процесса и `max.poll.records` не дают одному worker-у забрать больше, чем он успеет обработать за `max.poll.interval.ms`; иначе его выкидывают из группы и начинается шторм rebalance-ов ([[backpressure-and-flow-control]]).
- Ограничивай частоту на стороне зависимости (token bucket на провайдера), а не позволяй retry усиливать сбой ([[rate-limiting]]).

### Запрос-ответ поверх async
- Клиенту рано или поздно нужен ответ: вернуть `202` с id и эндпоинтом `GET /status`, отправить webhook или стримить прогресс через SSE/WebSocket ([[websockets-sse-and-long-polling]], [[spring-reactive-webclient-sse]]).
- Для многошаговых процессов с компенсацией движок workflow заменяет написанные вручную цепочки consumer-ов ([[temporal-concepts]], [[workflow-engines-vs-sagas]], [[saga]]).

### Когда не стоит уходить в async
- Пользователь должен увидеть результат в том же запросе (авторизация платежа, логин). Тогда синхронный путь делают быстрым и быстро падающим (fail fast); в очередь кладут только последующие действия (чек, аналитика, уведомление).
- Крошечные системы: очередь добавляет брокер, relay, retention и мониторинг. Поллер на `@Scheduled` по таблице с `SKIP LOCKED` — вполне годная первая очередь ([[background-jobs-and-schedulers]]).

## Пример
Consumer Spring Kafka с неблокирующими retry и DLQ, дедуплицирующий по id события в той же транзакции, что и бизнес-эффект:

```java
@Component
@RequiredArgsConstructor
public class SmsDispatchConsumer {
    private final SmsProvider provider;
    private final ProcessedEventRepository processed;
    private final NotificationRepository notifications;

    @RetryableTopic(
        attempts = "4",
        backoff = @Backoff(delay = 2_000, multiplier = 3),   // 2s, 6s, 18s
        exclude = { InvalidRecipientException.class },        // постоянная ошибка → сразу в DLQ
        dltTopicSuffix = ".dlq")
    @KafkaListener(topics = "notifications.sms", groupId = "sms-dispatch", concurrency = "6")
    @Transactional
    public void onMessage(@Header("event-id") String eventId, SmsRequested msg) {
        if (processed.existsById(eventId)) return;             // повторная доставка
        provider.send(msg.msisdn(), msg.text(), eventId);      // провайдер дедуплицирует по тому же ключу
        notifications.markSent(msg.notificationId(), Channel.SMS);
        processed.save(new ProcessedEvent(eventId, Instant.now()));
    }

    @DltHandler
    public void dead(SmsRequested msg, @Header(KafkaHeaders.EXCEPTION_MESSAGE) String reason) {
        notifications.markFailed(msg.notificationId(), Channel.SMS, reason);
    }
}
```

Шесть потоков listener-а соответствуют шести партициям; седьмой pod ничего не меняет, пока топик не перепартиционирован.

## Подводные камни
- `202 Accepted` без эндпоинта статуса или callback-а; клиенту нечего опрашивать, а поддержка спрашивает «а оно отправилось?».
- Бесконечные блокирующие retry на постоянной ошибке останавливают партицию для всех остальных пользователей за ней.
- Consumer-ы, которые вызывают зависимость без таймаута, превышают `max.poll.interval.ms`, вызывают rebalance и заново получают всё, что было в обработке.
- Таблица дедупликации без задачи очистки растёт бесконечно; используй TTL или партиционируй её по дням ([[postgres-partitioning]]).
- Связанные заметки: [[event-driven-architecture]] — сторона проектирования, [[spring-kafka-consumer]] и [[testing-async-and-kafka-flows]] — реализация и тесты.
