---
title: Абстракция кэширования Spring Cache
tags: [spring, caching, redis, caffeine, aop]
learned: 2026-10-22
---
## Кратко
`@EnableCaching` включает AOP-прокси, который перехватывает методы с `@Cacheable`, `@CachePut` и `@CacheEvict` и обращается к `CacheManager`, поэтому код сервиса не зависит от конкретного кэша: Caffeine (внутри процесса) меняется на Redis (общий) через конфигурацию. Ключи строятся из параметров метода или SpEL-выражения. Действуют те же правила прокси, что и в [[spring-transactional]]: методы должны быть публичными и вызываться из другого бина, иначе ничего не кэшируется.

## Основное

### Аннотации
| Аннотация | Поведение |
|---|---|
| `@Cacheable(cacheNames, key, condition, unless, sync)` | ищет ключ; при попадании возвращает закэшированное значение **без выполнения** метода; при промахе выполняет и сохраняет |
| `@CachePut(...)` | всегда выполняет метод и сохраняет результат (обновление кэша после изменения) |
| `@CacheEvict(cacheNames, key, allEntries, beforeInvocation)` | удаляет один ключ или весь кэш; по умолчанию после успешного выполнения метода |
| `@Caching(cacheable = ..., put = ..., evict = ...)` | объединяет несколько аннотаций на одном методе |
| `@CacheConfig(cacheNames = "...")` | значения по умолчанию на уровне класса |
- `sync = true` заставляет конкурентные вызовы с одним ключом ждать одного вычисления (защищает от лавины запросов в кэш, cache stampede); поддерживается менеджерами кэша Caffeine и Redis.
- `condition` вычисляется **до** вызова (`#id > 0`); `unless` — **после**, с доступом к `#result` (`unless = "#result == null"`).

### Ключи и SpEL
- Ключ по умолчанию: `SimpleKeyGenerator` — все параметры вместе (`SimpleKey`); ноль параметров → `SimpleKey.EMPTY`. Классы параметров должны реализовывать `equals/hashCode`.
- Свой ключ: `key = "#customerId"`, `key = "#cmd.id + ':' + #cmd.locale"`, `key = "#root.methodName"`, `key = "#p0"`. Избегай позиционного `#p0`, когда код скомпилирован без `-parameters` (плагины Spring Boot для Maven/Gradle добавляют его по умолчанию начиная с Boot 3.2; иначе Spring 6.1 больше не откатывается на имена в стиле `#a0`).
- `keyGenerator = "myGenerator"` — для бина, реализующего `KeyGenerator`.
- `cacheNames` логические; менеджер сопоставляет каждое имя с регионом хранилища (экземпляр Caffeine, префикс ключа в Redis).

### Менеджеры кэша
- **`ConcurrentMapCacheManager`**: используется Boot по умолчанию, когда больше ничего нет; без ограничений размера, без TTL, только для тестов.
- **Caffeine** (`spring-boot-starter-cache` + `com.github.ben-manes.caffeine:caffeine`): внутри JVM, `spring.cache.caffeine.spec=maximumSize=10000,expireAfterWrite=10m`. Самый быстрый; не общий между экземплярами; теряется при перезапуске.
- **Redis** (`spring-boot-starter-data-redis`, `spring.cache.type=redis`): общий для экземпляров, переживает перезапуски, TTL на каждый кэш через `RedisCacheConfiguration.entryTtl`; значения нужно сериализовать (JSON через `GenericJackson2JsonRedisSerializer` или по умолчанию JDK-сериализация, которая хрупкая). Хранится как обычные ключи `STRING` вида `cacheName::key`, см. [[redis-data-structures]].
- Адаптеры **JCache (JSR-107)** для Ehcache/Hazelcast/Infinispan; те же адаптеры использует кэш L2 в Hibernate, см. [[hibernate-caching]].
- Несколько менеджеров: пометь один `@Primary` или укажи `cacheManager = "redisCacheManager"` в аннотации; `CacheResolver` может выбирать во время выполнения.
- Boot автоматически настраивает менеджер по classpath; `spring.cache.cache-names` создаёт кэши заранее (иначе неизвестные имена создаются лениво с конфигурацией по умолчанию; Caffeine сразу падает, если `spring.cache.cache-names` задан, а имени в списке нет).

### Spring Cache и кэш Hibernate
- Spring Cache кэширует **возвращаемое значение метода** с ключом по аргументам: DTO, агрегаты, результаты удалённых вызовов. Он не знает, когда меняются строки в основе; вытеснять нужно явно.
- Hibernate L2 кэширует **сущности по id** и инвалидирует их при записи через ORM. См. [[hibernate-caching]].

## Пример
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
    public TariffDto byCode(String code) {               // DTO, никогда не сущность
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

## Подводные камни
- **Вызов самого себя (self-invocation)**: `this.byCode(code)` внутри того же класса обходит прокси; кэширования нет. Перенеси метод в другой бин или внедри сам себя через `ObjectProvider`/`@Lazy`.
- **Кэширование null**: по умолчанию результат `null` сохраняется (как маркер `NullValue`) и возвращается вечно; используй `unless = "#result == null"` или `disableCachingNullValues()`. Кроме того, исключение никогда не кэшируется, поэтому неудачный поиск повторяется при каждом вызове.
- **Кэширование сущностей**: JPA-сущность из Redis возвращается detached, ленивые поля бросают исключения, а JSON-сериализация двунаправленных связей уходит в рекурсию. Кэшируй DTO/record'ы.
- **Сериализация**: JDK-сериализация ломается при каждом изменении класса и между сервисами; используй JSON с информацией о типе или версионированный префикс ключа (`tariffs:v2`). `GenericJackson2JsonRedisSerializer` пишет `@class`, поэтому переименование DTO ничего не инвалидирует, и десериализация падает: вытесняй при deploy или меняй имя кэша.
- `@Cacheable` на методе с `@Transactional`: попадание в кэш полностью пропускает транзакцию (обычно это нормально); `@CacheEvict` до commit вытесняет значение, пока старое ещё видно другим транзакциям, и они снова кэшируют устаревшую строку. Вытесняй после commit (`TransactionAwareCacheManagerProxy` или `@TransactionalEventListener(AFTER_COMMIT)`).
- Коллизии ключей: `@Cacheable("users") byId(Long)` и `@Cacheable("users") byEmail(String)` делят одно имя и пространство ключей; `1L` и `"1"` различаются в Caffeine только типом, а после преобразования в строку для Redis — вообще ничем. Включай в ключ метод или префикс.
- Когда закэшированные данные должны быть строго согласованы с БД, ответ обычно не кэш, а правильный запрос или [[database-indexes]]; про дедупликацию запросов см. [[idempotency]].
