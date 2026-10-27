---
title: Неизменяемость и побочные эффекты
tags: [programming, immutability, side-effects, pure-functions, concurrency]
learned: 2026-10-27
---
## Кратко
Неизменяемое значение нельзя изменить после создания; чистая функция возвращает результат, который зависит только от её аргументов, и больше ничего не меняет. Вместе они убирают целые классы ошибок: никаких сюрпризов с алиасингом, никаких «кто это поменял?», и потокобезопасность бесплатно, потому что не за что гоняться. Практическое правило для бэкенда: держи данные (DTO, события, команды, конфигурацию, доменные значения) неизменяемыми, держи побочные эффекты (записи в базу, HTTP-вызовы, публикацию, логирование, часы, случайные числа) на краях use case, а середину делай чистыми функциями над неизменяемыми входами. Рецепт для Java (final-поля, защитные копии, records, `List.copyOf`) — в [[immutable-objects]].

## Основное

### Определения
- **Побочный эффект** (side effect): любое наблюдаемое изменение помимо возвращаемого значения: изменение аргумента или поля, ввод-вывод, запись в лог, чтение часов, исключение для управления потоком.
- **Чистая функция** (pure function): детерминированная и без побочных эффектов. `Math.max`, `String.toUpperCase`, `LocalDate.plusDays`, твой калькулятор цен. Не чистые: `LocalDate.now()`, `random.nextInt()`, `repo.save`, всё, что логирует.
- **Ссылочная прозрачность** (referential transparency): вызов можно заменить его результатом без изменения программы; именно это делает безопасными кэширование, мемоизацию ([[dynamic-programming]]) и параллельное вычисление.
- **Неизменяемый** (immutable) против **фактически неизменяемого** (effectively immutable; никогда не меняется после публикации) против **неизменяемого представления** (unmodifiable view; `Collections.unmodifiableList` над списком, который владелец всё ещё меняет).

### Что даёт неизменяемость
| Проблема | С изменяемым состоянием | С неизменяемыми значениями |
|---|---|---|
| Потокобезопасность | блокировки, volatile, рассуждения о happens-before ([[java-memory-model]]) | безопасно разделять; `final`-поля безопасно публикуются по JMM |
| Алиасинг | метод меняет список, который ты передал | вызывающие сохраняют свои данные |
| Ключи хеша | ключ меняется, запись потеряна ([[hash-tables]]) | стабильный `hashCode` |
| Кэширование | устаревшие копии, инвалидация | кэшируй само значение |
| Отладка | «кто выставил здесь null?» | состояние меняется только созданием новых значений; история явная |
| Тестирование | подготовка скрытого состояния, тесты зависят от порядка | вызвать функцию, проверить результат |
| Отмена / аудит / воспроизведение | разницу трудно восстановить | хранить старые версии ([[event-sourcing]]) |

### Цена
- Выделение памяти: `withX(...)` создаёт новый объект на каждое изменение. В Java это дёшево (bump-выделение в TLAB, GC молодого поколения, [[garbage-collection]]); это важно только в плотных циклах или для больших коллекций, где помогает builder или персистентная коллекция (structural sharing, Vavr/PCollections).
- Многословность до появления records; records и Lombok `@Value`/`@With` решают большую часть проблемы.
- Глубокая неизменяемость требует дисциплины: record с полем `List` неизменяем ровно настолько, насколько неизменяем этот список (`List.copyOf` в компактном конструкторе).

### Где должны жить побочные эффекты
- **Функциональное ядро, императивная оболочка** (functional core, imperative shell): разобрать и провалидировать вход → чистое решение (сколько списать, в какое состояние перейти, какие события выпустить) → эффекты (сохранить, опубликовать, вызвать). Ядро тестируется юнит-тестами без моков; оболочка тонкая и покрывается интеграционными тестами ([[use-cases-and-application-layer]], [[hexagonal-architecture]]).
- Внедряй часы и случайность (`Clock`, `Supplier<UUID>`), чтобы ядро оставалось детерминированным, а тесты могли управлять временем ([[testing-async-and-kafka-flows]]).
- Идемпотентные эффекты: если побочный эффект должен произойти хотя бы один раз, сделай его безопасным для повторения ([[idempotency]]); activity в Temporal — это ровно «побочные эффекты, изолированные от детерминированного кода workflow» ([[temporal-concepts]]).
- События и сообщения по определению — неизменяемые факты; никогда не меняй полученную запись Kafka или доменное событие ([[domain-events-and-integration-events]]).

### Изменяемость там, где ей место
- Локальные builder'ы и аккумуляторы внутри одного метода (`StringBuilder`, локальный `ArrayList`) — это нормально: они не разделяются и не утекают.
- Сущности с идентичностью и жизненным циклом (JPA-сущности, агрегаты) изменяемы по своей природе; держи изменения внутри методов агрегата и защищай инварианты ([[aggregates-and-invariants]], [[jpa-persistence-context-and-entity-lifecycle]]).
- Буферы, критичные к производительности, кольцевые буферы, `ByteBuffer` ([[stacks-and-queues]], [[java-nio]]).

### Поддержка в языках
- Java: `final`, records (16), `List.of`/`Map.of`/`copyOf` (9–10), `String`, `java.time`, `BigDecimal`; нет `const` для глубокой неизменяемости; см. [[immutable-objects]].
- Kotlin: `val`, data-классы с `copy`, интерфейс `List` только для чтения. Rust: всё неизменяемо по умолчанию, изменяемость явная и с единственным владельцем. Go: неизменяемости нет, только соглашения.

## Пример
Функциональное ядро (чистое решение о тарификации над неизменяемыми входами) и императивная оболочка (эффекты), время внедряется.

```java
record Subscriber(String msisdn, Plan plan, long balanceMinor, Instant activatedAt) {
    Subscriber withBalance(long newBalanceMinor) {
        return new Subscriber(msisdn, plan, newBalanceMinor, activatedAt);
    }
}

sealed interface ChargeDecision permits Charged, Declined {}
record Charged(Subscriber updated, long amountMinor) implements ChargeDecision {}
record Declined(String reason) implements ChargeDecision {}

/** Чистая: те же входы — тот же выход; без I/O, без часов, без логирования. */
static ChargeDecision decideCharge(Subscriber s, long amountMinor, Instant now) {
    if (now.isBefore(s.activatedAt().plus(Duration.ofDays(1))) && s.plan().freeFirstDay())
        return new Charged(s, 0);
    if (s.balanceMinor() < amountMinor)
        return new Declined("insufficient balance");
    return new Charged(s.withBalance(s.balanceMinor() - amountMinor), amountMinor);
}

/** Императивная оболочка: загружает, решает, применяет эффекты. */
@Service
@RequiredArgsConstructor
class ChargeService {
    private final SubscriberRepository repo;
    private final KafkaTemplate<String, ChargeEvent> kafka;
    private final Clock clock;                         // внедряется, чтобы тесты управляли временем

    @Transactional
    public ChargeDecision charge(String msisdn, long amountMinor) {
        Subscriber s = repo.findByMsisdn(msisdn).orElseThrow();
        ChargeDecision d = decideCharge(s, amountMinor, clock.instant());
        if (d instanceof Charged c) {
            repo.save(c.updated());                     // эффект 1
            kafka.send("billing.charged", msisdn, new ChargeEvent(msisdn, c.amountMinor())); // эффект 2 (в реальном коде — outbox)
        }
        return d;
    }
}
```

Для тестирования `decideCharge` не нужны ни Spring, ни моки, ни база данных; оболочка один раз покрывается интеграционным тестом.

## Подводные камни
- Record или `final`-класс, который хранит изменяемый `List`/`Date`/массив, не является неизменяемым; копируй на входе и возвращай неизменяемые представления ([[immutable-objects]]).
- `Collections.unmodifiableList(list)` — это представление: владелец всё ещё может менять `list`, и читатели увидят изменение; `List.copyOf` делает настоящую копию.
- Скрытые побочные эффекты в геттерах, `equals`, `hashCode` или `compareTo` (ленивая загрузка JPA-связи внутри `equals`, логирование внутри `hashCode`) приводят к N+1 запросам и недетерминированным коллекциям ([[jpa-n-plus-one]], [[equals-and-hashcode]]).
- Изменение общего объекта внутри параллельного стрима или цепочки `CompletableFuture` — это гонка данных, даже если каждый шаг «выглядит» последовательным ([[race-conditions-and-critical-sections]]).
- Связанные заметки: [[immutable-objects]] — рецепт для Java, [[java-memory-model]] — почему `final`-поля публикуются безопасно, [[programming-paradigms]] — функциональный взгляд.
