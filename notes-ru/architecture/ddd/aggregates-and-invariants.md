---
title: Агрегаты, инварианты и границы транзакции
tags: [ddd, aggregates, consistency, jpa]
learned: 2026-10-27
---
## Кратко
**Агрегат** (aggregate) — это группа объектов, которые должны быть согласованы *вместе*. Его **корень** (root) — единственная точка входа, а **инварианты** — правила, которые должны выполняться в конце каждой транзакции. Практический набор правил: один агрегат на транзакцию, ссылки на другие агрегаты по id, агрегаты небольшие, а всё, что затрагивает несколько агрегатов, становится согласованным в конечном счёте (eventual consistency) через [[domain-events-and-integration-events|доменные события]]. Id агрегата — это ещё и единица блокировки, партиционирования в Kafka и ключ в outbox.

## Основное

### Как провести границу
- Начинать нужно с инвариантов, а не с таблиц. «У SIM не больше одного активного профиля» — это инвариант между `Sim` и её `Profile`, поэтому они живут в одном агрегате. «У абонента не больше 5 SIM» звучит как инвариант `Subscriber`, но загружать все SIM ради добавления одной — ловушка масштабируемости. Обычно это *политика*, которую проверяют запросом с подсчётом и обеспечивают в конечном счёте.
- Вопрос: должны ли эти объекты меняться в одной транзакции, чтобы бизнес-правило оставалось верным? Да — один агрегат. Нет — отдельные агрегаты, ссылка по id, событие.
- Маленькие агрегаты выигрывают: `Order` со своими строками — нормально, `Customer` со всеми заказами — нет. Агрегат — это то, что можно загрузить, проверить и сохранить в одной короткой транзакции, не блокируя других пользователей.
- Всё внутреннее доступно только через корень. Внутренние сущности имеют локальную идентичность (номер `OrderLine` внутри заказа), а не глобальные id, используемые где-то ещё.

### Инварианты и где они живут
- Инварианты обеспечивают методы корня, а не сервис и не одна лишь база данных. `confirm()`, который бросает исключение, когда в заказе нет строк, — это и есть инвариант; ограничение `CHECK` — эшелонированная защита (defence in depth) ([[constraints-and-relationships]]).
- Конструктор и фабричные методы делают невалидный агрегат непредставимым: `Order.create(customerId, currency)` вместо `new Order()` + сеттеры ([[creational-patterns]], [[value-objects-and-entities]]).
- События забирают (pull), а не отправляют (push): корень записывает, что произошло (`events.add(new ProfileActivated(...))`), а прикладной слой публикует их после commit ([[use-cases-and-application-layer]]).

### Один агрегат на транзакцию
- Сценарий использования загружает один агрегат, вызывает один метод, сохраняет его. Изменение двух агрегатов в одной транзакции означает, что транзакция (и её блокировки) охватывает две единицы согласованности. Это работает в одной базе, но ломается в тот момент, когда агрегаты оказываются в двух сервисах ([[saga]]).
- Согласованность между агрегатами: доменное событие первого агрегата запускает обработчик, который меняет второй. Задержка — миллисекунды внутри процесса, секунды через Kafka; бизнес должен принять это окно.
- Исключения бывают (оба агрегата крошечные и всегда расположены вместе), но их нужно оговаривать явно.

### Конкурентный доступ
- По умолчанию — оптимистическая блокировка с колонкой `@Version` на корне. Два пользователя, редактирующие один агрегат, получают `OptimisticLockException`, и один из них делает retry ([[jpa-locking]]).
- Пессимистическая `SELECT ... FOR UPDATE` на строке корня, когда ожидается конкуренция (списание с баланса) ([[postgres-locks]]).
- Поскольку строка корня и есть блокировка, большой агрегат выстраивает в очередь несвязанных пользователей — ещё одна причина держать агрегаты маленькими.

### Агрегаты и инфраструктура
| Задача | Id агрегата используется как |
|---|---|
| Kafka | Ключ записи, чтобы все события одного агрегата оставались упорядоченными в одной партиции ([[kafka-producer]]) |
| Outbox | Колонка `aggregate_id`, ключ упорядочивания для relay ([[outbox-pattern]]) |
| Идемпотентность | Часть ключа дедупликации для команд ([[idempotency]]) |
| Шардирование | Естественный ключ шарда: агрегат никогда не охватывает несколько шардов ([[sharding-strategies]]) |
| Temporal | `workflowId` для одного долгоживущего процесса на агрегат ([[temporal-concepts]]) |

## Пример
Агрегат `Sim` с инвариантом «один активный профиль», версией для оптимистической блокировки и записанными доменными событиями.

```java
@Entity
@Table(name = "sim")
public class Sim {
    @Id private UUID id;
    @Version private long version;
    private String msisdn;

    @ElementCollection(fetch = FetchType.EAGER)      // маленькая коллекция, принадлежит корню
    @CollectionTable(name = "sim_profile", joinColumns = @JoinColumn(name = "sim_id"))
    private List<Profile> profiles = new ArrayList<>();

    @Transient private final List<DomainEvent> events = new ArrayList<>();

    protected Sim() {}                               // только для JPA

    public static Sim register(UUID id, Msisdn msisdn) {
        var sim = new Sim();
        sim.id = id;
        sim.msisdn = msisdn.value();
        return sim;
    }

    public void activate(Iccid iccid) {
        Profile target = profiles.stream().filter(p -> p.iccid().equals(iccid.value())).findFirst()
                .orElseThrow(() -> new ProfileNotFoundException(iccid));
        boolean anotherActive = profiles.stream()
                .anyMatch(p -> p.state() == ProfileState.ACTIVE && !p.iccid().equals(iccid.value()));
        if (anotherActive) throw new InvariantViolation("a SIM can have only one active profile");  // инвариант
        target.markActive();
        events.add(new ProfileActivated(id, iccid, Instant.now()));
    }

    public List<DomainEvent> pullEvents() {
        var copy = List.copyOf(events);
        events.clear();
        return copy;
    }
}
```

Прикладной сервис: `Sim sim = sims.findById(id).orElseThrow(); sim.activate(iccid); sims.save(sim); publisher.publish(sim.pullEvents());` внутри одного метода с `@Transactional` ([[spring-transactional]]).

## Подводные камни
- Анемичный агрегат: публичные сеттеры на корне, инвариант проверяется в `SimService.activate()`. Следующий вызывающий код забудет проверку.
- `@OneToMany` на сущности другого агрегата с `cascade = ALL`: границу транзакции теперь определяет граф ORM, а не бизнес-правило, и загружается полбазы ([[jpa-entities-and-relationships]], [[jpa-n-plus-one]]).
- Проверка инварианта запросом (`countActiveProfiles(simId) == 0`) вне блокировки агрегата — это гонка: две одновременные активации обе видят ноль ([[race-conditions-and-critical-sections]]).
- Изменяемые коллекции, отданные через геттеры (`getProfiles().add(...)`), обходят корень; нужно возвращать неизменяемые представления.
- Связанные заметки: [[ddd-basics]], [[value-objects-and-entities]], [[domain-events-and-integration-events]], [[event-sourcing]] (агрегаты, восстанавливаемые из событий).
