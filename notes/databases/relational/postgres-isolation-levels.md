---
title: Transaction isolation levels in Postgres
tags: [postgres, transactions, concurrency, mvcc]
learned: 2026-09-12
---
## TL;DR
Postgres uses MVCC, so readers never block writers. The default level is **READ COMMITTED**: each statement sees a fresh snapshot. **REPEATABLE READ** freezes the snapshot per transaction, **SERIALIZABLE** additionally detects write skew and aborts with `40001`, which the application must retry.

## Key points
| Level | Snapshot | Phantom reads | Write skew | Typical use |
|---|---|---|---|---|
| READ COMMITTED | per statement | possible | possible | default OLTP |
| REPEATABLE READ | per transaction | no (in PG) | possible | reports, consistent reads |
| SERIALIZABLE | per transaction + SSI | no | detected, aborts | money, inventory |

- Postgres has no READ UNCOMMITTED; it is mapped to READ COMMITTED.
- `SELECT ... FOR UPDATE` is the pessimistic alternative under READ COMMITTED.
- Spring maps `Isolation.SERIALIZABLE` in [[spring-transactional|@Transactional]]; retries need `@Retryable` on `CannotSerializeTransactionException`.

## Example
```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;
SELECT balance FROM accounts WHERE id = 42;
UPDATE accounts SET balance = balance - 100 WHERE id = 42;
COMMIT; -- may fail with SQLSTATE 40001: retry the whole transaction
```

## Pitfalls
- Write skew: two transactions each read "doctor on call = 2", both go off call, result 0. Only SERIALIZABLE catches it.
- Long transactions hold back vacuum. Watch `pg_stat_activity.xact_start`.
- Related: [[postgres-indexes]], [[idempotency]]
