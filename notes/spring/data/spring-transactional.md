---
title: @Transactional: how it really works
tags: [transactions, jpa, aop, spring-boot]
learned: 2026-09-18
---
## TL;DR
`@Transactional` is an AOP proxy around the bean ([[spring-aop]]). The proxy opens a transaction before the method, binds the connection to the thread, and commits or rolls back after. Anything that bypasses the proxy (self-invocation, private methods, `new`) bypasses the transaction. Keep transactions short and free of network calls.

## Key points

### Proxy mechanism
- Spring creates a CGLIB subclass (or JDK proxy for interfaces) and injects the proxy, not your object. `TransactionInterceptor` wraps the call: `getTransaction()` → method → `commit()`/`rollback()`.
- The connection and the JPA `EntityManager` are stored in `TransactionSynchronizationManager` thread-locals. Spawning a thread (or a `CompletableFuture`) inside the method runs outside the transaction ([[completable-future]]).
- **Self-invocation trap**: `this.save()` calls the raw object, not the proxy, so the annotation on `save` is ignored. Fix: move the method to another bean, inject self via `@Lazy`, or use `TransactionTemplate`.
- `private` methods cannot be proxied by CGLIB; `final` methods and classes cannot be overridden: the annotation is silently ignored. Spring Boot 3 fails fast on `final` classes only when AOP needs a subclass. Keep transactional methods `public` (protected/package-private work with CGLIB since Spring 6 but are not portable).

### Propagation
| Type | Existing TX | No TX |
|---|---|---|
| `REQUIRED` (default) | join it | create new |
| `REQUIRES_NEW` | suspend it, open a **second connection** | create new |
| `NESTED` | savepoint inside it (JDBC only, not JPA by default) | create new |
| `SUPPORTS` | join it | run without TX |
| `NOT_SUPPORTED` | suspend it, run without TX | run without TX |
| `MANDATORY` | join it | throw `IllegalTransactionStateException` |
| `NEVER` | throw | run without TX |
- `REQUIRES_NEW` for audit logs that must survive a rollback. Two nested ones can deadlock a small [[hikaricp-pool-sizing|pool]]: the outer holds one connection while waiting for the inner.
- In `REQUIRED`, an inner method that throws and is caught by the outer still marks the transaction **rollback-only**; the outer commit fails with `UnexpectedRollbackException`.

### Isolation, rollback rules, attributes
- Isolation defaults to the database's default: `READ COMMITTED` for Postgres ([[postgres-isolation-levels]], [[transactions-acid-and-isolation]]). Set it per method only when you understand the cost; `SERIALIZABLE` means retry on `40001`.
- Default rollback rule: `RuntimeException` and `Error` roll back, **checked exceptions commit**. Use `rollbackFor = Exception.class` or make domain exceptions unchecked ([[exceptions]]).
- `readOnly = true`: Hibernate sets `FlushMode.MANUAL` and skips dirty checking; the JDBC driver gets `setReadOnly(true)`; a routing datasource can send the call to a replica. It is a hint, not a guard: writes may still happen on some drivers.
- `timeout = 5` (seconds) makes the next statement after the deadline fail; it does not kill a running query.

### Programmatic alternatives
- `TransactionTemplate.execute(status -> ...)` gives explicit boundaries, works with lambdas and in the self-invocation case, and makes the scope visible in code.
- `TransactionSynchronizationManager.registerSynchronization(...)` or `@TransactionalEventListener(phase = AFTER_COMMIT)` runs code after a successful commit: cache eviction, metrics, sending a notification. Note that the after-commit callback runs on the same thread, still inside the interceptor, and any DB write there needs `REQUIRES_NEW`.

### Side effects inside a transaction
- Kafka send or HTTP call inside `@Transactional`: the connection is held for the network latency, the DB can still roll back after the message is sent (phantom event), and a retry re-sends. Use the [[outbox-pattern]] or an after-commit listener for best-effort notifications.
- Open Session In View (`spring.jpa.open-in-view`, default `true` in Boot): the `EntityManager` stays open until the response is written, so lazy loading works in controllers but connections are held during serialization and template rendering. Set it to `false` and map to DTOs inside the service ([[jpa-persistence-context-and-entity-lifecycle]], [[jpa-n-plus-one]]).

## Example
```java
@Service
@RequiredArgsConstructor
public class OrderService {
    private final OrderRepository orders;
    private final OutboxRepository outbox;
    private final TransactionTemplate tx;
    private final ApplicationEventPublisher events;

    @Transactional(rollbackFor = Exception.class, timeout = 5)
    public Order place(PlaceOrder cmd) throws QuotaExceededException {
        var order = orders.save(Order.from(cmd));
        outbox.save(OutboxEvent.of("OrderPlaced", order));  // same TX, see outbox pattern
        events.publishEvent(new OrderPlaced(order.id()));   // delivered after commit (listener below)
        return order;
    }

    public void importBatch(List<PlaceOrder> cmds) {
        for (var cmd : cmds) {
            tx.executeWithoutResult(status -> orders.save(Order.from(cmd))); // one TX per row, no self-invocation issue
        }
    }

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onPlaced(OrderPlaced e) { cache.evict(e.orderId()); }
}
```

## Pitfalls
- Calling an external HTTP API inside a transaction holds the DB connection for the whole call; under load the pool empties and unrelated requests time out.
- `@Transactional` on a `private` method or on a method called via `this` silently does nothing.
- Catching an exception from a `REQUIRED` inner call and continuing still ends in `UnexpectedRollbackException` at commit.
- `@Transactional` on an interface (JDK proxies only) or on a `@Repository` default method behaves differently from a class-level annotation; put it on concrete service classes.
- Lazy loading after the method returns throws `LazyInitializationException`; map to a DTO inside the transaction.
- Related: [[jpa-locking]] for `@Lock` inside transactions, [[spring-kafka-consumer]] for why a transactional listener is still at-least-once.
