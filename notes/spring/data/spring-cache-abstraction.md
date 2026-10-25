---
title: Spring Cache abstraction
tags: [spring, caching, redis, caffeine, aop]
learned: 2026-10-22
---
## TL;DR
`@EnableCaching` turns on an AOP proxy that intercepts `@Cacheable`, `@CachePut` and `@CacheEvict` methods and talks to a `CacheManager`, so the service code is cache-agnostic: swap Caffeine (in-process) for Redis (shared) through configuration. Keys are built from the method parameters or a SpEL expression. The same proxy rules as [[spring-transactional]] apply: public methods, called from another bean, or nothing is cached.

## Key points

### The annotations
| Annotation | Behaviour |
|---|---|
| `@Cacheable(cacheNames, key, condition, unless, sync)` | look up the key; on hit return the cached value **without executing** the method; on miss execute and store |
| `@CachePut(...)` | always execute the method and store the result (refresh a cache after an update) |
| `@CacheEvict(cacheNames, key, allEntries, beforeInvocation)` | remove one key or the whole cache; by default after the method succeeds |
| `@Caching(cacheable = ..., put = ..., evict = ...)` | combine several on one method |
| `@CacheConfig(cacheNames = "...")` | class-level defaults |
- `sync = true` makes concurrent callers for the same key wait for one computation (prevents cache stampede); supported by Caffeine and Redis cache managers.
- `condition` is evaluated **before** the call (`#id > 0`); `unless` **after** with access to `#result` (`unless = "#result == null"`).

### Keys and SpEL
- Default key: `SimpleKeyGenerator` – all parameters combined (`SimpleKey`); zero parameters → `SimpleKey.EMPTY`. Parameter classes must implement `equals/hashCode`.
- Custom key: `key = "#customerId"`, `key = "#cmd.id + ':' + #cmd.locale"`, `key = "#root.methodName"`, `key = "#p0"`. Avoid positional `#p0` when the code is compiled without `-parameters` (Spring Boot's Maven/Gradle plugins add it by default since Boot 3.2; Spring 6.1 no longer falls back to `#a0`-style names otherwise).
- `keyGenerator = "myGenerator"` for a bean implementing `KeyGenerator`.
- `cacheNames` are logical; the manager maps each to a store region (a Caffeine instance, a Redis key prefix).

### Cache managers
- **`ConcurrentMapCacheManager`**: Boot's default when nothing else is present; unbounded, no TTL, only for tests.
- **Caffeine** (`spring-boot-starter-cache` + `com.github.ben-manes.caffeine:caffeine`): in-JVM, `spring.cache.caffeine.spec=maximumSize=10000,expireAfterWrite=10m`. Fastest; not shared across instances; lost on restart.
- **Redis** (`spring-boot-starter-data-redis`, `spring.cache.type=redis`): shared across instances, survives restarts, TTL per cache via `RedisCacheConfiguration.entryTtl`; values must be serialized (JSON via `GenericJackson2JsonRedisSerializer`, or JDK serialization by default, which is fragile). Stored as plain `STRING` keys `cacheName::key`, see [[redis-data-structures]].
- **JCache (JSR-107)** adapters for Ehcache/Hazelcast/Infinispan; the same adapters Hibernate's L2 cache uses, see [[hibernate-caching]].
- Several managers: mark one `@Primary`, or use `cacheManager = "redisCacheManager"` on the annotation; a `CacheResolver` can choose at runtime.
- Boot auto-configures the manager from the classpath; `spring.cache.cache-names` pre-creates caches (otherwise unknown names are created lazily with the default config; Caffeine fails fast if `spring.cache.cache-names` is set and the name is not listed).

### Spring Cache vs Hibernate cache
- Spring Cache caches the **return value of a method** keyed by arguments: DTOs, aggregates, remote-call results. It does not know when the underlying rows change; you evict explicitly.
- Hibernate L2 caches **entities by id** and invalidates them on ORM writes. See [[hibernate-caching]].

## Example
```java
@Configuration
@EnableCaching
public class CacheConfig {
    @Bean
    RedisCacheManagerBuilderCustomizer ttls() {
        var json = RedisSerializationContext.SerializationPair
                .fromSerializer(new GenericJackson2JsonRedisSerializer());
        return b -> b
            .withCacheConfiguration("tariffs", RedisCacheConfiguration.defaultCacheConfig()
                .entryTtl(Duration.ofHours(1)).serializeValuesWith(json).disableCachingNullValues())
            .withCacheConfiguration("balances", RedisCacheConfiguration.defaultCacheConfig()
                .entryTtl(Duration.ofSeconds(30)).serializeValuesWith(json));
    }
}

@Service
@RequiredArgsConstructor
public class TariffService {
    private final TariffRepository tariffs;

    @Cacheable(cacheNames = "tariffs", key = "#code", sync = true)
    public TariffDto byCode(String code) {               // DTO, never an entity
        return tariffs.findByCode(code).map(TariffDto::from)
                .orElseThrow(() -> new TariffNotFound(code));
    }

    @CachePut(cacheNames = "tariffs", key = "#result.code")
    @Transactional
    public TariffDto update(UpdateTariff cmd) {
        Tariff t = tariffs.findByCode(cmd.code()).orElseThrow();
        t.apply(cmd);
        return TariffDto.from(t);
    }

    @CacheEvict(cacheNames = "tariffs", allEntries = true)
    public void reloadAll() { }
}
```

## Pitfalls
- **Self-invocation**: `this.byCode(code)` inside the same class bypasses the proxy; no caching. Move the method to another bean or inject self via `ObjectProvider`/`@Lazy`.
- **Caching nulls**: by default a `null` result is stored (as a `NullValue` marker) and returned forever; use `unless = "#result == null"` or `disableCachingNullValues()`. Also: an exception is never cached, so a failing lookup is retried on every call.
- **Caching entities**: a JPA entity in Redis is detached when it comes back, lazy fields throw, and JSON serialization of bidirectional relations recurses. Cache DTOs/records.
- **Serialization**: JDK serialization breaks on every class change and across services; use JSON with type info or a versioned key prefix (`tariffs:v2`). `GenericJackson2JsonRedisSerializer` writes `@class`, so renaming a DTO invalidates nothing and deserialization fails: evict on deploy or change the cache name.
- `@Cacheable` on a `@Transactional` method: a cache hit skips the transaction entirely (usually fine); a `@CacheEvict` before commit evicts while the old value is still visible to other transactions, which then re-cache the stale row. Evict after commit (`TransactionAwareCacheManagerProxy`, or `@TransactionalEventListener(AFTER_COMMIT)`).
- Key collisions: `@Cacheable("users") byId(Long)` and `@Cacheable("users") byEmail(String)` share a name and key space; `1L` and `"1"` differ only by type in Caffeine and not at all once stringified for Redis. Include the method or a prefix in the key.
- When the cached data must be exactly consistent with the DB, the answer is usually not a cache but the right query or [[database-indexes]]; for request de-duplication see [[idempotency]].
