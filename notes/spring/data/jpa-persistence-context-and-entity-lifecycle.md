---
title: Persistence context, entity lifecycle and flushing
tags: [jpa, hibernate, persistence-context, transactions]
learned: 2026-10-22
---
## TL;DR
The persistence context (Hibernate `Session`, JPA `EntityManager`) is an in-memory map of managed entities keyed by id for the duration of a transaction. An entity is **transient** (new object), **managed/persistent** (tracked, dirty-checked, written on flush), **detached** (has an id but is no longer tracked) or **removed** (scheduled for `DELETE`). SQL is not sent when you call `persist()`; it is sent on **flush**, which happens before commit, before a JPQL query that touches the same tables, or on `flush()`.

## Key points

### The persistence context
- One per transaction in Spring (transaction-scoped `EntityManager`); the `@PersistenceContext`-injected `EntityManager` is a thread-safe proxy that resolves to the current transaction's context.
- Guarantees **identity**: within one context, `findById(1)` twice returns the same Java object and the second call hits no DB; this is the first-level cache (see [[hibernate-caching]]).
- Tracks a snapshot of every managed entity; at flush it compares (**dirty checking**) and emits `UPDATE` only for changed entities. You never call `update()`: change a managed entity inside a transaction and it is saved.
- Entities loaded by a JPQL/native query that maps to an entity become managed; DTO projections do not.
- `DataSource` is the JDBC side (connection settings, pooled by HikariCP, [[hikaricp-pool-sizing]]); `EntityManager` is the JPA side built on top of a connection borrowed from it for the transaction.

### Entity states
| State | Has id | Tracked | How you get there |
|---|---|---|---|
| Transient (new) | no | no | `new Order()` |
| Managed (persistent) | yes | yes | `persist()`, `find()`, `getReference()`, any entity query, `merge()` returns one |
| Detached | yes | no | context closed/cleared, `detach(e)`, entity returned from a method after the transaction ended, deserialized from a cache |
| Removed | yes | until flush | `remove(e)`; row deleted at flush |

- `persist(e)` makes a transient entity managed; with `SEQUENCE` id generation it only takes a number, the `INSERT` waits for flush. With `IDENTITY` the `INSERT` runs immediately.
- `merge(detached)` loads (or uses the cached) managed copy, copies the state onto it and **returns the managed instance**; the argument stays detached. Spring Data `save()` is `persist` or `merge` depending on whether the entity is new (see [[spring-data-repositories]]).
- `detach(e)` / `clear()` stop tracking; unflushed changes are lost. `refresh(e)` overwrites in-memory state from the DB.
- Lifecycle callbacks: `@PrePersist`, `@PostPersist`, `@PreUpdate`, `@PostUpdate`, `@PreRemove`, `@PostRemove`, `@PostLoad`, or an `@EntityListeners(AuditingEntityListener.class)` for `@CreatedDate`/`@LastModifiedDate`.

### Flush
- Flush = push pending `INSERT`/`UPDATE`/`DELETE` to the JDBC connection; it is not a commit. Order is fixed: inserts, updates, collection changes, deletes.
- `FlushModeType.AUTO` (default): flush before commit and before any JPQL/Criteria query whose tables overlap pending changes, so your own query sees your own writes. `COMMIT`: only at commit; faster for read-heavy batches but queries may miss unflushed changes. Hibernate adds `ALWAYS` and `MANUAL`.
- Native queries do not reliably trigger the auto-flush, because Hibernate cannot parse which tables they touch; call `em.flush()` first or declare the affected entity with `addSynchronizedEntityClass`.
- Hibernate batches statements when `spring.jpa.properties.hibernate.jdbc.batch_size=50` and `order_inserts=true`, `order_updates=true`; in long loops call `flush()` + `clear()` every batch so the context does not hold thousands of snapshots.

### Transactions and JPA
- The transaction itself (propagation, rollback rules, proxies) is in [[spring-transactional]]; here only the JPA interplay.
- `@Transactional(readOnly = true)`: Spring sets the JDBC connection read-only and Hibernate switches the session to `FlushMode.MANUAL` and skips snapshots, so loading is cheaper and no accidental `UPDATE` escapes. Changes made inside are silently not flushed.
- Rollback marks the context invalid; after `RollbackException` the managed entities are detached and must be re-read.
- **Open Session In View** (`spring.jpa.open-in-view`, default `true` in Boot, with a startup warning): a web request keeps the `EntityManager` open through the controller and view rendering, so lazy collections work in controllers and in Jackson serialization. The cost: a DB connection is held for the whole request (including external HTTP calls), and the N+1 is hidden instead of surfaced. Set it to `false` and load what you need inside the service with `join fetch`/`@EntityGraph` (see [[jpa-n-plus-one]]).
- Logical vs physical transaction: one `@Transactional` service method (logical) usually maps to one DB transaction (physical), but `REQUIRES_NEW` opens a second physical one; see [[transactions-acid-and-isolation]].

## Example
```java
@Service
@RequiredArgsConstructor
public class OrderService {
    private final OrderRepository orders;
    private final EntityManager em;

    @Transactional
    public void importOrders(List<ImportRow> rows) {
        for (int i = 0; i < rows.size(); i++) {
            orders.save(Order.from(rows.get(i)));   // transient -> managed, INSERT deferred
            if (i % 50 == 0) {                        // matches hibernate.jdbc.batch_size
                em.flush();                           // send the batch
                em.clear();                           // drop snapshots, free memory
            }
        }
    }

    @Transactional
    public void rename(Long id, String name) {
        Order order = orders.findById(id).orElseThrow(); // managed
        order.setName(name);                             // no save() needed: dirty checking
    }                                                    // flush + commit on exit
}
```

## Pitfalls
- `LazyInitializationException`: touching a lazy field on a detached entity (after the transaction, in a controller with `open-in-view=false`, in an async thread). Fetch what you need inside the transaction or map to a DTO.
- Calling `repository.save(entity)` on an already managed entity is harmless but useless; calling it on a detached entity and then continuing to mutate the *argument* loses changes, because `merge` returned a different object.
- A `@Modifying` bulk update does not update managed entities; clear the context or re-read.
- Long `importOrders` loops without `flush()/clear()` grow the context and slow dirty checking to O(n) per flush.
- Caching layers above the context: [[hibernate-caching]], [[spring-cache-abstraction]]. Pessimistic and optimistic locks are set on managed entities only: [[jpa-locking]].
