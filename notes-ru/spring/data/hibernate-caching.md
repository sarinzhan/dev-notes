---
title: Кэш первого и второго уровня и кэш запросов в Hibernate
tags: [hibernate, jpa, caching, performance]
learned: 2026-10-22
---
## Кратко
В Hibernate два уровня кэша. **Кэш первого уровня (first-level cache)** — это сам контекст персистентности: один на `EntityManager`/транзакцию, всегда включён и не отключается; он гарантирует один Java-объект на строку и избавляет от повторных `find()`. **Кэш второго уровня (second-level cache, L2)** необязателен, общий для всех сессий в `SessionFactory`, работает поверх провайдера (JCache + Ehcache/Caffeine, Infinispan, Hazelcast) и хранит состояние сущностей по id, а при включении — ещё коллекции и результаты запросов. L2 оправдан для справочных данных, которые в основном читаются; он не подходит для данных, которые другие сервисы пишут напрямую в БД.

## Основное

### Кэш первого уровня (кэш сессии)
- Область действия: один `EntityManager`, то есть в Spring одна транзакция. Очищается при `clear()`, `close()`, завершении транзакции.
- `find(Order.class, 1)` дважды → один SQL. `getReference()` возвращает прокси и не обращается к БД, пока не будет прочитано поле.
- Он сокращает только поиск **по id**. JPQL `where id = 1` всё равно идёт в БД (после автоматического flush), а затем возвращённая строка сопоставляется с уже управляемым экземпляром, так что идентичность сохраняется.
- Это ещё и буфер записи: здесь живут проверка изменений (dirty checking) и порядок flush. Подробности в [[jpa-persistence-context-and-entity-lifecycle]].

### Кэш второго уровня
- Область действия: всё приложение (один `SessionFactory`), переживает транзакции. При промахе `find()` в L1 сначала проверяется L2, а уже потом выполняется SQL; при попадании новый экземпляр сущности *гидратируется* из закэшированного разобранного состояния (L2 хранит массивы значений столбцов, а не объекты).
- Включение: `spring.jpa.properties.hibernate.cache.use_second_level_cache=true`, фабрика регионов (`hibernate.cache.region.factory_class=jcache` с `hibernate-jcache` + Ehcache 3 или JCache-адаптер Caffeine) и `@Cacheable @Cache(usage = CacheConcurrencyStrategy.READ_WRITE)` на каждой сущности. `jakarta.persistence.sharedCache.mode=ENABLE_SELECTIVE` — значение по умолчанию: кэшируются только помеченные сущности.
- Стратегии конкурентного доступа:
| Стратегия | Для чего | Поведение |
|---|---|---|
| `READ_ONLY` | неизменяемые справочные данные (страны, тарифы) | самая быстрая; обновление бросает исключение |
| `NONSTRICT_READ_WRITE` | редко обновляемые данные, устаревшее чтение допустимо | инвалидирует после commit; короткое окно устаревших данных |
| `READ_WRITE` | обычные изменяемые сущности | мягкие блокировки (soft locks) в кэше на время обновления; согласованность при READ COMMITTED |
| `TRANSACTIONAL` | JTA + транзакционный кэш (Infinispan) | полная XA-согласованность |
- Кэш коллекций: `@Cache` на поле `@OneToMany` кэширует список **id** дочерних элементов; сами дочерние элементы должны быть закэшированными сущностями, иначе каждое попадание всё равно загружает их по id (N+1 против L2, см. [[jpa-n-plus-one]]).
- Инвалидация идёт по каждой сущности по id при `persist`/`merge`/`remove` через Hibernate. Массовые JPQL-обновления вытесняют целые регионы. Прямой SQL (нативный `@Modifying`, другой сервис, скрипт DBA) невидим: данные устаревшие до истечения TTL или вытеснения.
- В кластере у каждой JVM свой L2, если провайдер не распределённый (Hazelcast, Infinispan, Redis через JCache-провайдер). Локальный L2 + несколько экземпляров = каждый узел отдаёт своё устаревшее состояние.

### Кэш запросов
- `hibernate.cache.use_query_cache=true` + `query.setHint("org.hibernate.cacheable", true)` (или `@QueryHints` на методе репозитория). Кэширует **id результатов** конкретной строки запроса + параметров; сущности должны быть в L2, иначе получаются те же загрузки по одному id.
- Инвалидация грубая: `UpdateTimestampsCache` хранит время последнего изменения по каждой таблице, и любая запись в таблицу, используемую запросом, инвалидирует все его закэшированные результаты. Для часто изменяемых таблиц доля попаданий близка к нулю.
- Хорошо подходит: выпадающие списки, таблицы конфигурации; плохо подходит: всё, где есть `where created_at > now() - ...` или фильтры под конкретного пользователя.

### Кэш Hibernate и Spring Cache
- Hibernate прозрачно кэширует **сущности по id** внутри ORM; [[spring-cache-abstraction]] кэширует **результаты методов** (DTO, вычисленные значения) и ничего не знает о таблицах. Для HTTP-ответов или агрегированных представлений обычно лучше подходит Spring Cache поверх хранилища Redis ([[redis-data-structures]]); для горячих `findById` по справочным таблицам проще Hibernate L2.

## Пример
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
        generate_statistics: true   # следи за secondLevelCacheHitCount, прежде чем доверять кэшу
```

## Подводные камни
- Включение L2 для таблицы, в которую напрямую пишет другая система, приводит к молчаливому чтению устаревших данных; в Hibernate нет захвата изменений (change-data capture).
- `READ_WRITE` с нетранзакционным провайдером всё равно оставляет короткое окно, когда конкурентный читатель видит старое значение после commit; используй `TRANSACTIONAL` или смирись с этим.
- Кэширование сущности с множеством ленивых связей редко помогает: связи всё равно загружаются, если не закэшированы тоже.
- `hibernate.cache.use_query_cache=true` без сущностей с `@Cacheable` делает всё медленнее, а не быстрее.
- Измеряй через `Statistics` (`hibernate.generate_statistics`) или `HibernateMetrics` из Micrometer; кэш, в который никто не попадает, — это просто потраченная память и ошибки инвалидации.
- Альтернативы с кэшированием на уровне репозитория и сервиса: [[spring-cache-abstraction]]; те же компромиссы согласованности, что и у любого кэша перед БД: [[idempotency]] — для безопасного повторного вычисления.
