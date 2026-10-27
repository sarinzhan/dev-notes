---
title: Уровни изоляции транзакций в Postgres
tags: [postgres, transactions, concurrency, mvcc]
learned: 2026-09-12
---
## Кратко
Postgres использует MVCC, поэтому читатели никогда не блокируют писателей. Уровень по умолчанию — **READ COMMITTED**: каждый оператор видит свежий снимок. **REPEATABLE READ** фиксирует снимок на всю транзакцию, **SERIALIZABLE** дополнительно обнаруживает асимметрию записи (write skew) и прерывает транзакцию с `40001`, которую приложение должно повторить.

## Основное
| Уровень | Снимок | Фантомное чтение | Write skew | Типичное применение |
|---|---|---|---|---|
| READ COMMITTED | на оператор | возможно | возможна | OLTP по умолчанию |
| REPEATABLE READ | на транзакцию | нет (в PG) | возможна | отчёты, согласованное чтение |
| SERIALIZABLE | на транзакцию + SSI | нет | обнаруживается, прерывание | деньги, складские остатки |

- READ UNCOMMITTED в Postgres нет; он отображается на READ COMMITTED.
- `SELECT ... FOR UPDATE` — пессимистичная альтернатива при READ COMMITTED.
- Spring отображает `Isolation.SERIALIZABLE` в [[spring-transactional|@Transactional]]; для повторов нужен `@Retryable` на `CannotSerializeTransactionException`.

## Пример
```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;
SELECT balance FROM accounts WHERE id = 42;
UPDATE accounts SET balance = balance - 100 WHERE id = 42;
COMMIT; -- может упасть с SQLSTATE 40001: повтори всю транзакцию
```

## Подводные камни
- Асимметрия записи: две транзакции каждая читают «дежурных врачей = 2», обе снимаются с дежурства, в итоге 0. Ловит это только SERIALIZABLE.
- Длинные транзакции сдерживают vacuum. Следи за `pg_stat_activity.xact_start`.
- Связанные заметки: [[postgres-indexes]], [[idempotency]]
