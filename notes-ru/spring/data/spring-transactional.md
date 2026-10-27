---
title: @Transactional: как это работает на самом деле
tags: [transactions, jpa, aop, spring-boot]
learned: 2026-09-18
---
## Кратко
`@Transactional` — это AOP-прокси вокруг бина ([[spring-aop]]). Прокси открывает транзакцию перед методом, привязывает соединение к потоку и после метода выполняет commit или откат. Всё, что обходит прокси (вызов самого себя, приватные методы, `new`), обходит и транзакцию. Держи транзакции короткими и без сетевых вызовов.

## Основное

### Механизм прокси
- Spring создаёт подкласс CGLIB (или JDK-прокси для интерфейсов) и внедряет прокси, а не твой объект. `TransactionInterceptor` оборачивает вызов: `getTransaction()` → метод → `commit()`/`rollback()`.
- Соединение и JPA-`EntityManager` хранятся в thread-local'ах `TransactionSynchronizationManager`. Запуск потока (или `CompletableFuture`) внутри метода выполняется вне транзакции ([[completable-future]]).
- **Ловушка вызова самого себя (self-invocation)**: `this.save()` вызывает «сырой» объект, а не прокси, поэтому аннотация на `save` игнорируется. Решения: перенести метод в другой бин, внедрить себя через `@Lazy` или использовать `TransactionTemplate`.
- `private`-методы CGLIB проксировать не может; `final`-методы и классы нельзя переопределить: аннотация молча игнорируется. Spring Boot 3 сразу падает на `final`-классах, только когда AOP нужен подкласс. Делай транзакционные методы `public` (protected и package-private работают с CGLIB начиная со Spring 6, но это непереносимо).

### Распространение (propagation)
| Тип | Есть транзакция | Нет транзакции |
|---|---|---|
| `REQUIRED` (по умолчанию) | присоединиться | создать новую |
| `REQUIRES_NEW` | приостановить её, открыть **второе соединение** | создать новую |
| `NESTED` | точка сохранения (savepoint) внутри неё (только JDBC, по умолчанию не JPA) | создать новую |
| `SUPPORTS` | присоединиться | выполнить без транзакции |
| `NOT_SUPPORTED` | приостановить её, выполнить без транзакции | выполнить без транзакции |
| `MANDATORY` | присоединиться | бросить `IllegalTransactionStateException` |
| `NEVER` | бросить исключение | выполнить без транзакции |
- `REQUIRES_NEW` — для журналов аудита, которые должны пережить откат. Две вложенные такие транзакции могут привести к deadlock в маленьком [[hikaricp-pool-sizing|пуле]]: внешняя держит одно соединение, ожидая внутреннюю.
- При `REQUIRED` внутренний метод, который бросил исключение, перехваченное внешним, всё равно помечает транзакцию как **rollback-only**; commit внешней падает с `UnexpectedRollbackException`.

### Изоляция, правила отката, атрибуты
- Уровень изоляции по умолчанию — тот, что задан по умолчанию в базе: `READ COMMITTED` для Postgres ([[postgres-isolation-levels]], [[transactions-acid-and-isolation]]). Задавай его на уровне метода, только понимая цену; `SERIALIZABLE` означает retry при `40001`.
- Правило отката по умолчанию: `RuntimeException` и `Error` откатывают транзакцию, **проверяемые (checked) исключения приводят к commit**. Используй `rollbackFor = Exception.class` или делай доменные исключения непроверяемыми ([[exceptions]]).
- `readOnly = true`: Hibernate выставляет `FlushMode.MANUAL` и пропускает проверку изменений; JDBC-драйвер получает `setReadOnly(true)`; маршрутизирующий источник данных (routing datasource) может направить вызов на реплику. Это подсказка, а не защита: на некоторых драйверах запись всё равно возможна.
- `timeout = 5` (секунд) заставляет упасть следующее выражение после дедлайна; уже выполняющийся запрос он не прерывает.

### Программные альтернативы
- `TransactionTemplate.execute(status -> ...)` даёт явные границы, работает с лямбдами и в случае вызова самого себя и делает область транзакции видимой в коде.
- `TransactionSynchronizationManager.registerSynchronization(...)` или `@TransactionalEventListener(phase = AFTER_COMMIT)` выполняет код после успешного commit: вытеснение из кэша, метрики, отправку уведомления. Учти, что колбэк после commit выполняется в том же потоке, всё ещё внутри интерсептора, и любая запись в БД там требует `REQUIRES_NEW`.

### Побочные эффекты внутри транзакции
- Отправка в Kafka или HTTP-вызов внутри `@Transactional`: соединение удерживается на время сетевой задержки, БД всё ещё может откатиться после отправки сообщения (фантомное событие), а retry отправит его повторно. Используй [[outbox-pattern]] или слушатель после commit для уведомлений по принципу best effort.
- Open Session In View (`spring.jpa.open-in-view`, в Boot по умолчанию `true`): `EntityManager` остаётся открытым, пока не записан ответ, поэтому ленивая загрузка работает в контроллерах, но соединения удерживаются во время сериализации и отрисовки шаблонов. Выставь `false` и отображай в DTO внутри сервиса ([[jpa-persistence-context-and-entity-lifecycle]], [[jpa-n-plus-one]]).

## Пример
```java
@Service
@RequiredArgsConstructor
public class OrderService {
    private final OrderRepository orders;
    private final OutboxRepository outbox;
    private final TransactionTemplate tx;
    private final ApplicationEventPublisher events;

    @Transactional(rollbackFor = Exception.class, timeout = 5)
    public Order place(PlaceOrder cmd) throws QuotaExceededException {
        var order = orders.save(Order.from(cmd));
        outbox.save(OutboxEvent.of("OrderPlaced", order));  // та же транзакция, см. паттерн outbox
        events.publishEvent(new OrderPlaced(order.id()));   // доставляется после commit (слушатель ниже)
        return order;
    }

    public void importBatch(List<PlaceOrder> cmds) {
        for (var cmd : cmds) {
            tx.executeWithoutResult(status -> orders.save(Order.from(cmd))); // одна транзакция на строку, проблемы self-invocation нет
        }
    }

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onPlaced(OrderPlaced e) { cache.evict(e.orderId()); }
}
```

## Подводные камни
- Вызов внешнего HTTP API внутри транзакции держит соединение с БД всё время вызова; под нагрузкой пул опустошается, и посторонние запросы отваливаются по таймауту.
- `@Transactional` на `private`-методе или на методе, вызванном через `this`, молча ничего не делает.
- Если перехватить исключение из внутреннего вызова с `REQUIRED` и продолжить, всё равно получишь `UnexpectedRollbackException` при commit.
- `@Transactional` на интерфейсе (только для JDK-прокси) или на default-методе `@Repository` ведёт себя иначе, чем аннотация на уровне класса; ставь её на конкретные классы сервисов.
- Ленивая загрузка после возврата из метода бросает `LazyInitializationException`; отображай в DTO внутри транзакции.
- Связанные заметки: [[jpa-locking]] — про `@Lock` внутри транзакций, [[spring-kafka-consumer]] — почему транзакционный слушатель всё равно даёт at-least-once.
