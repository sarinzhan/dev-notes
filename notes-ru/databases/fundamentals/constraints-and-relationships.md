---
title: Ограничения и связи между таблицами
tags: [sql, constraints, foreign-keys, schema-design]
learned: 2026-10-22
---
## Кратко
Ограничения (constraints) — это правила, которые СУБД проверяет при каждой записи: `PRIMARY KEY`, `FOREIGN KEY`, `UNIQUE`, `NOT NULL`, `CHECK`, `DEFAULT` (и `EXCLUDE` в Postgres). Именно они делают данные надёжными, независимо от того, какое приложение или скрипт их пишет. Связи между таблицами выражаются внешними ключами (foreign keys): один-к-одному (внешний ключ, который также уникален), один-ко-многим (внешний ключ на стороне «многих»), многие-ко-многим (связующая таблица с двумя внешними ключами). Название «реляционная» происходит именно от этих отношений (relations).

## Основное

### Ограничения
| Ограничение | Что гарантирует | Примечания |
|---|---|---|
| `PRIMARY KEY` | уникальный и непустой идентификатор каждой строки; один на таблицу | Postgres создаёт уникальный B-tree-индекс. Лучше суррогатный `bigint GENERATED ALWAYS AS IDENTITY` или `uuid`; естественные ключи (msisdn, email) меняются |
| `FOREIGN KEY ... REFERENCES` | значение существует в PK/уникальном столбце связанной таблицы (ссылочная целостность, referential integrity) | связанный столбец должен быть PK или UNIQUE; сам столбец внешнего ключа в Postgres автоматически **не** индексируется: индекс нужно добавить |
| `UNIQUE` | никакие две строки не имеют одинакового значения (значений); `NULL` не конфликтуют (`NULLS NOT DISTINCT` в PG 15 это меняет) | реализуется уникальным индексом; также это половина ключей [[idempotency]] на стороне БД |
| `NOT NULL` | значение обязательно | самое дешёвое ограничение; при добавлении к большой таблице она сканируется целиком (PG 12+ может пропустить сканирование, если есть проверенный `CHECK (col IS NOT NULL)`) |
| `CHECK` | произвольное логическое условие на строку: `CHECK (amount > 0)`, `CHECK (status IN ('NEW','PAID'))` | не может ссылаться на другие строки или таблицы; для этого нужны триггеры или `EXCLUDE` |
| `DEFAULT` | значение, если в insert столбец не указан | строго говоря, не ограничение; `DEFAULT now()`, `DEFAULT gen_random_uuid()` |
| `EXCLUDE` (Postgres) | никакие две строки не удовлетворяют оператору по индексу, например пересекающиеся временные диапазоны | `EXCLUDE USING gist (room_id WITH =, during WITH &&)` |

- Ограничения могут быть `DEFERRABLE INITIALLY DEFERRED`: проверяются при commit, а не после каждого оператора. Полезно для циклических внешних ключей или обмена уникальными значениями.
- Добавление внешнего ключа или CHECK к большой таблице: `ADD CONSTRAINT ... NOT VALID` (мгновенно, действует для новых строк), а позже `VALIDATE CONSTRAINT` (сканирует с более слабой блокировкой).

### Действия внешнего ключа
Что происходит с дочерними строками, когда связанная родительская строка удаляется или её ключ меняется (`ON DELETE` / `ON UPDATE`):
- `NO ACTION` (по умолчанию) / `RESTRICT` — отклонить удаление или обновление, пока существуют зависимые строки. Разница: `RESTRICT` проверяет сразу, а `NO ACTION` можно отложить до конца оператора/транзакции.
- `CASCADE` — удалить или обновить и зависимые строки. Удобно для принадлежащих данных (`order_lines` заказа), опасно для общих ссылок.
- `SET NULL` — столбец внешнего ключа становится `NULL` (столбец должен допускать NULL).
- `SET DEFAULT` — столбец внешнего ключа получает своё значение `DEFAULT` (которое само должно существовать в родительской таблице).
- JPA `CascadeType.REMOVE`/`orphanRemoval` — аналог на уровне приложения, и он не создаёт `ON DELETE CASCADE` на уровне БД; эти два механизма должны быть согласованы (см. [[jpa-entities-and-relationships]]).

### Связи
| Связь | Схема | Пример | JPA |
|---|---|---|---|
| **Один-к-одному** | внешний ключ в одной таблице с ограничением `UNIQUE` (или общий PK) | `customers` ↔ `customer_profiles` | `@OneToOne @JoinColumn(unique = true)`; часто лучше сделать столбцами той же таблицы, если дополнительные данные не большие и не необязательные |
| **Один-ко-многим** | внешний ключ на стороне «многих», указывающий на сторону «одного» | один `customer` → много `orders` (`orders.customer_id`) | `@ManyToOne` на `Order` — владеющая сторона; `@OneToMany(mappedBy)` на `Customer` |
| **Многие-ко-многим** | **связующая таблица (junction table)** с двумя внешними ключами и составным PK или уникальной парой | `subscriptions` ↔ `services` через `subscription_services(subscription_id, service_id)` | `@ManyToMany @JoinTable`; превращать в сущность, как только у связи появляются свои столбцы (`activated_at`) |
| Ссылка на себя | внешний ключ на ту же таблицу | `employees.manager_id` | `@ManyToOne Employee manager` |
| Полиморфная | `target_type` + `target_id` без настоящего внешнего ключа | комментарии к заказам или тикетам | избегать; использовать отдельную таблицу на каждую цель или маппинг наследования |

- Направление внешнего ключа — «многие указывают на одного»; у БД нет понятия «владеющей стороны», кроме того, где находится столбец. Это понятие добавляют ORM.
- Составные первичные ключи в связующих таблицах (`PRIMARY KEY (subscription_id, service_id)`) дают уникальность и индекс для поиска по первому столбцу; для обратного направления нужен второй индекс по `(service_id)`.

## Пример
```sql
CREATE TABLE subscriptions (
    id            bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    customer_id   bigint NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    tariff_id     bigint NOT NULL REFERENCES tariffs(id),
    status        varchar(16) NOT NULL DEFAULT 'NEW'
                  CHECK (status IN ('NEW', 'ACTIVE', 'SUSPENDED', 'CLOSED')),
    activated_at  timestamptz,
    closed_at     timestamptz,
    CHECK (closed_at IS NULL OR activated_at IS NOT NULL),
    CHECK (closed_at IS NULL OR closed_at >= activated_at)
);
CREATE INDEX subscriptions_customer_idx ON subscriptions (customer_id);  -- столбцы внешних ключей по умолчанию не индексируются

-- один-к-одному: профиль, который может существовать, а может и нет
CREATE TABLE esim_profiles (
    subscription_id bigint PRIMARY KEY REFERENCES subscriptions(id) ON DELETE CASCADE,
    iccid           varchar(22) NOT NULL UNIQUE,
    eid             varchar(32)
);

-- многие-ко-многим с атрибутами на связи
CREATE TABLE subscription_services (
    subscription_id bigint NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    service_id      bigint NOT NULL REFERENCES services(id),
    enabled_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (subscription_id, service_id)
);
CREATE INDEX subscription_services_service_idx ON subscription_services (service_id);
```

## Подводные камни
- Неиндексированные столбцы внешних ключей: каждый `DELETE` в родительской таблице делает последовательное сканирование дочерней, чтобы проверить `RESTRICT`, и каждый join по внешнему ключу медленный. Postgres **не** создаёт этот индекс за тебя (MySQL InnoDB создаёт).
- `ON DELETE CASCADE` через несколько уровней может удалить намного больше, чем задумано, и долго держать блокировки; для всего, что не принадлежит строго, лучше `RESTRICT` + явная очистка.
- Проверка «статус должен быть одним из» только в Java enum: первый ручной `UPDATE` или другой сервис её нарушит. Нужен ещё `CHECK` или справочная таблица в БД.
- `UNIQUE` на столбце, допускающем NULL, разрешает сколько угодно `NULL`; используй `NULLS NOT DISTINCT` (PG 15) или частичный уникальный индекс `WHERE col IS NOT NULL`.
- Удалить ограничение «временно» для массовой загрузки и забыть вернуть; вместо этого используй `DEFERRABLE` или `NOT VALID` + `VALIDATE`.
- Устройство схемы, стоящей за этими правилами: [[normalization-and-denormalization]]; индексы, которые создают ограничения, и те, что ещё нужны: [[database-indexes]].
