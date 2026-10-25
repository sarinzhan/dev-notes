---
title: The N+1 select problem and how to fix it
tags: [jpa, hibernate, performance, n-plus-one]
learned: 2026-10-22
---
## TL;DR
N+1 happens when one query loads N parent rows and then each lazy association is loaded by its own query while you iterate: 1 + N statements instead of 1 or 2. Fix it per use case with `JOIN FETCH` or `@EntityGraph`, or globally soften it with batch fetching (`hibernate.default_batch_fetch_size`), which turns N queries into N / batch size `IN (...)` queries. Detect it in tests by counting statements, not by looking at the code.

## Key points

### How it appears
- `List<Order> orders = repo.findAll()` → 1 `SELECT ... FROM orders`.
- `for (o : orders) o.getLines().size()` → N `SELECT ... FROM order_lines WHERE order_id = ?`, one per order, because `@OneToMany` is LAZY and each collection is a separate proxy.
- The EAGER variant is as bad: `@ManyToOne(fetch = EAGER)` on `OrderLine.product` makes Hibernate issue a `SELECT` per distinct product after a JPQL query (JPQL does not honour EAGER by join, it loads eagerly with secondary selects).
- With `spring.jpa.open-in-view=true` the extra queries fire during JSON serialization, so the service looks clean and the request is still slow (see [[jpa-persistence-context-and-entity-lifecycle]]).

### Fix 1: `JOIN FETCH` (per query)
- `select o from Order o join fetch o.lines where o.customer.id = :id` → one SQL join; the collection is initialized from the same result set.
- Fetch-joining a collection multiplies parent rows; Hibernate 6 de-duplicates parents automatically (no `distinct` needed; it was needed in Hibernate 5).
- Cannot combine with `Pageable`: Hibernate paginates in memory with the warning `HHH90003004 firstResult/maxResults specified with collection fetch; applying in memory`. Paginate parents first, then fetch children for those ids.
- Fetch-joining two collections at once gives a cartesian product and `MultipleBagFetchException` for `List` bags; use `Set` or two queries.

### Fix 2: `@EntityGraph` (per repository method)
- `@EntityGraph(attributePaths = {"lines", "lines.product"})` on a repository method, or a named graph on the entity; the same effect as `JOIN FETCH` (uses `LEFT JOIN`) without rewriting the query, works with derived methods like `findByCustomerId`.
- `type = EntityGraph.EntityGraphType.FETCH` (default) makes everything not listed LAZY; `LOAD` keeps the entity's own defaults for the rest.

### Fix 3: batch fetching (global safety net)
- `spring.jpa.properties.hibernate.default_batch_fetch_size=50` globally, or `@BatchSize(size = 50)` on a collection/entity.
- How it works: parents are loaded; when you touch the first lazy collection, Hibernate looks in the persistence context for other parents of the same type with uninitialized collections and loads up to `size` of them with `select * from order_lines where order_id in (?, ?, ...)`. N queries become `ceil(N / size)`.
- Hibernate 6 uses array parameters on Postgres (`where order_id = any(?)`) for a single prepared statement shape; in Hibernate 5 it padded `IN` lists to powers of two.
- It is a mitigation, not a plan: still extra round trips, and it does nothing for detached entities.

### Fix 4: do not load entities at all
- A DTO projection (`select new ...` or an interface projection) with explicit joins returns exactly the columns you need and has no lazy proxies; best for read endpoints.
- `@Fetch(FetchMode.SUBSELECT)` loads all collections of all parents from the original query with one subselect; useful when you always iterate the whole list.

### Detecting it
- `spring.jpa.show-sql` prints to stdout and bypasses the logger; use `logging.level.org.hibernate.SQL=DEBUG` (and `org.hibernate.orm.jdbc.bind=TRACE` for parameters) so the statements go through Logback and into Loki with the rest of the logs, see [[logging-stacks]]. For tests use a statement counter: Hibernate `Statistics` (`hibernate.generate_statistics=true`, `getPrepareStatementCount()`), `datasource-proxy` + `QueryCountHolder`, or the `digma`/`hypersistence-utils` `SQLStatementCountValidator.assertSelectCount(1)`.
- Hibernate 6.2+: `hibernate.query.fail_on_pagination_over_collection_fetch=true` turns the in-memory pagination warning into an exception.

## Example
```java
public interface OrderRepository extends JpaRepository<Order, Long> {

    // Fix 1: explicit fetch join, one SQL statement
    @Query("select o from Order o join fetch o.lines l join fetch l.product where o.customer.id = :customerId")
    List<Order> findWithLinesByCustomer(@Param("customerId") Long customerId);

    // Fix 2: derived query + graph, same SQL shape
    @EntityGraph(attributePaths = {"lines", "lines.product"})
    List<Order> findByStatus(OrderStatus status);

    // Paging: parents first, children by ids (2 queries, no in-memory paging)
    Page<Order> findByCustomerId(Long customerId, Pageable page);

    @Query("select o from Order o join fetch o.lines where o in :orders")
    List<Order> fetchLines(@Param("orders") Collection<Order> orders);
}
```
```yaml
spring:
  jpa:
    open-in-view: false
    properties:
      hibernate:
        default_batch_fetch_size: 50
```

## Pitfalls
- `@OneToOne` on the inverse (`mappedBy`) side cannot be lazy without bytecode enhancement: Hibernate must query to know whether to return `null` or a proxy, so every parent load adds a select. Map the FK on the side you read from.
- `@ManyToOne` defaults to EAGER. Every mapping should say `fetch = FetchType.LAZY` explicitly.
- Fixing N+1 with `EAGER` moves the problem to every other query that loads the entity.
- `findAll()` + `stream().map(toDto)` in a `@Transactional(readOnly = true)` method still runs N+1; the transaction only hides the exception, not the queries.
- Mapping rules that cause it: [[jpa-entities-and-relationships]]; second-level cache can mask it for hot rows but not for collections by default: [[hibernate-caching]]. The same loop-of-queries anti-pattern on the SQL side is the reason to prefer joins: [[sql-joins-and-commands]].
