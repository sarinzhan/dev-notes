---
title: CQRS — раздельные модели чтения и записи
tags: [cqrs, architecture, microservices, postgres]
learned: 2026-10-27
---
## Кратко
CQRS (command query responsibility segregation, разделение ответственности команд и запросов) делит API сервиса на **команды**, которые меняют состояние через доменную модель, и **запросы**, которые читают из модели, сформированной под экран или отчёт. Сторона записи остаётся маленькой и сосредоточенной на инвариантах ([[aggregates-and-invariants]]); стороной чтения может быть денормализованная таблица, материализованное представление, Elasticsearch или Redis. Простейший CQRS — два класса-обработчика над одной базой Postgres; полная форма имеет отдельное хранилище для чтения, наполняемое событиями, и согласована в конечном счёте. Большинству систем нужна простая форма; полная оправдана только реальной асимметрией чтения и записи.

## Какую проблему решает
- **Без этого:** одна JPA-модель обслуживает и чтение, и запись. Стороне записи нужен маленький агрегат с инвариантами; «странице абонента» — SIM + активный профиль + тариф + последние 5 событий в одной строке. Один граф сущностей для обоих даёт [[jpa-n-plus-one]] на чтении и гигантские агрегаты на записи. Чтений обычно в 100 раз больше, чем записей, но масштабировать или хранить их отдельно нельзя.
- **Что меняет:** команды идут через агрегат и служат поверхностью аудита (кто что сделал); запросы по определению не меняют состояние и читают модель, сформированную под экран. Эту модель можно кэшировать, реплицировать и шардировать независимо ([[database-scaling-path]]) или держать в другом хранилище — полнотекстовый поиск в Elasticsearch ([[search-and-indexing-services]]), ленты в Redis, аналитику в ClickHouse ([[column-and-wide-column-stores]]), — а модель записи остаётся в Postgres.
- **Когда не нужно:** CRUD, где экран показывает сущность как есть (уровень 0). Большинству остальных сервисов хватает уровня 1 (два обработчика над одной Postgres); отдельное хранилище для чтения окупается только при реальной асимметрии чтения и записи.

## Основное

### Уровни CQRS
| Уровень | Модель записи | Модель чтения | Согласованность | Когда |
|---|---|---|---|---|
| 0: обычный сервис | Сущности | Те же сущности | Немедленная | CRUD |
| 1: раздельные обработчики | Агрегаты через репозиторий | SQL/`JdbcClient` в DTO, та же БД | Немедленная | По умолчанию для любого нетривиального сервиса ([[use-cases-and-application-layer]]) |
| 2: реплика для чтения / представления | Та же | Материализованные представления, реплика для чтения | Отставание реплики (мс–с) | Тяжёлая отчётность, дашборды ([[read-write-splitting-and-cqrs-lite]]) |
| 3: отдельное хранилище для чтения | Та же | Таблицы-проекции, Elasticsearch, Redis, обновляемые из событий | В конечном счёте (с) | Другая форма запросов или масштаб (поиск, ленты) |
| 3 + ES | Хранилище событий | Проекции, восстанавливаемые из событий | В конечном счёте | [[event-sourcing]] |
- CQRS не требует event sourcing, а event sourcing не требует отдельного хранилища для чтения; их часто преподают вместе, потому что при event sourcing запросы к модели записи становятся непрактичными.

### Синхронизация стороны чтения (уровень 3)
- **Та же транзакция**: обработчик команды обновляет и таблицу-проекцию. Немедленная согласованность, просто, но путь записи связывается с каждой проекцией.
- **Доменные события в процессе** (`@TransactionalEventListener`): проекция обновляется после commit; падение между ними теряет обновление, поэтому нужна задача перестроения ([[domain-events-and-integration-events]]).
- **Интеграционные события через outbox + consumer**: проектор — это Kafka consumer, идемпотентный по id события, может работать в другом сервисе, может быть переигран с offset 0 для перестроения ([[outbox-pattern]], [[idempotency]]).
- **CDC** (Debezium на таблицах записи), когда изменить пишущую сторону нельзя.
- Каждую проекцию должно быть можно перестроить с нуля; это запасной выход на случай багов в проекторе.

### Как жить с согласованностью в конечном счёте
- UI отправляет команду, затем читает проекцию, которая ещё не обновилась: классическое «я сохранил, а этого нет». Варианты: возвращать новое состояние в ответе на команду; версионировать чтение («покажи хотя бы версию N», ждать или повторить); оптимистичное обновление UI; или оставить для этого экрана уровень 1.
- Устаревание — продуктовое решение: дашборд поддержки терпит секунды, проверка баланса — нет ([[consistency-models]]).

### Команды и запросы в коде
- Команда: неизменяемое намерение, один обработчик, возвращает void или id; запрос: неизменяемые параметры, один обработчик, возвращает DTO. Крошечный `CommandBus`/`QueryBus` или обычные бины Spring; фреймворки (Axon) необязательны и тяжелы.
- `@Transactional(readOnly = true)` на обработчиках запросов направляет их на реплику, если настроен маршрутизирующий `DataSource`, и позволяет Hibernate пропустить dirty checking ([[spring-transactional]]).

## Пример
Проектор уровня 3: Kafka consumer поддерживает таблицу чтения `subscriber_overview` из событий SIM; обработчик запроса читает её одним `SELECT`.

```java
// Таблица-проекция: одна широкая строка на абонента, никаких join-ов во время запроса
// CREATE TABLE subscriber_overview (subscriber_id uuid PRIMARY KEY, msisdn text, active_iccid text,
//   tariff text, last_event text, last_event_at timestamptz, version bigint NOT NULL);

@Component
@RequiredArgsConstructor
public class SubscriberOverviewProjector {
    private final JdbcClient jdbc;
    private final ProcessedEvents processed;

    @KafkaListener(topics = "esim.sim.events", groupId = "subscriber-overview-projector")
    @Transactional
    public void on(ConsumerRecord<String, byte[]> rec, Acknowledgment ack) {
        UUID eventId = UUID.fromString(header(rec, "event-id"));
        if (!processed.insertIfAbsent(eventId)) { ack.acknowledge(); return; }
        if ("ProfileActivated.v1".equals(header(rec, "type"))) {
            var e = codec.decode(rec.value(), ProfileActivatedV1.class);
            jdbc.sql("""
                INSERT INTO subscriber_overview (subscriber_id, msisdn, active_iccid, last_event, last_event_at, version)
                VALUES (:sid, :msisdn, :iccid, 'ProfileActivated', :at, :v)
                ON CONFLICT (subscriber_id) DO UPDATE
                   SET active_iccid = EXCLUDED.active_iccid, last_event = EXCLUDED.last_event,
                       last_event_at = EXCLUDED.last_event_at, version = EXCLUDED.version
                 WHERE subscriber_overview.version < EXCLUDED.version""")   // игнорировать устаревшие/пришедшие не по порядку
                .param("sid", e.subscriberId()).param("msisdn", e.msisdn()).param("iccid", e.iccid())
                .param("at", e.occurredAt()).param("v", e.aggregateVersion())
                .update();
        }
        ack.acknowledge();
    }
}

@Service
@RequiredArgsConstructor
public class GetSubscriberOverview {
    private final JdbcClient jdbc;

    @Transactional(readOnly = true)
    public Optional<SubscriberOverview> handle(UUID subscriberId) {
        return jdbc.sql("SELECT * FROM subscriber_overview WHERE subscriber_id = :id")
                .param("id", subscriberId).query(SubscriberOverview.class).optional();
    }
}
```

## Подводные камни
- Полный CQRS для админки с CRUD: две модели, проектор, топик Kafka и согласованность в конечном счёте ради таблицы тарифов. Хватило бы уровня 1.
- Проекции без версии агрегата: события, пришедшие не по порядку или переигранные, перезаписывают новое состояние старым. Нужно хранить `version` и сравнивать, как выше.
- Модель чтения, которую никогда не перестраивают: первый же баг проектора портит её навсегда. Перестроение (truncate + replay) нужно оформить скриптом и регулярно запускать на staging.
- Запрос к модели записи «всего один раз» из API чтения: агрегат снова становится API запросов, и разделения больше нет.
- Связанные заметки: [[event-sourcing]], [[event-driven-architecture]], [[read-write-splitting-and-cqrs-lite]], [[design-news-feed]] (fan-out как задача проекции).
