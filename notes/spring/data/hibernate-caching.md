---
title: Hibernate first-level, second-level and query cache
tags: [hibernate, jpa, caching, performance]
learned: 2026-10-22
---
## TL;DR
Hibernate has two cache levels. The **first-level cache** is the persistence context itself: per `EntityManager`/transaction, always on, cannot be disabled; it guarantees one Java object per row and skips repeated `find()`. The **second-level cache** (L2) is optional, shared across all sessions in the `SessionFactory`, backed by a provider (JCache + Ehcache/Caffeine, Infinispan, Hazelcast), and stores entity state by id, plus collections and query results if enabled. L2 is worth it for read-mostly reference data; it is wrong for data other services write directly to the DB.

## Key points

### First-level cache (session cache)
- Scope: one `EntityManager`, i.e. one transaction in Spring. Cleared on `clear()`, `close()`, transaction end.
- `find(Order.class, 1)` twice → one SQL. `getReference()` returns a proxy and never hits the DB until a field is accessed.
- It only short-circuits lookups **by id**. A JPQL `where id = 1` still goes to the DB (after an auto-flush), then the returned row is matched to the already managed instance so identity still holds.
- It is also the write buffer: dirty checking and flush ordering live here. Details in [[jpa-persistence-context-and-entity-lifecycle]].

### Second-level cache
- Scope: the whole application (one `SessionFactory`), survives transactions. A `find()` miss in L1 checks L2 before SQL; a hit *hydrates* a new entity instance from the cached disassembled state (L2 stores arrays of column values, not objects).
- Enable: `spring.jpa.properties.hibernate.cache.use_second_level_cache=true`, a region factory (`hibernate.cache.region.factory_class=jcache` with `hibernate-jcache` + Ehcache 3 or Caffeine's JCache adapter), and `@Cacheable @Cache(usage = CacheConcurrencyStrategy.READ_WRITE)` on each entity. `jakarta.persistence.sharedCache.mode=ENABLE_SELECTIVE` is the default: only annotated entities.
- Concurrency strategies:
| Strategy | Use for | Behaviour |
|---|---|---|
| `READ_ONLY` | immutable reference data (countries, tariffs) | fastest; update throws |
| `NONSTRICT_READ_WRITE` | rarely updated, stale reads tolerable | invalidates after commit; brief window of stale data |
| `READ_WRITE` | normal updatable entities | soft locks in cache during update; consistent under READ COMMITTED |
| `TRANSACTIONAL` | JTA + transactional cache (Infinispan) | full XA consistency |
- Collection cache: `@Cache` on the `@OneToMany` field caches the list of child **ids**; the children themselves must be cached entities, otherwise every hit still loads them by id (an N+1 against L2, see [[jpa-n-plus-one]]).
- Invalidation is per entity by id on `persist`/`merge`/`remove` through Hibernate. Bulk JPQL updates evict whole regions. Direct SQL (`@Modifying` native, another service, a DBA script) is invisible: stale data until TTL or eviction.
- In a cluster each JVM has its own L2 unless the provider is distributed (Hazelcast, Infinispan, Redis via a JCache provider). Local L2 + several instances = each node serves different stale states.

### Query cache
- `hibernate.cache.use_query_cache=true` + `query.setHint("org.hibernate.cacheable", true)` (or `@QueryHints` on a repository method). Caches the **result ids** of a specific query string + parameters; entities must be in L2 or you get the same id-by-id loads.
- Invalidation is coarse: the `UpdateTimestampsCache` records the last modification time per table, and any write to a table used by the query invalidates all its cached results. For frequently written tables the hit ratio is near zero.
- Good fit: dropdown lists, configuration tables; bad fit: anything with a `where created_at > now() - ...` or user-specific filters.

### Hibernate cache vs Spring Cache
- Hibernate caches **entities by id** transparently inside the ORM; [[spring-cache-abstraction]] caches **method results** (DTOs, computed values) and knows nothing about tables. For HTTP responses or aggregated views, Spring Cache over a Redis store ([[redis-data-structures]]) is usually the better layer; for hot `findById` on reference tables, Hibernate L2 is simpler.

## Example
```java
@Entity
@Cacheable
@org.hibernate.annotations.Cache(usage = CacheConcurrencyStrategy.READ_WRITE, region = "tariffs")
public class Tariff {
    @Id private Long id;
    private String code;
    private BigDecimal pricePerMb;

    @OneToMany(mappedBy = "tariff")
    @org.hibernate.annotations.Cache(usage = CacheConcurrencyStrategy.READ_WRITE)
    private Set<TariffOption> options = new HashSet<>();
}

public interface TariffRepository extends JpaRepository<Tariff, Long> {
    @QueryHints(@QueryHint(name = "org.hibernate.cacheable", value = "true"))
    List<Tariff> findByActiveTrue();
}
```
```yaml
spring:
  jpa:
    properties:
      hibernate:
        cache:
          use_second_level_cache: true
          use_query_cache: true
          region.factory_class: jcache
        javax.cache.provider: org.ehcache.jsr107.EhcacheCachingProvider
        javax.cache.uri: classpath:ehcache.xml
        generate_statistics: true   # watch secondLevelCacheHitCount before trusting it
```

## Pitfalls
- Enabling L2 on a table that another system writes directly produces silent stale reads; there is no change-data capture in Hibernate.
- `READ_WRITE` with a non-transactional provider still gives a short window where a concurrent reader sees the old value after commit; use `TRANSACTIONAL` or accept it.
- Caching an entity with many lazy associations rarely helps: the associations still load unless cached too.
- `hibernate.cache.use_query_cache=true` without `@Cacheable` entities makes things slower, not faster.
- Measure with `Statistics` (`hibernate.generate_statistics`) or Micrometer's `HibernateMetrics`; a cache nobody hits is just memory and invalidation bugs.
- Repository- and service-level caching alternatives: [[spring-cache-abstraction]]; the same consistency trade-offs as any cache in front of a DB: [[idempotency]] for safe re-computation.
