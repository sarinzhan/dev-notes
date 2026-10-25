---
title: JPA entities and relationship mappings
tags: [jpa, hibernate, orm, spring-data]
learned: 2026-10-22
---
## TL;DR
JPA (Jakarta Persistence) is a specification of interfaces for ORM: mapping Java objects to relational tables and back. Hibernate is the implementation Spring Boot uses. An entity is a class annotated `@Entity` that represents a table (or view); relationships between entities are declared with `@OneToOne`, `@OneToMany`, `@ManyToOne`, `@ManyToMany`, and the physical foreign key lives on the *owning* side, described by `@JoinColumn` or `@JoinTable`. Defaults that matter most: `@ManyToOne`/`@OneToOne` are EAGER, collections are LAZY.

## Key points

### JPA, ORM, Hibernate
- **ORM** maps objects to tables so that the application works with a "virtual object database". JPA describes *which* interfaces you get (`EntityManager`, `Query`, `Criteria`), Hibernate is the provider that does the SQL.
- Since Jakarta EE 9 the package is `jakarta.persistence`, not `javax.persistence`. Spring Boot 3.x uses Jakarta Persistence 3.1 and Hibernate 6.x; a `javax.persistence` import on Boot 3 is a compile error.
- **`DataSource`** describes how to obtain a JDBC `Connection` (pooled by HikariCP, see [[hikaricp-pool-sizing]]). **`EntityManager`** is the main JPA interface: it manages entity state inside the persistence context and creates queries. Spring Data repositories wrap it (see [[spring-data-repositories]]).
- Schema initialization: `spring.jpa.hibernate.ddl-auto=create|update|validate|none` lets Hibernate generate tables from entities. Use `validate` in production and manage the schema with Flyway/Liquibase; `update` never drops or narrows columns and hides mapping errors.

### What makes a class an entity
| Annotation | Meaning |
|---|---|
| `@Entity` | class is managed by JPA; needs a no-arg constructor (at least `protected`), must not be `final` |
| `@Table(name, schema, uniqueConstraints, indexes)` | which table backs it; defaults to the class name |
| `@Id` | the identifier field; required |
| `@GeneratedValue(strategy)` | `IDENTITY` (DB auto-increment, disables JDBC batching), `SEQUENCE` (preferred for Postgres, batch-friendly, `allocationSize`), `TABLE` (slow, avoid), `AUTO`, `UUID` (JPA 3.1) |
| `@Column(name, nullable, length, unique, columnDefinition)` | column mapping; defaults to the field name |
| `@Version` | optimistic lock counter, see [[jpa-locking]] |
| `@Enumerated(EnumType.STRING)` | always STRING; `ORDINAL` breaks on reordering |

- **`@Entity` vs `@Component`**: `@Entity` is a JPA annotation. It marks a data-holding class whose instances are created per row and managed by the persistence context; Hibernate instantiates them, Spring does not. `@Component` is a Spring stereotype: one singleton bean created and wired by the IoC container (see [[spring-ioc-and-di]]), holding behaviour, not rows. An entity is never a bean and must not have dependencies injected.

### Relationships
- `@OneToOne` attributes: `mappedBy` (names the field on the owning side, used on the inverse side), `fetch` (default **EAGER**), `optional` (default `true`; `false` lets Hibernate use an inner join and skip the "is it null?" extra select), `cascade`, `orphanRemoval` (default `false`).
- `@OneToMany`: `cascade`, `fetch` (default **LAZY**), `mappedBy`, `orphanRemoval`, `targetEntity`. A unidirectional `@OneToMany` without `@JoinColumn` creates a join table; prefer bidirectional with `@ManyToOne` as the owner.
- `@ManyToOne`: the owning side in practice; default fetch is **EAGER** and should almost always be set to `LAZY`.
- `@ManyToMany`: `cascade`, `fetch` (default **LAZY**), `mappedBy`, `targetEntity`. Never `CascadeType.REMOVE` on it: deleting one side would delete shared rows on the other.
- `@JoinColumn` names the FK column; used with `@OneToOne`, `@ManyToOne` and unidirectional `@OneToMany`. `@JoinTable` names the link table and its two FK columns for `@ManyToMany` (see [[constraints-and-relationships]] for the SQL view).
- The **owning side** is the one without `mappedBy`; Hibernate writes the FK only from it. Setting `order.setCustomer(c)` without `c.getOrders().add(order)` is still persisted; the reverse is not.

### Cascade (`CascadeType`)
Cascading propagates an `EntityManager` operation from the parent to related entities:
- `PERSIST` — save children when the parent is saved.
- `MERGE` — merge children when the parent is merged.
- `REMOVE` — delete children when the parent is deleted.
- `REFRESH` — reload children from the DB together with the parent.
- `DETACH` — detach children with the parent.
- `ALL` — all of the above.
- `orphanRemoval = true` is different: removing a child from the collection deletes its row, even without `REMOVE` on the parent.

## Example
```java
@Entity
@Table(name = "orders", indexes = @Index(columnList = "customer_id"))
public class Order {
    @Id
    @GeneratedValue(strategy = GenerationType.SEQUENCE, generator = "orders_seq")
    @SequenceGenerator(name = "orders_seq", sequenceName = "orders_id_seq", allocationSize = 50)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "customer_id")
    private Customer customer;

    @OneToMany(mappedBy = "order", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<OrderLine> lines = new ArrayList<>();

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private OrderStatus status;

    @Version
    private long version;

    public void addLine(OrderLine line) {   // keep both sides in sync
        lines.add(line);
        line.setOrder(this);
    }
}
```

## Pitfalls
- Lombok `@Data`/`@EqualsAndHashCode` on entities: `hashCode` over a generated id changes after persist, breaking `Set` membership; `toString` over lazy collections triggers loads or `LazyInitializationException`. Use `equals` on a business key or `instanceof` + id-not-null.
- `FetchType.EAGER` on `@ManyToOne` loads the whole graph on every `findById` and is the usual root of [[jpa-n-plus-one]].
- `GenerationType.IDENTITY` forces an immediate `INSERT` on `persist()` and disables batch inserts; use `SEQUENCE` with a pooled optimizer.
- `CascadeType.ALL` on `@ManyToOne` (child → parent) deletes the parent when a child is removed.
- Entity state transitions and when the SQL actually runs: [[jpa-persistence-context-and-entity-lifecycle]]; transactions around it: [[spring-transactional]].
