---
title: Моделирование данных для сервисов: владение, агрегаты, мягкое удаление, аудит
tags: [data-modeling, microservices, postgres, schema-design]
learned: 2026-10-27
---
## Кратко
Каждый сервис владеет своей базой данных (или хотя бы своей схемой) и отдаёт данные только через свой API или события; никакой другой сервис не читает его таблицы. Внутри схемы моделируй вокруг агрегатов: одна корневая таблица на агрегат, дочерние таблицы для частей, которые живут и умирают вместе с ним, ссылки на другие агрегаты по id без внешних ключей через границы сервисов. Добавляй операционные столбцы с первого дня (`created_at`, `updated_at`, `version`, кто изменил), осознанно выбирай между мягким и жёстким удалением и держи машины состояний явными. Правила нормализации — в [[normalization-and-denormalization]]; эта заметка — о соглашениях на уровне сервиса.

## Основное

### База данных на сервис
- Общая база у нескольких сервисов связывает их схемы: переименование столбца в одном требует deploy всех, а медленный запрос в одном истощает пул соединений другого ([[monolith-vs-microservices]]).
- Варианты от мягкого к строгому: отдельная схема в одном кластере Postgres с ролями для каждого сервиса (дёшево, доступ обеспечивается через `GRANT`), отдельная база данных, отдельный кластер (нужен, когда надо изолировать нагрузку или домены отказов).
- Данные, нужные другому сервису, приходят через вызов API или как копия из события ([[event-driven-architecture]], [[outbox-pattern]]). Копия — это локальная проекция только для чтения в собственной таблице; она может устареть, и её никогда нельзя изменять локально. Храни id из источника как ключ соединения.
- Отчёты и аналитика читают из реплики или хранилища данных, наполняемого через CDC, но никогда не из основной базы сервиса ([[postgres-replication-and-failover]]).

### Агрегаты как таблицы
- Агрегат ([[aggregates-and-invariants]]) — единица транзакции: `esim_order` с дочерними `esim_order_item`. У дочерних таблиц есть внешний ключ на корень с `ON DELETE CASCADE`, и к ним обращаются только через корень; на другой агрегат (`subscriber`) ссылаются через `subscriber_id bigint NOT NULL` без внешнего ключа, если он живёт в другом сервисе, и с внешним ключом, если здесь ([[constraints-and-relationships]]).
- Одна транзакция затрагивает один агрегат; изменения между агрегатами идут через события или сагу ([[saga]]). Это делает блокировки строк короткими и позже превращает агрегат в естественный ключ партиционирования ([[sharding-strategies]]).
- Инварианты, которые база данных может выразить, оформляй как ограничения: `CHECK (quantity > 0)`, `UNIQUE (subscriber_id, iccid)`, исключающие ограничения (exclusion constraints) для непересекающихся диапазонов. База данных — последняя линия обороны от конкурентных запросов, которые не предусмотрела логика сервиса.

### Стандартные столбцы
| Столбец | Тип | Зачем |
|---|---|---|
| `id` | `bigint identity` или `uuid` (v7) | см. [[time-money-and-ids]] |
| `created_at`, `updated_at` | `timestamptz NOT NULL DEFAULT now()` | отладка, сроки хранения, упорядочивание; `updated_at` через триггер или `@UpdateTimestamp` в ORM |
| `version` | `int NOT NULL DEFAULT 0` | оптимистическая блокировка (`@Version`), [[jpa-locking]] |
| `created_by`, `updated_by` | `text` или id пользователя | аудит; `@CreatedBy` в Spring Data с `AuditorAware` |
| `status` | `text` + `CHECK` или таблица-справочник | явная машина состояний; избегай размножающихся булевых флагов (`is_active`, `is_blocked`, `is_deleted`) |
| `external_ref` / `idempotency_key` | `text UNIQUE` | дедупликация входящих запросов и сопоставление с системами партнёров ([[idempotency]]) |

- Предпочитай `text` с `CHECK` типу `enum` в Postgres: добавление значения enum — это DDL, которое в старых версиях нельзя выполнить внутри транзакции, и значение неудобно удалять; таблица-справочник лучше, когда у значений есть атрибуты.

### Мягкое удаление против жёсткого
- **Мягкое удаление** (soft delete; `deleted_at timestamptz NULL`): сохраняет историю и ссылки, позволяет отменить удаление, нужно для аудита и сроков хранения по закону. Цена: каждый запрос должен фильтровать `deleted_at IS NULL` (Hibernate `@SQLRestriction` или представление), уникальным ограничениям нужен частичный индекс `WHERE deleted_at IS NULL`, внешние ключи по-прежнему указывают на «удалённые» строки, а таблицы никогда не уменьшаются.
- **Жёсткое удаление** (hard delete) с **таблицей аудита/архива** или событием: запросы проще, реальная очистка, удаление по GDPR — настоящее удаление. Цена: каскадные удаления нужно проектировать, а история живёт в другом месте.
- Эмпирическое правило: мягкое удаление для сущностей, видимых пользователю, которые можно восстановить или нужно показывать в истории (заказы, подписки); жёсткое удаление плюс аудит для временных или массовых данных (сессии, токены, события); партиционирование и удаление партиций для логов ([[postgres-partitioning]]).

### Аудит и история
- Минимум: столбцы `created_*`/`updated_*` плюс логи приложения с trace id ([[observability-three-pillars]]).
- История строк: таблица `*_history`, заполняемая триггером или приложением (Hibernate Envers пишет таблицы `_AUD`), или таблица доменных событий только на добавление, которая заодно служит outbox. Темпоральные таблицы с `tstzrange` плюс исключающее ограничение дают запросы «по состоянию на».
- Никогда не делай аудит перезаписью: `UPDATE ... SET status = 'CANCELLED'` без строки истории теряет, кто это сделал и когда.

### Как избежать типичных форм
- Таблицы «сущность-атрибут-значение» (`entity_id, key, value`) ради «гибкости»: используй `jsonb` для действительно разреженных атрибутов и столбцы для всего остального ([[postgres-jsonb-and-full-text]]).
- Строка `settings` на арендатора с 80 nullable-столбцами: раздели по областям ответственности или используй `jsonb` с проверкой схемы.
- Id через запятую в текстовом столбце: столбец-массив с GIN-индексом или нормальная связующая таблица.
- Столбцы для мультиарендности и уровни изоляции — отдельное решение ([[multi-tenancy-strategies]]).

## Пример
```sql
-- V3__esim_order_aggregate.sql
CREATE TABLE esim_order (
    id              uuid        PRIMARY KEY,                -- UUID v7, генерируется приложением
    subscriber_id   bigint      NOT NULL,                   -- другой сервис: без FK, только id
    idempotency_key text        NOT NULL,
    status          text        NOT NULL DEFAULT 'NEW'
                    CHECK (status IN ('NEW','RESERVED','DOWNLOADING','ACTIVE','FAILED','CANCELLED')),
    operator_attrs  jsonb       NOT NULL DEFAULT '{}',
    version         int         NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text        NOT NULL,
    deleted_at      timestamptz
);
CREATE UNIQUE INDEX esim_order_idem_uq ON esim_order (idempotency_key) WHERE deleted_at IS NULL;
CREATE INDEX esim_order_subscriber_idx ON esim_order (subscriber_id, created_at DESC) WHERE deleted_at IS NULL;

CREATE TABLE esim_order_item (
    id          bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id    uuid    NOT NULL REFERENCES esim_order(id) ON DELETE CASCADE,
    iccid       text    NOT NULL,
    profile     text    NOT NULL,
    UNIQUE (order_id, iccid)
);
CREATE INDEX esim_order_item_order_idx ON esim_order_item (order_id);   -- столбцы FK не индексируются автоматически

CREATE TABLE esim_order_status_history (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id    uuid        NOT NULL REFERENCES esim_order(id) ON DELETE CASCADE,
    from_status text,
    to_status   text        NOT NULL,
    changed_by  text        NOT NULL,
    changed_at  timestamptz NOT NULL DEFAULT now(),
    reason      text
);
```

## Подводные камни
- Внешние ключи через границы сервисов (даже если обе схемы в одном кластере): следующая команда, которая будет разделять кластер, найдёт висячие ссылки и миграцию, которую никто не планировал.
- Мягкое удаление без частичных уникальных индексов: «удалённый» email блокирует повторную регистрацию или, что хуже, уникальность убирают, чтобы её разрешить.
- Использовать один столбец статуса для двух задач (`status = 'PAID_BUT_NOT_SHIPPED'`): состояния размножаются; моделируй `payment_status` и `fulfilment_status` отдельно.
- Забыть про поведение `ON DELETE`: значение по умолчанию `NO ACTION` блокирует удаление корня с дочерними строками, а каскад не на том FK стирает историю.
- Безопасная эволюция этой схемы: [[schema-migrations-flyway-liquibase]]; основы DDD: [[aggregates-and-invariants]], [[bounded-contexts-and-context-mapping]].
