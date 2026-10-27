---
title: Postgres locks: row, table, advisory, and deadlocks
tags: [postgres, locks, concurrency, deadlock]
learned: 2026-10-27
---
## TL;DR
Postgres takes **row locks** for `UPDATE`/`DELETE`/`SELECT ... FOR UPDATE` and **table locks** for every statement, including plain `SELECT` (`ACCESS SHARE`) and DDL (`ACCESS EXCLUSIVE`). Readers and writers do not block each other thanks to MVCC, but two writers of the same row serialize, and DDL waits for everyone and makes everyone wait behind it. Deadlocks are detected after `deadlock_timeout` (1 s) and one transaction is killed; `lock_timeout` prevents a statement from queueing forever. **Advisory locks** are application-defined locks on an arbitrary 64-bit key, handy for "one worker per job". The isolation side of concurrency is in [[postgres-isolation-levels]].

## Key points

### Row-level locks
| Mode | Taken by | Conflicts with |
|---|---|---|
| `FOR UPDATE` | `UPDATE`, `DELETE`, `SELECT ... FOR UPDATE` | all other row locks |
| `FOR NO KEY UPDATE` | `UPDATE` that does not change key columns | everything except `FOR KEY SHARE` |
| `FOR SHARE` | `SELECT ... FOR SHARE` | `FOR UPDATE`, `FOR NO KEY UPDATE` |
| `FOR KEY SHARE` | foreign key checks on insert/update of a referencing row | `FOR UPDATE` only |

- Row locks live in the tuple header (`xmax` plus flags), not in shared memory, so locking a million rows costs disk writes, not lock-table slots; the lock on a row is released at transaction end, never earlier.
- `SELECT ... FOR UPDATE` is pessimistic locking: read the row, hold it, update it, commit. Under READ COMMITTED the statement re-evaluates the `WHERE` after waiting; under REPEATABLE READ it errors with `could not serialize access due to concurrent update`. JPA exposes it as `PESSIMISTIC_WRITE` ([[jpa-locking]]).
- `FOR UPDATE SKIP LOCKED` skips rows another session holds: the building block for job queues and the [[outbox-pattern]] relay. `NOWAIT` errors instead of waiting.
- A foreign key makes inserts into the child table take `FOR KEY SHARE` on the parent row; an `UPDATE` of a non-key column on the parent takes `FOR NO KEY UPDATE` and is compatible with it. Updating the parent's primary key is not.

### Table-level locks
- Every statement takes a lock on each table it uses; the modes form a conflict matrix. The ones that matter in practice:
  - `ACCESS SHARE` (`SELECT`): conflicts only with `ACCESS EXCLUSIVE`.
  - `ROW EXCLUSIVE` (`INSERT`/`UPDATE`/`DELETE`): conflicts with `SHARE` (plain `CREATE INDEX`) and stronger.
  - `SHARE UPDATE EXCLUSIVE` (`VACUUM`, `CREATE INDEX CONCURRENTLY`, `ALTER TABLE ... SET`): self-conflicting, lets reads and writes through.
  - `ACCESS EXCLUSIVE` (`ALTER TABLE` adding a column with a volatile default, `DROP`, `TRUNCATE`, `VACUUM FULL`, `LOCK TABLE`): conflicts with everything, including `SELECT`.
- Lock requests queue in order. An `ALTER TABLE` waiting for a long `SELECT` to finish blocks every subsequent `SELECT` behind it, even though those would be compatible with the running one. A 50 ms migration can stop a service for the duration of the longest open transaction. Always run DDL with `lock_timeout` and retry ([[schema-migrations-flyway-liquibase]]).

### Timeouts and diagnostics
- `lock_timeout`: abort a statement that waits for a lock longer than this. Set it per migration session (`SET lock_timeout = '3s'`) and leave it off for normal traffic, or set a generous global value.
- `statement_timeout` bounds the whole statement; `idle_in_transaction_session_timeout` kills sessions that hold locks while doing nothing (a `@Transactional` method waiting on an HTTP call).
- `pg_locks` joined with `pg_stat_activity` shows who holds and who waits; `pg_blocking_pids(pid)` returns the blockers directly. `log_lock_waits = on` writes a line for every wait longer than `deadlock_timeout`.

### Deadlocks
- Two transactions each hold a row the other needs: T1 updates row A then row B, T2 updates B then A. Postgres checks the wait-for graph after `deadlock_timeout` and aborts one with `SQLSTATE 40P01`; the other proceeds. The application must retry the aborted transaction.
- Prevention: lock rows in a consistent order (sort ids before `UPDATE ... WHERE id IN (...)` or lock them with `SELECT ... FOR UPDATE ORDER BY id`), keep transactions short, avoid updating parent and child tables in different orders across code paths. Foreign-key checks are a frequent hidden participant.
- Deadlocks across a database and something else (a JVM `synchronized` block or a Redis lock) are not detected by anyone; see [[deadlocks-and-synchronization-primitives]].

### Advisory locks
- `pg_advisory_lock(key)` / `pg_try_advisory_lock(key)` (session-scoped, must be unlocked) and `pg_advisory_xact_lock(key)` (released at commit) lock an application-defined `bigint` or `(int, int)` pair with no table involved.
- Uses: one scheduler instance runs the nightly job (`SELECT pg_try_advisory_lock(hashtext('billing-run'))`), serialize all writes for one subscriber without locking rows that may not exist yet, prevent duplicate inserts before a unique constraint is possible. It is a single-database lock; as a cross-service distributed lock it has the same fencing problems as any lease ([[distributed-locks-and-fencing]]).
- Session-level advisory locks survive transaction rollback and are released when the connection closes; with a connection pool a leaked lock stays held by a pooled connection forever. Prefer the `_xact_` variants.

## Example
```sql
-- Find what blocks what (run during an incident)
SELECT blocked.pid       AS blocked_pid,
       blocked.query     AS blocked_query,
       blocking.pid      AS blocking_pid,
       blocking.state    AS blocking_state,
       now() - blocking.xact_start AS blocking_xact_age
FROM pg_stat_activity blocked
JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS b(pid) ON true
JOIN pg_stat_activity blocking ON blocking.pid = b.pid
WHERE cardinality(pg_blocking_pids(blocked.pid)) > 0;
--  blocked_pid | blocked_query                      | blocking_pid | blocking_state      | blocking_xact_age
--  5120        | ALTER TABLE sim ADD COLUMN eid text| 4982         | idle in transaction | 00:04:12
-- Fix: SELECT pg_terminate_backend(4982); then rerun the migration with lock_timeout.

-- Deadlock-free batch update: lock in a fixed order first
BEGIN;
SELECT id FROM accounts WHERE id IN (17, 3, 42) ORDER BY id FOR UPDATE;
UPDATE accounts SET balance = balance - 10 WHERE id = 42;
UPDATE accounts SET balance = balance + 10 WHERE id = 3;
COMMIT;
```

## Pitfalls
- A migration without `lock_timeout` queues behind one long transaction and takes the service down; with `lock_timeout = '3s'` it fails fast and can be retried.
- `SELECT ... FOR UPDATE` on a parent row to "serialize" inserts into a child table works, but `FOR SHARE` taken by FK checks from other sessions still conflicts with it under load.
- `pg_advisory_lock` with HikariCP: the lock is tied to the physical connection, not to your thread; an exception before `pg_advisory_unlock` leaves it held.
- `deadlock_timeout` is also the threshold for `log_lock_waits`; lowering it to detect deadlocks faster adds overhead to every lock wait.
- Related: [[jpa-locking]] for the ORM side, [[postgres-isolation-levels]] for serialization failures that look like lock errors, [[race-conditions-and-critical-sections]] for the general concept.
