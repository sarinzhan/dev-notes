---
title: Transactions, ACID and isolation anomalies
tags: [transactions, acid, isolation, concurrency]
learned: 2026-10-22
---
## TL;DR
A transaction is a sequence of database operations executed as one unit: all of it commits or none of it does. ACID names the four guarantees (atomicity, consistency, isolation, durability). Isolation is the one with trade-offs: full isolation is expensive, so SQL defines four levels that allow progressively fewer anomalies: dirty read, non-repeatable read, phantom read, and the ones the standard forgot, lost update and write skew. Postgres defaults to READ COMMITTED; how it implements each level is in [[postgres-isolation-levels]].

## Key points

### Logical vs physical transaction
- **Physical transaction**: the DBMS-level unit between `BEGIN` and `COMMIT`/`ROLLBACK` on one connection (TCL, see [[sql-joins-and-commands]]).
- **Logical transaction**: a business operation that must happen as a whole from the domain's point of view ("place order" = insert order, reserve stock, write outbox event). It may span several methods and, with Spring, several `@Transactional` boundaries that *join* the same physical transaction (`REQUIRED`), or deliberately open separate ones (`REQUIRES_NEW` for an audit row that must survive a rollback). Details in [[spring-transactional]].
- A logical transaction across **services** or **systems** (DB + Kafka, DB + payment gateway) cannot be a single physical one; you get at-least-once delivery plus [[idempotency]], the [[outbox-pattern]], or sagas instead of 2PC.

### ACID
- **Atomicity** — the transaction is never applied partially; on failure every change is undone (Postgres: MVCC row versions simply never become visible; the commit record in the WAL is the atomic switch).
- **Consistency** — a committed transaction moves the database from one valid state to another: all constraints ([[constraints-and-relationships]]), triggers and cascades hold. The application is responsible for business invariants the schema cannot express.
- **Isolation** — concurrent transactions do not affect each other's result; the degree is configurable, see below.
- **Durability** — once committed, the change survives a crash: the write-ahead log is `fsync`ed before `COMMIT` returns (`synchronous_commit`), replication makes it survive a disk loss.

### Isolation anomalies
The source conflated some of these; here is the standard set, each with the two-transaction story:
| Anomaly | Story |
|---|---|
| **Dirty read** | T1 updates a row but has not committed; T2 reads the new value; T1 rolls back. T2 worked with data that never existed. |
| **Non-repeatable read** | T1 reads a row; T2 updates (or deletes) it and commits; T1 reads the same row again and gets a different value within one transaction. |
| **Phantom read** | T1 runs `SELECT ... WHERE status = 'NEW'`; T2 inserts or deletes rows matching that predicate and commits; T1 re-runs the query and gets a different **set of rows**. |
| **Lost update** | T1 and T2 both read `balance = 100`; T1 writes 90, T2 writes 80 and commits last ("last commit wins"); T1's change is gone. A variant: T2 rolls back after T1's write and the rollback erases T1's change too (only possible when dirty writes are allowed; no serious DBMS permits it). |
| **Write skew** | T1 and T2 each read an *invariant* (two doctors on call), each decides it is safe to update a *different* row (each goes off call), both commit; the invariant is broken, no row was written twice. |

- The row-level cure for lost update is a lock or a version check ([[jpa-locking]]) or an atomic `UPDATE ... SET balance = balance - 10`; only SERIALIZABLE catches write skew.

### The four isolation levels (SQL standard)
| Level | Dirty read | Non-repeatable read | Phantom | Write skew | How it is usually implemented |
|---|---|---|---|---|---|
| READ UNCOMMITTED | allowed | allowed | allowed | allowed | no read locks / no snapshot; Postgres does not implement it (treated as READ COMMITTED) |
| READ COMMITTED | no | allowed | allowed | allowed | each statement sees data committed before it started; the common default |
| REPEATABLE READ | no | no | allowed by the standard; **not** in Postgres | allowed | snapshot taken at the first statement of the transaction |
| SERIALIZABLE | no | no | no | no | the outcome equals some serial order: 2PL locking (MySQL, SQL Server) or serializable snapshot isolation with abort-and-retry (Postgres) |

- Higher level = fewer anomalies, more aborts or blocking, fewer transactions per second. Pick per use case: READ COMMITTED for ordinary CRUD, REPEATABLE READ for consistent reports, SERIALIZABLE for invariants across rows (money, inventory) with a retry loop.
- Set it per transaction (`SET TRANSACTION ISOLATION LEVEL ...`) or in Spring with `@Transactional(isolation = Isolation.SERIALIZABLE)`.
- Locks vs MVCC: lock-based engines make readers wait for writers; MVCC engines (Postgres, Oracle, InnoDB) keep old row versions so readers never block writers and vice versa, and only writer–writer conflicts block.

## Example
```sql
-- Lost update, step by step under READ COMMITTED (two sessions)
-- T1                                          -- T2
BEGIN;                                         BEGIN;
SELECT balance FROM accounts WHERE id = 1;     SELECT balance FROM accounts WHERE id = 1;  -- both see 100
UPDATE accounts SET balance = 90 WHERE id = 1;
COMMIT;                                        UPDATE accounts SET balance = 80 WHERE id = 1; -- waited for T1, then overwrote
                                               COMMIT;                                        -- balance = 80, T1's -10 is lost

-- The fix: make the update relative (atomic), or lock the row first
UPDATE accounts SET balance = balance - 10 WHERE id = 1 AND balance >= 10;
-- or
SELECT balance FROM accounts WHERE id = 1 FOR UPDATE;
```

## Pitfalls
- "We use transactions, so we are safe from concurrency bugs": READ COMMITTED allows lost updates and write skew; a transaction only gives atomicity, not serial correctness.
- Long transactions (an HTTP call inside, a user think-time) hold locks and, in MVCC engines, block vacuum; keep them to milliseconds.
- SERIALIZABLE without a retry loop just turns race conditions into `40001` errors for users.
- Postgres specifics and the write-skew example: [[postgres-isolation-levels]]; the Java-side analogues (check-then-act races): [[concurrency-hazards]].
