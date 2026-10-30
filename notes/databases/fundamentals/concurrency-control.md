---
title: Database concurrency control
tags: [databases, locking, concurrency, transactions, deadlocks]
learned: 2026-10-30
---
## TL;DR
When transactions touch the same data at the same time, the database must keep the result correct. It uses **locks**: a **shared** lock lets many readers in, an **exclusive** lock lets one writer in alone. Locks held until commit (**two-phase locking**) give serializable schedules but can form **deadlocks**, which the database detects and breaks by killing one transaction. The application chooses between **pessimistic** control (lock first: `SELECT ... FOR UPDATE`) and **optimistic** control (no lock, check a version at write time and retry). The classic test case is the **double booking** problem: two users book the same seat.

## Key points
### Shared vs exclusive locks
- **Shared (S, read) lock**: many transactions can hold it on the same object; nobody can write while it is held.
- **Exclusive (X, write) lock**: only one holder; blocks both other writers and other lockers. You cannot get X while anyone holds S, and vice versa.
- Used for consistency: e.g., a bank reconciliation reads all accounts under S locks so no transfer changes them mid-report; a balance update takes X on the row.
- MVCC databases (Postgres, InnoDB, Oracle) mostly avoid read locks: plain `SELECT` reads a snapshot and never blocks or is blocked by writers. Locks are taken by writes and by explicit `FOR SHARE` / `FOR UPDATE`. Lock modes in detail: [[postgres-locks]].

### Deadlocks
- T1 locks row A and waits for B; T2 locks B and waits for A. Neither can proceed.
- Databases detect the cycle in the wait-for graph (Postgres checks after `deadlock_timeout`, 1 s; InnoDB immediately) and abort one victim with an error (`40P01` in Postgres, `1213` in MySQL). The application must **retry** the whole transaction.
- Common causes: updating the same rows in different orders, foreign key checks taking shared locks on the parent, inserts into a unique index with the same key.
- Prevention: lock in a consistent order (e.g., sort ids), keep transactions short, lock everything you need up front. Theory: [[deadlocks-and-synchronization-primitives]].

### Two-phase locking (2PL)
- **Growing phase**: a transaction acquires locks, never releases. **Shrinking phase**: once it releases any lock, it acquires no more.
- **Strict 2PL** holds all write locks until commit/abort (what real databases do): no one sees uncommitted data, and the schedule is serializable.
- Cost: locks are held long, so concurrency drops and deadlocks become possible. SQL Server and MySQL `SERIALIZABLE` are lock-based; Postgres `SERIALIZABLE` uses SSI instead (optimistic, see [[postgres-isolation-levels]]).

### The double booking problem
- Two users see seat 13 as free and both run `UPDATE seats SET booked_by = ? WHERE id = 13`. Under READ COMMITTED the second update simply overwrites the first: lost update. Both get "success".
- **Fix 1, pessimistic row lock**: `SELECT ... WHERE id = 13 AND booked_by IS NULL FOR UPDATE` inside the transaction. The second transaction blocks until the first commits, then re-reads the row (Postgres re-checks the WHERE on the new version) and finds it booked.
- **Fix 2, atomic conditional update**: `UPDATE seats SET booked_by = ? WHERE id = 13 AND booked_by IS NULL` and check the affected row count (0 = someone else got it). One statement, no explicit lock; the row lock taken by UPDATE serializes the two.
- **Fix 3, constraint**: a unique index on `(event_id, seat_no)` in a `bookings` table; the second INSERT fails with a unique violation. Works even without a pre-existing row to lock.
- **Fix 4, SERIALIZABLE**: let the database detect the conflict and retry on `40001`.
- What does **not** work: checking in the application ("SELECT, if free then UPDATE") without a lock.

```anim
double-booking-locks
```

### Optimistic vs pessimistic
| | Pessimistic | Optimistic |
|---|---|---|
| How | lock before reading (`FOR UPDATE`) | read freely, write with `WHERE version = ?`; 0 rows = conflict |
| Conflicts | wait | detect and retry |
| Good when | conflicts are frequent, retry is expensive, short transactions | conflicts are rare, reads dominate, long "think time" (user edits a form) |
| Risks | blocking, deadlocks, lock escalation (SQL Server turns many row locks into a table lock) | retry storms under contention |
| In JPA | `@Lock(PESSIMISTIC_WRITE)` | `@Version` ([[jpa-locking]]) |

- Lock memory: InnoDB stores row locks in a bitmap per page; SQL Server escalates thousands of row locks to a table lock; Postgres stores row locks in the tuple header (no memory limit), but `FOR SHARE` by many transactions uses MultiXact.

### InnoDB specifics
- **Record locks** lock index records, not rows: an UPDATE without a usable index locks every record it scans.
- **Gap locks** and **next-key locks** (record + the gap before it) at REPEATABLE READ prevent phantoms: `SELECT ... WHERE age BETWEEN 20 AND 30 FOR UPDATE` blocks inserts of `age = 25`. They also cause surprising deadlocks between inserts.
- **Insert intention locks** let concurrent inserts into the same gap proceed if they target different positions.
- Lower to READ COMMITTED to disable most gap locking if you do not need phantom protection.

## Example
```sql
-- pessimistic: lock the seat, then book it
BEGIN;
SELECT id FROM seats WHERE id = 13 AND booked_by IS NULL FOR UPDATE;  -- blocks a concurrent booker
-- 0 rows → already booked: ROLLBACK and report
UPDATE seats SET booked_by = 'alice' WHERE id = 13;
COMMIT;

-- optimistic / atomic: one statement, check the row count
UPDATE seats SET booked_by = 'bob' WHERE id = 13 AND booked_by IS NULL;   -- UPDATE 0 → lost the race

-- consistent lock order to avoid deadlocks in a transfer
SELECT id FROM accounts WHERE id IN (7, 3) ORDER BY id FOR UPDATE;
```

## Pitfalls
- Read-then-write in application code without a lock or a condition in the UPDATE: lost updates.
- Locking rows in different orders in different code paths: deadlocks under load. Always retry on `40P01`/`40001`.
- Holding a row lock while calling an external service: everyone else waits for the network.
- InnoDB UPDATE/DELETE on an unindexed column locks far more than the matching rows.
- Related: [[transactions-acid-and-isolation]], [[postgres-locks]], [[race-conditions-and-critical-sections]], [[distributed-locks-and-fencing]].
