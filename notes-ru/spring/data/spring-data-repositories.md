---
title: Репозитории Spring Data и способы писать запросы
tags: [spring-data, jpa, repositories, querydsl]
learned: 2026-10-22
---
## Кратко
Репозиторий Spring Data — это интерфейс; Spring генерирует реализацию (`SimpleJpaRepository` поверх `EntityManager`) при запуске. Иерархия: `Repository` → `CrudRepository` → `PagingAndSortingRepository` → `JpaRepository`. Запросы берутся из имён методов, `@Query` (JPQL или нативный SQL), Criteria API, спецификаций (Specifications) или QueryDSL. Обновления и удаления, написанные как запросы, требуют `@Modifying`.

## Основное

### Интерфейсы репозиториев
- `Repository<T, ID>` — **маркерный интерфейс**: он лишь сообщает Spring Data, что его подтипы — репозитории, которые нужно найти при сканировании и проксировать. Он помечен `@Indexed`, поэтому каждый подынтерфейс попадает в индекс компонентов (`spring-context-indexer`) и находится при сканировании без обхода classpath.
- `CrudRepository` добавляет `save`, `saveAll`, `findById`, `existsById`, `findAll`, `count`, `deleteById`, `delete`.
- `ListCrudRepository` (Spring Data 3.0) возвращает `List` вместо `Iterable`.
- `PagingAndSortingRepository` добавляет `findAll(Pageable)` и `findAll(Sort)`. Начиная со Spring Data 3.0 он больше не расширяет `CrudRepository`; объединяй их сам или используй `JpaRepository`.
- `JpaRepository` = `ListCrudRepository` + `ListPagingAndSortingRepository` + дополнения JPA: `flush()`, `saveAndFlush()`, `deleteAllInBatch()`, `getReferenceById()` (ленивый прокси без `SELECT`).
- `@NoRepositoryBean` на промежуточном интерфейсе (например, `BaseRepository<T, ID>` с общими методами) не даёт Spring создать для него бин.
- `save()` вызывает `persist()`, когда сущность новая (`id == null`, или `@Version` равен null, или `Persistable.isNew()`), иначе `merge()`. `merge()` для сущности с вручную назначенным id выполняет дополнительный `SELECT`.

### Способы написать запрос
| Способ | Как | Когда |
|---|---|---|
| Имя метода | `findByStatusAndCreatedAtAfter(...)` разбирается `PartTreeJpaQuery` | простые фильтры; длинные имена становятся нечитаемыми |
| JPQL / HQL `@Query` | имена сущностей и полей, а не таблиц; параметры `:named` или `?1` | большинство нестандартных чтений; проходит через контекст персистентности |
| Нативный `@Query(nativeQuery = true)` | обычный SQL; результат отображается на сущность, интерфейсную проекцию или `Tuple` | SQL, специфичный для БД (`jsonb`, CTE, `FOR UPDATE SKIP LOCKED`) |
| Criteria API | `CriteriaBuilder` → `CriteriaQuery<T>` → `Root<T>` → `Predicate` | динамические фильтры; многословно, типобезопасно со сгенерированной метамоделью |
| `JpaSpecificationExecutor` | `Specification<T>` = переиспользуемая лямбда поверх Criteria | составные динамические фильтры в репозиториях |
| QueryDSL | сгенерированные `Q`-классы, `BooleanBuilder`, `Predicate`, `QuerydslPredicateExecutor<T>` | то же, что Criteria, но читаемо |
| Своя реализация | `OrderRepositoryCustom` + `OrderRepositoryImpl` (суффикс `Impl`), внедрённый `EntityManager` | всё остальное |

- Нативный запрос обходит разбор JPQL, но по-прежнему возвращает управляемые сущности, если отображается на сущность. Чего он **не** делает — так это не вызывает автоматический flush ожидающих изменений контекста персистентности перед выполнением (Hibernate делает flush, только когда известно, что нативный запрос затрагивает нужные таблицы; задай query spaces или вызови `flush()`), см. [[jpa-persistence-context-and-entity-lifecycle]].
- Понятия Criteria API: `CriteriaBuilder` (фабрика запросов и предикатов, берётся из `EntityManager`), `CriteriaQuery<T>` (запрос, типизированный по результату), `Root<T>` (сущность во `FROM`), `Predicate` (условие `WHERE`).
- Понятия QueryDSL: `BooleanBuilder` (построитель предиката, обычный `new`, `EntityManager` не нужен), `QOrder`/Q-классы (генерируются процессором аннотаций при сборке, по одному на сущность), `Predicate`, `QuerydslPredicateExecutor<T>`, подмешиваемый в репозиторий. На Hibernate 6 / Jakarta используй артефакт `querydsl-jpa` с классификатором `jakarta`.

### `@Modifying`
- JPQL- или нативный `UPDATE`/`DELETE` в `@Query` должен быть помечен `@Modifying`, чтобы Spring Data вызвал `executeUpdate()` и вернул количество затронутых строк (`int`/`void`), а не пытался построить набор результатов. Методу также нужна транзакция: `@Transactional` на нём или на вызывающем коде (см. [[spring-transactional]]).
- Массовые JPQL-обновления минуют контекст персистентности: уже загруженные сущности сохраняют устаревшие значения. `@Modifying(clearAutomatically = true, flushAutomatically = true)` сначала сбрасывает ожидающие изменения, а после очищает контекст.

### Пагинация и проекции
- `Page<T>` выполняет второй запрос `count`; `Slice<T>` загружает `size + 1` строк и пропускает подсчёт; для бесконечной прокрутки предпочтителен `Slice`.
- Интерфейсные проекции или проекции в record (`select new com.x.OrderView(o.id, o.total)`) позволяют не загружать сущности, которые ты не будешь менять; сочетай с `@Transactional(readOnly = true)`.

## Пример
```java
public interface OrderRepository extends JpaRepository<Order, Long>, JpaSpecificationExecutor<Order> {

    List<Order> findByCustomerIdAndStatus(Long customerId, OrderStatus status);

    @Query("select o from Order o join fetch o.lines where o.id = :id")
    Optional<Order> findWithLines(@Param("id") Long id);

    @Modifying(clearAutomatically = true)
    @Transactional
    @Query("update Order o set o.status = 'EXPIRED' where o.status = 'NEW' and o.createdAt < :before")
    int expireOlderThan(@Param("before") Instant before);

    @Query(value = """
            select * from orders
            where status = 'NEW'
            order by created_at
            limit :n for update skip locked
            """, nativeQuery = true)
    List<Order> lockNextBatch(@Param("n") int n);
}
```

## Подводные камни
- `@Query` с опечаткой в поле сущности падает при **запуске** (это хорошо); нативный запрос с опечаткой падает **во время выполнения** при первом вызове (тестируй его).
- Имена производных запросов тоже проверяются при запуске; несуществующее свойство прерывает создание контекста с `PropertyReferenceException`.
- `findAll()` на большой таблице без `Pageable` загружает всё в контекст персистентности.
- `deleteById` сначала делает `findById`, а затем `remove` (два выражения, каскады применяются); `deleteAllInBatch` — одно JPQL-удаление без каскадов.
- Методы репозитория по умолчанию транзакционные (`SimpleJpaRepository` помечен `@Transactional(readOnly = true)`, а методы записи это переопределяют), но собственное чтение через `@Query` внутри сервиса без транзакции выполняется само по себе и потом не сможет лениво догрузить данные; см. [[spring-transactional]] и [[jpa-n-plus-one]].
- Аннотации блокировок на методах репозитория: [[jpa-locking]].
