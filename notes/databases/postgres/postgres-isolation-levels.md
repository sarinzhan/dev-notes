---
title: Transaction isolation levels in Postgres
tags: [postgres, transactions, concurrency, mvcc]
learned: 2026-09-12
---
## TL;DR
Postgres uses MVCC, so readers never block writers. The default level is **READ COMMITTED**: each statement sees a fresh snapshot. **REPEATABLE READ** takes one snapshot for the whole transaction (snapshot isolation), which in Postgres also prevents phantoms, and aborts a transaction that tries to update a row someone else changed since the snapshot. **SERIALIZABLE** adds serializable snapshot isolation (SSI): it tracks read/write dependencies and aborts with `40001` when the result could not have come from any serial order, which catches write skew. Every abort must be retried by the application.

## Key points
| Level | Snapshot | Non-repeatable read | Phantom | Lost update | Write skew | Typical use |
|---|---|---|---|---|---|---|
| READ COMMITTED | per statement | possible | possible | possible (read-then-write) | possible | default OLTP |
| REPEATABLE READ | per transaction | no | no (in PG) | detected → `40001` | possible | reports, consistent reads |
| SERIALIZABLE | per transaction + SSI | no | no | detected | detected → `40001` | money, inventory, invariants |

- Postgres has no READ UNCOMMITTED; it is mapped to READ COMMITTED. Dirty reads are impossible at every level.

### READ COMMITTED
- Every statement gets a new snapshot, so two identical SELECTs in one transaction may return different rows.
- An UPDATE that finds a row being changed by another transaction **waits**, then re-reads the latest committed version and re-checks its WHERE (EvalPlanQual). This is why `UPDATE ... SET x = x - 1` is safe here, but read-in-the-app-then-write is not ([[concurrency-control]]).

### REPEATABLE READ (snapshot isolation)
- The snapshot is taken at the first statement, not at BEGIN. All reads see the database as of that moment, including new rows: inserts committed later are invisible, so **phantoms do not appear** (the SQL standard allows them at this level; Postgres is stricter).
- If the transaction updates or locks a row that was changed and committed after its snapshot, Postgres aborts it: `could not serialize access due to concurrent update` (40001). That prevents lost updates.
- Write skew is still possible: two transactions read the same set, each updates a **different** row, no row conflict, both commit.
- MySQL InnoDB REPEATABLE READ differs: plain reads use a snapshot, but `UPDATE` and locking reads see the latest data and use gap/next-key locks to block phantoms.

### Phantom reads, visualized
- T1: `SELECT count(*) FROM orders WHERE day = today` → 10. T2 inserts an order for today and commits. T1 runs the same query again.
- READ COMMITTED: 11 (phantom). REPEATABLE READ: 10 (same snapshot). SERIALIZABLE: 10, and if T1 then writes something that depends on the count, SSI may abort one side.

```anim
phantom-rr-vs-serializable
```

### SERIALIZABLE vs REPEATABLE READ
- Both read the same snapshot; SERIALIZABLE additionally records **predicate locks** (SIRead locks) on what each transaction read. They never block; they only feed a dependency graph.
- When two concurrent transactions form a dangerous structure (T1 read something T2 wrote, and T2 read something T1 wrote, the "rw-antidependency" cycle), one is aborted with `40001`.
- Classic write skew: doctors on call. Both read `count(*) WHERE on_call` = 2, each sets their own row `on_call = false`. REPEATABLE READ: both commit, nobody on call. SERIALIZABLE: the second commit fails.
- Alternatives to SERIALIZABLE for write skew: lock the rows that form the invariant (`SELECT ... FOR UPDATE` on all doctors of the shift), materialize the conflict into one row (a `shift` row both must update), or a constraint (`EXCLUDE`, unique).
- Cost: more aborts under contention, predicate lock memory, false positives (aborts that were not strictly necessary). Declaring transactions `READ ONLY DEFERRABLE` lets long reports run serializable without risk of abort.

### Snapshots and long transactions
- A snapshot keeps all row versions it might need alive: VACUUM cannot remove tuples newer than the oldest running snapshot ([[postgres-vacuum-and-bloat]]).
- Do you need a transaction for read-only work? A single SELECT is already atomic and consistent. Several SELECTs that must agree (a report) need REPEATABLE READ; otherwise skip the explicit transaction.

## Example
```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;
SELECT count(*) FROM doctors WHERE shift_id = 7 AND on_call;     -- 2
UPDATE doctors SET on_call = false WHERE id = 101;
COMMIT; -- may fail with SQLSTATE 40001: retry the whole transaction
```

```java
// Spring: retry the whole transactional method on serialization failures
@Retryable(retryFor = PessimisticLockingFailureException.class, maxAttempts = 5,
           backoff = @Backoff(delay = 20, multiplier = 2, random = true))
@Transactional(isolation = Isolation.SERIALIZABLE)
public void goOffCall(long doctorId) { ... }
```

## Pitfalls
- Write skew under REPEATABLE READ: it looks safe because the snapshot is stable, but it is not serializable.
- SERIALIZABLE without retries, or retrying only the failed statement instead of the whole transaction.
- Mixing levels: SSI only protects transactions that all run at SERIALIZABLE.
- Long transactions hold back vacuum. Watch `pg_stat_activity.xact_start`.
- Spring maps `Isolation.SERIALIZABLE` in [[spring-transactional|@Transactional]]; Spring translates SQLSTATE 40001 and 40P01 into subclasses of `PessimisticLockingFailureException`, so retry on that. Related: [[transactions-acid-and-isolation]], [[concurrency-control]], [[idempotency]].
