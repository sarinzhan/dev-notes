---
title: @Transactional: how it really works
tags: [transactions, jpa, aop]
learned: 2026-09-18
---
## TL;DR
`@Transactional` is an AOP proxy around the bean. The proxy opens a transaction before the method and commits or rolls back after. Anything that bypasses the proxy, bypasses the transaction.

## Key points
- Only **public** methods called **from another bean** are intercepted. A self-invocation (`this.save()`) is a plain Java call.
- Default rollback rule: unchecked exceptions roll back, checked ones **commit**. Use `rollbackFor = Exception.class` when needed.
- Propagation `REQUIRES_NEW` opens a second connection from the pool. Two nested ones can deadlock a small [[hikaricp-pool-sizing|pool]].
- Isolation defaults to the database's default, which for Postgres is `READ COMMITTED` (see [[postgres-isolation-levels]]).
- `@Transactional(readOnly = true)` lets Hibernate skip dirty checking and lets a routing datasource send reads to a replica.

## Example
```java
@Service
public class OrderService {
    @Transactional
    public Order place(PlaceOrder cmd) {
        var order = orders.save(Order.from(cmd));
        outbox.save(OutboxEvent.of("OrderPlaced", order)); // same TX, see outbox pattern
        return order;
    }
}
```

## Pitfalls
- Calling an external HTTP API inside a transaction holds the DB connection for the whole call. Publish via the [[outbox-pattern]] instead.
- `@Transactional` on a `private` method silently does nothing.
- Lazy loading after the method returns throws `LazyInitializationException`; map to a DTO inside the transaction.
