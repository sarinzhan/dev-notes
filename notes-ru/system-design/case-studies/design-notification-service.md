---
title: Проектирование сервиса уведомлений
tags: [system-design, case-study, kafka, notifications, reliability]
learned: 2026-10-27
---
## Кратко
Сервис уведомлений принимает запросы вида «сообщи пользователю X о Y» и доставляет их через push, SMS и email с помощью сторонних провайдеров, у которых разная скорость и разные режимы отказа, — без дубликатов и без потери запросов, когда провайдер недоступен. Архитектура: API, который быстро сохраняет и подтверждает запрос; лог Kafka, поглощающий пики; группы worker-ов на каждый канал со своими retry и circuit breaker; идемпотентность в трёх точках: клиентский запрос, consumer, вызов провайдера. Самое сложное — гарантия доставки: честная доставка «хотя бы один раз» (at-least-once) плюс дедупликация, а никогда не «ровно один раз» (exactly-once). Строительные блоки описаны в [[message-queues-and-streams]], [[rate-limiting]], [[caching]]; общие паттерны рассылки — в [[notifications-and-push]].

## Основное

### Требования и оценки
- Функциональные: отправка пользователю по одному или нескольким каналам; шаблоны с параметрами; предпочтения пользователя и часы тишины; статус доставки по каждому каналу; массовые кампании.
- Нефункциональные: никаких дублей SMS (они стоят денег и раздражают); никаких потерянных уведомлений; OTP должен прийти за секунды, маркетинг может подождать минуты; сбои провайдеров не должны заваливать API ошибками.
- Числа (телеком, 5 M абонентов): 20 M уведомлений в день ≈ 230/с в среднем, пик кампании 2 000/с. Строка ~1 KB → 20 GB/день; 90 дней храним в горячем виде (~2 TB), более старое — в архив. Задержка провайдера 100 мс–5 с; пропускная способность SMS-провайдера ограничена, скажем, 500/с.

### API
```
POST /v1/notifications          Idempotency-Key: <uuid>
  { userId, template, params, channels: [PUSH, SMS], priority: HIGH|NORMAL|BULK, scheduleAt? }
  -> 202 { notificationId }                      (same key again -> same 202, same id)
GET  /v1/notifications/{id}     -> { status per channel, attempts, lastError }
PUT  /v1/users/{id}/preferences -> { channels, quietHours, locale }
POST /v1/providers/{p}/receipts  (webhook from provider: DELIVERED / FAILED)
```
- `Idempotency-Key` хранится вместе с уведомлением; повторный запрос возвращает существующий id ([[idempotency]], [[api-design-for-system-design]]).
- Приоритет — это отдельный топик или набор партиций, чтобы кампании не задерживали OTP.

### Модель данных
- `notification(id, user_id, template, params jsonb, priority, created_at, idempotency_key unique)`
- `delivery(notification_id, channel, status, attempts, provider_msg_id, last_error, updated_at)` — первичный ключ `(notification_id, channel)`.
- `user_preference(user_id, channels, quiet_hours, locale)` — кешируется в Redis с TTL.
- `outbox` для публикации `NotificationRequested` ([[outbox-pattern]]); `processed_event(event_id, processed_at)` на стороне consumer-а.
- Всё это в Postgres; `notification` и `delivery` партиционированы по месяцам ради политики хранения ([[postgres-partitioning]]).

### Общая архитектура
```
clients ──▶ API (stateless, N pods) ──tx: notification + outbox──▶ Postgres
                                                                   │ relay
                                                                   ▼
                              Kafka notifications.requested (key = userId, priority topics)
                                                                   │
                        router: prefs (Redis cache) + quiet hours + template render
                          ├─▶ notifications.push ──▶ push workers ──▶ FCM/APNs
                          ├─▶ notifications.sms  ──▶ sms workers  ──▶ SMS gateway (rate-limited)
                          └─▶ notifications.email ─▶ email workers ─▶ SMTP/SES
                                      each: own consumer group, retry topics, DLQ, breaker
provider receipts ──webhook──▶ API ──▶ notifications.receipts ──▶ status updater ──▶ delivery
```

### Подробно: гарантия доставки без дубликатов
- Три места, где возникают дубликаты: клиент повторяет POST (сеть), consumer получает сообщение повторно после падения между «отправлено» и «offset закоммичен», и провайдер отвечает таймаутом, хотя на самом деле отправил.
- Три точки дедупликации: уникальный индекс `idempotency_key` в API; строка `processed_event`, вставляемая в той же транзакции, что и обновление `delivery`, на стороне consumer-а; идемпотентность на стороне провайдера (FCM/APNs принимают id сообщения, многие SMS-шлюзы принимают клиентскую ссылку). Если у провайдера такого нет, используется короткоживущее состояние «отправляется»: перед вызовом ставим `SENDING`, а при повторной доставке строки в `SENDING` со свежей меткой времени запрашиваем статус у провайдера, а не отправляем заново.
- Порядок: ключ по `userId`, чтобы «код 1234» и «код истёк» одного пользователя приходили по порядку ([[kafka-architecture]]).
- Retry: временные ошибки провайдера идут через retry-топики с задержкой (2 с, 10 с, 60 с); постоянные ошибки (неверный номер, отписка) уходят в DLQ и помечаются `FAILED` ([[dead-letter-queues-and-retries]]). [[circuit-breaker]] на каждого провайдера открывается по доле ошибок, и worker приостанавливает свои партиции, а не сжигает retry.
- Ограничение нагрузки на провайдера: token bucket на каждого провайдера в Redis; worker-ы берут токен перед отправкой, поэтому трафик кампании 2 000/с вытекает с оговорённой в договоре скоростью 500/с без шторма ответов 429 ([[rate-limiting]], [[redis-patterns-and-persistence]]).
- Наблюдаемость: lag по каждой consumer group, задержка отправки по каждому провайдеру, доля DLQ, сквозная гистограмма «запрошено → доставлено»; trace id от API до вызова провайдера ([[distributed-tracing]], [[slo-sli-and-alerting]]).

### Компромиссы
| Решение | Выбрано | Альтернатива | Почему |
|---|---|---|---|
| Очередь | лог Kafka, топики на канал | очереди RabbitMQ на канал | replay, порядок на пользователя, рассылка кампаний; Rabbit подходит при меньшем масштабе |
| Хранение статусов | партиционированный Postgres | Cassandra | запросы по пользователю и id, умеренная скорость записи; wide-column — при > 50k записей/с ([[storage-choices]]) |
| Приоритет | отдельные топики | поле приоритета в одном топике | в Kafka нет приоритетов; OTP никогда не должен стоять в очереди за массовой рассылкой |
| Рендеринг шаблонов | в роутере | в API | предпочтения и локаль определяются один раз, ближе к моменту отправки |

## Пример
Дедупликация на стороне consumer-а и защита через состояние «отправляется» в одной транзакции:

```java
@Transactional
public void dispatchSms(String eventId, SmsRequested msg) {
    if (processed.existsById(eventId)) return;                       // повторная доставка из Kafka
    Delivery d = deliveries.lockByKey(msg.notificationId(), SMS);    // SELECT ... FOR UPDATE
    if (d.status() == SENDING && d.updatedAt().isAfter(now().minusSeconds(60))) {
        d.apply(provider.status(d.providerMsgId()));                 // ранее был таймаут: спросить, не отправлять повторно
    } else if (d.status() == PENDING) {
        d.markSending();
        var ack = provider.send(msg.msisdn(), msg.text(), msg.notificationId().toString()); // client ref = ключ дедупликации
        d.markSent(ack.providerMsgId());
    }
    processed.save(new ProcessedEvent(eventId));
}
```

## Подводные камни
- На собеседовании спрашивают: «worker отправил SMS и умер до commit — что будет?» Отвечать через `processed_event` + ссылку для провайдера + защиту `SENDING`, а не «транзакциями Kafka».
- «Кампания на 1 M сообщений стартует в 9:00 — что сломается?» Отставание партиций для OTP, если приоритеты делят один топик; квота SMS-провайдера; всплески записи в Postgres. Отдельные топики, token bucket, пакетная запись статусов.
- «Провайдер лежит час» — breaker открыт, lag растёт, срок хранения (retention) должен это покрыть, ничего не теряется; алерт по lag, а не только по доле ошибок.
- Часы тишины и предпочтения, читаемые с primary на каждое сообщение: их нужно кешировать ([[caching]]) и инвалидировать при изменении предпочтений.
- Связанные заметки: [[async-processing-and-event-driven-scaling]], [[system-design-basics]], [[design-rate-limiter]].
