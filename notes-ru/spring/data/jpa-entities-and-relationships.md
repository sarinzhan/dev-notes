---
title: Сущности JPA и отображение связей
tags: [jpa, hibernate, orm, spring-data]
learned: 2026-10-22
---
## Кратко
JPA (Jakarta Persistence) — спецификация интерфейсов для ORM: отображения Java-объектов на реляционные таблицы и обратно. Hibernate — реализация, которую использует Spring Boot. Сущность (entity) — класс с аннотацией `@Entity`, который представляет таблицу (или представление); связи между сущностями объявляются через `@OneToOne`, `@OneToMany`, `@ManyToOne`, `@ManyToMany`, а физический внешний ключ находится на *владеющей* стороне (owning side) и описывается `@JoinColumn` или `@JoinTable`. Самые важные значения по умолчанию: `@ManyToOne`/`@OneToOne` — EAGER, коллекции — LAZY.

## Основное

### JPA, ORM, Hibernate
- **ORM** отображает объекты на таблицы, чтобы приложение работало с «виртуальной объектной базой данных». JPA описывает, *какие* интерфейсы ты получаешь (`EntityManager`, `Query`, `Criteria`), а Hibernate — провайдер, который выполняет SQL.
- Начиная с Jakarta EE 9 пакет называется `jakarta.persistence`, а не `javax.persistence`. Spring Boot 3.x использует Jakarta Persistence 3.1 и Hibernate 6.x; импорт `javax.persistence` в Boot 3 — ошибка компиляции.
- **`DataSource`** описывает, как получить JDBC-`Connection` (пул соединений — HikariCP, см. [[hikaricp-pool-sizing]]). **`EntityManager`** — главный интерфейс JPA: он управляет состоянием сущностей внутри контекста персистентности и создаёт запросы. Репозитории Spring Data оборачивают его (см. [[spring-data-repositories]]).
- Инициализация схемы: `spring.jpa.hibernate.ddl-auto=create|update|validate|none` позволяет Hibernate генерировать таблицы по сущностям. В продакшене используй `validate` и управляй схемой через Flyway/Liquibase; `update` никогда не удаляет и не сужает столбцы и скрывает ошибки отображения.

### Что делает класс сущностью
| Аннотация | Значение |
|---|---|
| `@Entity` | класс управляется JPA; нужен конструктор без аргументов (как минимум `protected`), класс не должен быть `final` |
| `@Table(name, schema, uniqueConstraints, indexes)` | какая таблица за ним стоит; по умолчанию — имя класса |
| `@Id` | поле-идентификатор; обязательно |
| `@GeneratedValue(strategy)` | `IDENTITY` (автоинкремент БД, отключает пакетную обработку JDBC), `SEQUENCE` (предпочтительно для Postgres, дружит с пакетами, `allocationSize`), `TABLE` (медленно, избегать), `AUTO`, `UUID` (JPA 3.1) |
| `@Column(name, nullable, length, unique, columnDefinition)` | отображение столбца; по умолчанию — имя поля |
| `@Version` | счётчик оптимистической блокировки, см. [[jpa-locking]] |
| `@Enumerated(EnumType.STRING)` | всегда STRING; `ORDINAL` ломается при изменении порядка |

- **`@Entity` и `@Component`**: `@Entity` — аннотация JPA. Она помечает класс с данными, экземпляры которого создаются по одному на строку и управляются контекстом персистентности; создаёт их Hibernate, а не Spring. `@Component` — стереотип Spring: один singleton-бин, который создаёт и связывает IoC-контейнер (см. [[spring-ioc-and-di]]), он содержит поведение, а не строки. Сущность никогда не бывает бином, и в неё нельзя внедрять зависимости.

### Связи
- Атрибуты `@OneToOne`: `mappedBy` (имя поля на владеющей стороне, используется на обратной стороне), `fetch` (по умолчанию **EAGER**), `optional` (по умолчанию `true`; `false` позволяет Hibernate использовать inner join и пропустить дополнительный select «а не null ли там?»), `cascade`, `orphanRemoval` (по умолчанию `false`).
- `@OneToMany`: `cascade`, `fetch` (по умолчанию **LAZY**), `mappedBy`, `orphanRemoval`, `targetEntity`. Однонаправленный `@OneToMany` без `@JoinColumn` создаёт связующую таблицу; предпочтительна двунаправленная связь с `@ManyToOne` в роли владельца.
- `@ManyToOne`: на практике владеющая сторона; fetch по умолчанию **EAGER**, и его почти всегда стоит выставлять в `LAZY`.
- `@ManyToMany`: `cascade`, `fetch` (по умолчанию **LAZY**), `mappedBy`, `targetEntity`. Никогда не ставь на неё `CascadeType.REMOVE`: удаление одной стороны удалит общие строки на другой.
- `@JoinColumn` задаёт имя столбца внешнего ключа; используется с `@OneToOne`, `@ManyToOne` и однонаправленным `@OneToMany`. `@JoinTable` задаёт имя связующей таблицы и двух её столбцов внешних ключей для `@ManyToMany` (SQL-сторона описана в [[constraints-and-relationships]]).
- **Владеющая сторона** — та, у которой нет `mappedBy`; Hibernate записывает внешний ключ только с неё. `order.setCustomer(c)` без `c.getOrders().add(order)` всё равно сохранится; обратное — нет.

### Каскады (`CascadeType`)
Каскадирование распространяет операцию `EntityManager` с родителя на связанные сущности:
- `PERSIST` — сохранять дочерние сущности при сохранении родителя.
- `MERGE` — выполнять merge дочерних при merge родителя.
- `REMOVE` — удалять дочерние при удалении родителя.
- `REFRESH` — перечитывать дочерние из БД вместе с родителем.
- `DETACH` — отсоединять дочерние вместе с родителем.
- `ALL` — всё перечисленное.
- `orphanRemoval = true` — другое: удаление дочернего элемента из коллекции удаляет его строку, даже без `REMOVE` на родителе.

## Пример
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

    public void addLine(OrderLine line) {   // синхронизируем обе стороны
        lines.add(line);
        line.setOrder(this);
    }
}
```

## Подводные камни
- Lombok `@Data`/`@EqualsAndHashCode` на сущностях: `hashCode` по сгенерированному id меняется после persist и ломает принадлежность к `Set`; `toString` по ленивым коллекциям вызывает загрузки или `LazyInitializationException`. Используй `equals` по бизнес-ключу или `instanceof` + проверку id на null.
- `FetchType.EAGER` на `@ManyToOne` загружает весь граф при каждом `findById` и обычно является корнем [[jpa-n-plus-one]].
- `GenerationType.IDENTITY` вынуждает немедленный `INSERT` при `persist()` и отключает пакетные вставки; используй `SEQUENCE` с пулирующим оптимизатором (pooled optimizer).
- `CascadeType.ALL` на `@ManyToOne` (дочерняя → родительская) удаляет родителя при удалении дочерней сущности.
- Переходы состояний сущности и момент, когда реально выполняется SQL: [[jpa-persistence-context-and-entity-lifecycle]]; транзакции вокруг этого: [[spring-transactional]].
