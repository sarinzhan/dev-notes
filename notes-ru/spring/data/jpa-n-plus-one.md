---
title: Проблема N+1 запросов и как её исправить
tags: [jpa, hibernate, performance, n-plus-one]
learned: 2026-10-22
---
## Кратко
N+1 возникает, когда один запрос загружает N родительских строк, а затем при обходе каждая ленивая связь загружается своим отдельным запросом: 1 + N выражений вместо 1 или 2. Исправляется под конкретный сценарий через `JOIN FETCH` или `@EntityGraph`, либо глобально смягчается пакетной загрузкой (`hibernate.default_batch_fetch_size`), которая превращает N запросов в N / размер пакета запросов с `IN (...)`. Обнаруживать её в тестах нужно подсчётом выражений, а не чтением кода.

## Основное

### Как она проявляется
- `List<Order> orders = repo.findAll()` → 1 `SELECT ... FROM orders`.
- `for (o : orders) o.getLines().size()` → N `SELECT ... FROM order_lines WHERE order_id = ?`, по одному на заказ, потому что `@OneToMany` — LAZY, и каждая коллекция — отдельный прокси.
- Вариант с EAGER не лучше: `@ManyToOne(fetch = EAGER)` на `OrderLine.product` заставляет Hibernate после JPQL-запроса выполнять `SELECT` на каждый отдельный продукт (JPQL не выполняет EAGER через join, а загружает жадно дополнительными select'ами).
- С `spring.jpa.open-in-view=true` лишние запросы срабатывают во время сериализации в JSON, так что сервис выглядит чистым, а запрос всё равно медленный (см. [[jpa-persistence-context-and-entity-lifecycle]]).

### Решение 1: `JOIN FETCH` (для конкретного запроса)
- `select o from Order o join fetch o.lines where o.customer.id = :id` → один SQL с join; коллекция инициализируется из того же набора результатов.
- Fetch join коллекции размножает родительские строки; Hibernate 6 автоматически убирает дубликаты родителей (`distinct` не нужен; в Hibernate 5 он был нужен).
- Нельзя сочетать с `Pageable`: Hibernate выполняет пагинацию в памяти с предупреждением `HHH90003004 firstResult/maxResults specified with collection fetch; applying in memory`. Сначала пагинируй родителей, затем загружай дочерние элементы для этих id.
- Fetch join двух коллекций сразу даёт декартово произведение и `MultipleBagFetchException` для `List` (bag); используй `Set` или два запроса.

### Решение 2: `@EntityGraph` (для метода репозитория)
- `@EntityGraph(attributePaths = {"lines", "lines.product"})` на методе репозитория или именованный граф на сущности; тот же эффект, что и у `JOIN FETCH` (использует `LEFT JOIN`), без переписывания запроса, работает с производными методами вроде `findByCustomerId`.
- `type = EntityGraph.EntityGraphType.FETCH` (по умолчанию) делает всё неперечисленное LAZY; `LOAD` оставляет для остального собственные настройки сущности.

### Решение 3: пакетная загрузка (глобальная страховка)
- `spring.jpa.properties.hibernate.default_batch_fetch_size=50` глобально или `@BatchSize(size = 50)` на коллекции/сущности.
- Как это работает: родители загружены; когда ты обращаешься к первой ленивой коллекции, Hibernate ищет в контексте персистентности других родителей того же типа с неинициализированными коллекциями и загружает до `size` из них через `select * from order_lines where order_id in (?, ?, ...)`. N запросов превращаются в `ceil(N / size)`.
- Hibernate 6 в Postgres использует параметры-массивы (`where order_id = any(?)`), чтобы форма подготовленного выражения была одна; в Hibernate 5 списки `IN` дополнялись до степеней двойки.
- Это смягчение, а не план: лишние обращения к БД всё равно есть, и для отсоединённых (detached) сущностей это ничего не даёт.

### Решение 4: вообще не загружать сущности
- DTO-проекция (`select new ...` или интерфейсная проекция) с явными join'ами возвращает ровно нужные столбцы и не содержит ленивых прокси; лучший вариант для эндпоинтов чтения.
- `@Fetch(FetchMode.SUBSELECT)` загружает все коллекции всех родителей из исходного запроса одним подзапросом; полезно, когда всегда обходишь весь список.

### Как обнаружить
- `spring.jpa.show-sql` печатает в stdout в обход логгера; используй `logging.level.org.hibernate.SQL=DEBUG` (и `org.hibernate.orm.jdbc.bind=TRACE` для параметров), чтобы выражения шли через Logback и попадали в Loki вместе с остальными логами, см. [[logging-stacks]]. В тестах используй счётчик выражений: `Statistics` Hibernate (`hibernate.generate_statistics=true`, `getPrepareStatementCount()`), `datasource-proxy` + `QueryCountHolder` или `SQLStatementCountValidator.assertSelectCount(1)` из `digma`/`hypersistence-utils`.
- Hibernate 6.2+: `hibernate.query.fail_on_pagination_over_collection_fetch=true` превращает предупреждение о пагинации в памяти в исключение.

## Пример
```java
public interface OrderRepository extends JpaRepository<Order, Long> {

    // Решение 1: явный fetch join, одно SQL-выражение
    @Query("select o from Order o join fetch o.lines l join fetch l.product where o.customer.id = :customerId")
    List<Order> findWithLinesByCustomer(@Param("customerId") Long customerId);

    // Решение 2: производный запрос + граф, та же форма SQL
    @EntityGraph(attributePaths = {"lines", "lines.product"})
    List<Order> findByStatus(OrderStatus status);

    // Пагинация: сначала родители, потом дочерние по id (2 запроса, без пагинации в памяти)
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

## Подводные камни
- `@OneToOne` на обратной стороне (`mappedBy`) не может быть ленивым без улучшения байткода (bytecode enhancement): Hibernate должен выполнить запрос, чтобы понять, вернуть `null` или прокси, поэтому каждая загрузка родителя добавляет select. Отображай внешний ключ на той стороне, с которой читаешь.
- `@ManyToOne` по умолчанию EAGER. В каждом отображении нужно явно писать `fetch = FetchType.LAZY`.
- Исправление N+1 через `EAGER` переносит проблему во все остальные запросы, которые загружают эту сущность.
- `findAll()` + `stream().map(toDto)` в методе `@Transactional(readOnly = true)` всё равно выполняет N+1; транзакция скрывает только исключение, но не запросы.
- Правила отображения, которые к этому приводят: [[jpa-entities-and-relationships]]; кэш второго уровня может замаскировать проблему для горячих строк, но по умолчанию не для коллекций: [[hibernate-caching]]. Тот же антипаттерн «запросы в цикле» на стороне SQL — причина предпочитать join'ы: [[sql-joins-and-commands]].
