---
title: Категории команд SQL и типы join
tags: [sql, joins, ddl, dml]
learned: 2026-10-22
---
## Кратко
SQL-операторы делятся на DDL (определение структуры: `CREATE`, `ALTER`, `DROP`), DML (изменение строк: `INSERT`, `UPDATE`, `DELETE`; `SELECT` иногда выделяют отдельно как DQL), DCL (права: `GRANT`, `REVOKE`) и TCL (`BEGIN`, `COMMIT`, `ROLLBACK`, `SAVEPOINT`). Соединения (join) объединяют строки двух таблиц по условию: `INNER` оставляет только совпадения, `LEFT`/`RIGHT` оставляют все строки одной стороны с `NULL` на месте отсутствующей другой, `FULL` оставляет всё, `CROSS` даёт все комбинации.

## Основное

### Категории команд
| Группа | Операторы | Примечания |
|---|---|---|
| **DDL** (определение данных) | `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `RENAME` | В Postgres DDL транзакционный: неудачная миграция откатывает и `CREATE TABLE` (MySQL и Oracle делают auto-commit DDL). `TRUNCATE` похож на DDL: быстрый, сбрасывает таблицу, не вызывает построчных триггеров `DELETE` |
| **DML** (манипулирование данными) | `INSERT`, `UPDATE`, `DELETE`, `MERGE` (Postgres 15+), `INSERT ... ON CONFLICT` (upsert) | Изменения строк, записываются в WAL, подчиняются ограничениям и триггерам |
| **DQL** (запрос данных) | `SELECT` | Часто относят к DML; только чтение |
| **DCL** (управление доступом) | `GRANT`, `REVOKE` | Права на таблицы, представления, схемы, последовательности; роли создаются через `CREATE ROLE` (DDL) |
| **TCL** (управление транзакциями) | `BEGIN`/`START TRANSACTION`, `COMMIT`, `ROLLBACK`, `SAVEPOINT`, `ROLLBACK TO SAVEPOINT`, `SET TRANSACTION ISOLATION LEVEL` | См. [[transactions-acid-and-isolation]]; Spring выполняет их за тебя через [[spring-transactional]] |

- Логический порядок `SELECT`: `FROM` → `JOIN` → `WHERE` → `GROUP BY` → `HAVING` → `SELECT` → `DISTINCT` → `ORDER BY` → `LIMIT/OFFSET`. Поэтому псевдоним столбца из `SELECT` нельзя использовать в `WHERE`, и поэтому фильтр по присоединённой таблице в `WHERE` может превратить `LEFT JOIN` во внутренний join.
- `DELETE` против `TRUNCATE`: `DELETE` работает построчно, может иметь `WHERE`, вызывает триггеры, сохраняет последовательность; `TRUNCATE` отбрасывает все страницы, может сделать `RESTART IDENTITY`, требует эксклюзивной блокировки.

### Типы join
| Join | Строки результата | Типичный вопрос |
|---|---|---|
| `INNER JOIN` | только пары, для которых условие выполняется | клиенты, у которых есть хотя бы один заказ |
| `LEFT [OUTER] JOIN` | все строки левой таблицы; столбцы правой — `NULL`, если совпадения нет | все клиенты и их заказы, включая клиентов без заказов |
| `RIGHT [OUTER] JOIN` | зеркало LEFT; для читаемости лучше переписать как LEFT | используется редко |
| `FULL [OUTER] JOIN` | все строки с обеих сторон, дополненные `NULL` | сверка: строки, которые есть только с одной стороны |
| `CROSS JOIN` | каждая строка A × каждая строка B | генерация комбинаций (даты × тарифы) |
| `SELF JOIN` | таблица соединяется сама с собой | сотрудник → руководитель в той же таблице |
| `LATERAL` join | правая сторона может ссылаться на строку левой (коррелированный подзапрос во `FROM`) | «последние 3 заказа каждого клиента» |
| semi/anti join | `WHERE EXISTS` / `WHERE NOT EXISTS` | фильтр по существованию без дублирования строк |

- Идиома anti-join: `LEFT JOIN b ON ... WHERE b.id IS NULL` возвращает левые строки без совпадения. `NOT IN (subquery)` не возвращает ничего, если в подзапросе есть `NULL`; лучше `NOT EXISTS`.
- `USING (customer_id)` вместо `ON a.customer_id = b.customer_id`, когда имена столбцов совпадают; `NATURAL JOIN` соединяет по всем одноимённым столбцам и молча ломается при добавлении столбца.
- Алгоритмы join, которые выбирает планировщик (видны в `EXPLAIN`): **nested loop** (маленькая внешняя сторона × индексированная внутренняя), **hash join** (построить хеш меньшей стороны; нужен `work_mem`), **merge join** (обе стороны отсортированы по ключу). Выбираешь их не ты; от тебя — [[database-indexes]] и точная статистика.
- Соединить много строк в SQL почти всегда дешевле, чем получить id и крутить цикл в Java; этот цикл и есть проблема [[jpa-n-plus-one]].

## Пример
```sql
-- Все клиенты, их активная подписка (если есть) и последний платёж по подписке
SELECT c.id,
       c.msisdn,
       t.code                      AS tariff,
       lp.paid_at                  AS last_payment_at,
       lp.amount                   AS last_payment
FROM customers c
LEFT JOIN subscriptions s
       ON s.customer_id = c.id AND s.status = 'ACTIVE'   -- фильтр здесь, а не в WHERE, чтобы сохранить LEFT
LEFT JOIN tariffs t
       ON t.id = s.tariff_id
LEFT JOIN LATERAL (
       SELECT p.paid_at, p.amount
       FROM payments p
       WHERE p.subscription_id = s.id
       ORDER BY p.paid_at DESC
       LIMIT 1
) lp ON true
WHERE c.created_at >= date '2026-01-01'
ORDER BY c.id;

-- Клиенты, которые ни разу не платили (anti-join)
SELECT c.id FROM customers c
WHERE NOT EXISTS (SELECT 1 FROM payments p WHERE p.customer_id = c.id);
```

## Подводные камни
- `LEFT JOIN b ... WHERE b.status = 'X'` молча отбрасывает левые строки без совпадения (`NULL = 'X'` не истинно); перенеси условие в `ON`.
- Join по связи один-ко-многим с последующими `COUNT(*)` или `SUM` считает родителя несколько раз; агрегируй в подзапросе или используй `COUNT(DISTINCT ...)`.
- `SELECT *` по join возвращает повторяющиеся имена столбцов (`id`, `id`), что ломает JDBC `getLong("id")` и нативные маппинги JPA.
- DDL в Postgres берёт блокировку `ACCESS EXCLUSIVE` на таблицу; `ALTER TABLE ... ADD COLUMN ... DEFAULT` с PG 11 мгновенный, но добавление `NOT NULL` с проверкой всё равно перезаписывает таблицу; `CREATE INDEX` требует `CONCURRENTLY` (см. [[postgres-indexes]]).
- Как ключи вообще делают join корректным: [[constraints-and-relationships]]; какой должна быть схема до join: [[normalization-and-denormalization]].
