---
title: Паттерн transactional outbox
tags: [messaging, kafka, reliability, postgres]
learned: 2026-08-20
---
## Кратко
Изменение домена и событие для публикации записываются в **одной транзакции базы данных** (таблица `outbox`). Отдельный relay читает outbox и публикует в Kafka. Это устраняет проблему двойной записи (dual write): событие существует тогда и только тогда, когда бизнес-изменение закоммичено. Доставка становится **at-least-once**, поэтому consumer-ы должны быть [[idempotency|идемпотентными]].

## Какую проблему решает
- **Без этого:** наивный `orders.save(order); kafka.send(event);` — это двойная запись. Если БД закоммитилась, а отправка упала (брокер недоступен, pod убит между двумя вызовами), ниже по потоку о заказе никто не узнает: тихая потеря данных. Если отправка прошла, а commit упал, consumer-ы реагируют на заказ, которого нет: фантомное событие. Перенос `send()` внутрь метода с `@Transactional` не помогает: отправка всё равно происходит до commit, а соединение с БД теперь удерживается во время сетевого вызова ([[hikaricp-pool-sizing]]).
- **Что меняет:** событие становится строкой в той же локальной транзакции, что и заказ, а relay публикует его позже, поэтому событие существует тогда и только тогда, когда заказ закоммичен. 2PC/XA — не выход: у Kafka нет XA-менеджера ресурсов, а даже с JMS + JDBC 2PC медленный и блокируется при отказе координатора.
- **Когда не нужно:** потерять отдельное событие допустимо (метрики, уведомления по принципу best effort) или у сервиса нет своей базы и он пишет только в Kafka.

## Основное

### Relay: опрашивающий публикатор и CDC (Debezium)
| | Опрашивающий публикатор (polling publisher) | Debezium CDC |
|---|---|---|
| Механизм | Задача `@Scheduled` выбирает неопубликованные строки, отправляет, помечает как опубликованные | Читает WAL Postgres через слот логической репликации, SMT Outbox Event Router превращает строки в записи Kafka |
| Задержка | интервал опроса (100 мс – 1 с) | почти в реальном времени |
| Нагрузка на БД | повторяющиеся запросы, нужен частичный индекс | на таблицу никакой, но есть слот репликации, который нужно потреблять, иначе WAL растёт |
| Эксплуатация | только код приложения | кластер Kafka Connect, конфигурация коннектора, мониторинг слота |
| Масштабирование | `FOR UPDATE SKIP LOCKED` для нескольких экземпляров | один коннектор на базу, параллелизм через задачи Connect |
- С Debezium строку можно удалить сразу после вставки в той же транзакции: WAL всё равно содержит вставку, поэтому таблица по замыслу остаётся пустой.

### Несколько экземпляров relay
- `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 100` позволяет N экземплярам забирать непересекающиеся пачки, не блокируя друг друга. Строки, заблокированные упавшим экземпляром, освобождаются, когда завершается его транзакция.
- Одна транзакция relay на пачку: выбрать, отправить с ожиданием через `get()`/callback, обновить `published_at`, commit. Если pod умирает после отправки, но до обновления, пачка отправляется повторно: это и есть гарантия at-least-once, а не баг.

### Порядок
- Порядок имеет смысл только в пределах агрегата. `aggregate_id` используется как ключ Kafka, чтобы все события одного абонента попадали в одну партицию ([[kafka-producer]]).
- Выбирать по `created_at, id` и отправлять последовательно по ключу; параллельный relay не должен переупорядочивать строки одного агрегата (разбивать пачки по ключу или запускать один экземпляр на таблицу).

### Exactly-once здесь — иллюзия
- Relay даёт at-least-once. Идемпотентный producer Kafka убирает только дубликаты от retry producer-а, но не от повторных запусков relay. Сквозной «exactly-once» на деле — это доставка at-least-once плюс идемпотентный consumer с ключом по `id` из outbox ([[idempotency]]).

### Хранение
- Строки помечаются как опубликованные, а не удаляются по одной. Очищать пачками (`DELETE ... WHERE published_at < now() - interval '7 days' LIMIT ...` через CTE), чтобы избежать долгих блокировок и раздувания (bloat). Частичный индекс по `published_at IS NULL` держит горячий набор маленьким ([[postgres-indexes]]).

## Пример
```java
@Component
@RequiredArgsConstructor
public class OutboxRelay {
    private final JdbcTemplate jdbc;
    private final KafkaTemplate<String, String> kafka;

    @Scheduled(fixedDelay = 500)
    @Transactional
    public void relay() {
        List<OutboxRow> rows = jdbc.query("""
            SELECT id, aggregate_id, type, payload::text
            FROM outbox WHERE published_at IS NULL
            ORDER BY created_at, id
            FOR UPDATE SKIP LOCKED LIMIT 100
            """, (rs, i) -> new OutboxRow(rs.getObject("id", UUID.class),
                 rs.getString("aggregate_id"), rs.getString("type"), rs.getString("payload")));
        for (OutboxRow row : rows) {
            var record = new ProducerRecord<>("esim.events", row.aggregateId(), row.payload());
            record.headers().add("event-id", row.id().toString().getBytes(UTF_8));
            record.headers().add("type", row.type().getBytes(UTF_8));
            kafka.send(record).get(5, TimeUnit.SECONDS);   // блокируемся: ack нужен до пометки
            jdbc.update("UPDATE outbox SET published_at = now() WHERE id = ?", row.id());
        }
    }
}
```
Бизнес-сервис остаётся тривиальным: `@Transactional place(cmd)` сохраняет агрегат и вставляет строку outbox через тот же `EntityManager`/`JdbcTemplate`, поэтому коммитятся либо оба, либо ни один ([[spring-transactional]]).

## Подводные камни
- Запись строки outbox вне границы транзакции (например, в `@TransactionalEventListener(AFTER_COMMIT)`) возвращает двойную запись.
- `kafka.send()` без ожидания ack с последующей пометкой строки как опубликованной превращает at-least-once в at-most-once.
- Опрос с `ORDER BY created_at` без `SKIP LOCKED`: второй экземпляр блокируется на строках первого, и весь параллелизм теряется.
- Непотребляемый слот Debezium (коннектор упал) держит WAL на диске, пока том не заполнится. Нужен алерт на lag в `pg_replication_slots`.
- Схема JSON payload развивается; поле `version` стоит заложить с первого дня.
- Связанные заметки: [[saga]] использует outbox на каждом шаге, [[kafka-architecture]] — про семантику партиций, [[esim-provisioning-platform]] — реальное внедрение.
