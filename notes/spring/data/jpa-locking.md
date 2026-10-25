---
title: Optimistic and pessimistic locking in JPA
tags: [jpa, hibernate, locking, concurrency]
learned: 2026-10-22
---
## TL;DR
Locks control concurrent access to the same rows so that two transactions do not silently overwrite each other (lost update). **Optimistic** locking takes no DB lock: a `@Version` column is compared on `UPDATE`, and a mismatch throws `OptimisticLockException`, which the caller retries. **Pessimistic** locking takes a row lock at read time (`SELECT ... FOR SHARE/UPDATE`), so other writers wait until the transaction ends. Optimistic for rare conflicts and user-facing edits; pessimistic for hot rows, counters and queue-like processing.

## Key points

### The problem both solve
- Transaction A reads `balance = 100`, transaction B reads `100`, A writes `90`, B writes `80`: A's update is lost. Under READ COMMITTED (the Postgres default) this is allowed; see [[transactions-acid-and-isolation]] and [[postgres-isolation-levels]].
- Locks are the JPA-level tools; the alternatives are `SERIALIZABLE` isolation (detects and aborts) or atomic SQL (`update ... set balance = balance - 10 where ...`).

### Optimistic locking
- Add a `@Version` field (`int`, `long`, `Integer`, `Long`, `short`, or `Timestamp`/`Instant`; a number is safer than a timestamp). Hibernate manages it: never set it by hand.
- Every `UPDATE` becomes `update orders set ..., version = 2 where id = ? and version = 1`. Zero affected rows → `jakarta.persistence.OptimisticLockException` (Hibernate: `StaleObjectStateException`, Spring wraps it as `ObjectOptimisticLockingFailureException`) at flush/commit.
- No DB locks are held, so there is no deadlock and no waiting; the loser retries or reports a conflict. Works across HTTP requests: send `version` to the client in the DTO and back in the update, and a stale form submission fails instead of overwriting.
- `LockModeType.OPTIMISTIC` (a.k.a. `READ`): also verify the version of an entity you only *read* at commit, to make sure nobody changed it. `OPTIMISTIC_FORCE_INCREMENT` (`WRITE`): bump the version even without changes, e.g. to version a parent when its children change.
- Without `@Version` Hibernate can still do it with `@OptimisticLocking(type = ALL|DIRTY)` and `@DynamicUpdate`, comparing all (or changed) columns in the `WHERE`; rarely worth it.

### Pessimistic locking
- Assumes conflicts are likely; the row is locked when read and stays locked until the transaction commits or rolls back, so it must be used inside a transaction (see [[spring-transactional]]).
| `LockModeType` | SQL on Postgres | Effect |
|---|---|---|
| `PESSIMISTIC_READ` | `SELECT ... FOR SHARE` | others may read and take shared locks; writers wait |
| `PESSIMISTIC_WRITE` | `SELECT ... FOR UPDATE` | others may still *read* (MVCC: plain selects never block), but `FOR SHARE`/`FOR UPDATE`/`UPDATE`/`DELETE` wait |
| `PESSIMISTIC_FORCE_INCREMENT` | `FOR UPDATE` + version bump | same lock, and the `@Version` is incremented |
- The source's "forbids reading" wording is MySQL/SQL Server-style thinking; on Postgres a `FOR UPDATE` lock blocks other *locking* reads and writes, not ordinary `SELECT`s.
- Where to apply: `em.find(Order.class, id, LockModeType.PESSIMISTIC_WRITE)`, `em.lock(entity, mode)`, `query.setLockMode(...)`, or `@Lock(LockModeType.PESSIMISTIC_WRITE)` on a repository method.
- Timeout: `@QueryHints(@QueryHint(name = "jakarta.persistence.lock.timeout", value = "3000"))`; `0` means `NOWAIT`, `-2` means `SKIP LOCKED` on Hibernate. Postgres applies `NOWAIT`/`SKIP LOCKED` only, it has no per-statement lock timeout other than `SET lock_timeout`.
- `FOR UPDATE SKIP LOCKED` is the standard way to let several workers pull jobs from one table (the [[outbox-pattern]] relay, schedulers) without contention.

### Choosing
| | Optimistic | Pessimistic |
|---|---|---|
| Conflict rate | low | high |
| Cost on no-conflict path | one extra `where version = ?` | row lock held, connection busy |
| Across requests (edit form) | yes | no |
| Deadlock risk | none | yes, order locks consistently |
| Failure | exception at commit, retry | waiting, timeout, deadlock error |

## Example
```java
public interface AccountRepository extends JpaRepository<Account, Long> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @QueryHints(@QueryHint(name = "jakarta.persistence.lock.timeout", value = "2000"))
    @Query("select a from Account a where a.id = :id")
    Optional<Account> findForUpdate(@Param("id") Long id);
}

@Service
@RequiredArgsConstructor
public class TransferService {
    private final AccountRepository accounts;

    @Transactional
    public void transfer(Long fromId, Long toId, BigDecimal amount) {
        // lock in a fixed order to avoid deadlocks between opposite transfers
        Long first = Math.min(fromId, toId), second = Math.max(fromId, toId);
        Account a = accounts.findForUpdate(first).orElseThrow();
        Account b = accounts.findForUpdate(second).orElseThrow();
        Account from = a.getId().equals(fromId) ? a : b;
        Account to   = from == a ? b : a;
        from.withdraw(amount);   // throws if insufficient
        to.deposit(amount);
    }                            // locks released at commit

    @Retryable(retryFor = ObjectOptimisticLockingFailureException.class, maxAttempts = 3)
    @Transactional
    public void rename(Long id, String name) {   // Account has @Version
        accounts.findById(id).orElseThrow().setName(name);
    }
}
```

## Pitfalls
- `@Retryable` on the same method as `@Transactional` is fine only if the retry proxy is **outside** the transaction proxy (Spring Retry's advice has higher precedence by default); otherwise the retry reuses the dead transaction. Safer: retry from the caller.
- Optimistic lock exceptions surface at **commit**, after the method returned normally; catching inside the method does not work.
- Pessimistic locks plus an external HTTP call inside the transaction = rows locked for seconds; keep locked sections short.
- Locking a parent does not lock its children; `FOR UPDATE` on a `JOIN FETCH` query locks all joined rows, which is usually more than intended.
- Mapping `@Version` with `@Column(updatable = false)` or copying it from a DTO by `BeanUtils` silently disables the check.
- Java-level counterparts (compare-and-set, monitors): [[concurrency-hazards]], [[locks-and-lock-free]]. Making retried writes safe: [[idempotency]].
