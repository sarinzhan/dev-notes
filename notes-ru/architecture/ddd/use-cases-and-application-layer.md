---
title: Сценарии использования и прикладной слой
tags: [ddd, architecture, spring, cqrs]
learned: 2026-10-27
---
## Кратко
**Прикладной слой** (application layer) — тонкий слой между транспортом (контроллер, Kafka listener, планировщик) и доменом. Каждый **сценарий использования** (use case: прикладной сервис, обработчик команды) занимается только оркестрацией: открыть транзакцию, загрузить агрегат через репозиторий, вызвать один доменный метод, сохранить, опубликовать события, отобразить результат. Бизнес-правила живут в домене ([[aggregates-and-invariants]]); HTTP и JSON живут в адаптерах ([[hexagonal-architecture]]). Разделение сценариев на **команды** (commands: меняют состояние) и **запросы** (queries: читают, без побочных эффектов) делает их маленькими и становится первым шагом к [[cqrs]].

## Основное

### Что где находится
| Слой | Знает о | Типичный код |
|---|---|---|
| Транспорт / адаптер | HTTP, Kafka, JSON, принципал аутентификации | `@RestController`, `@KafkaListener`, валидация request DTO ([[spring-mvc-validation-and-uploads]]) |
| Прикладной | Сценарии, транзакции, порты, события | `ActivateProfileHandler`, `@Transactional`, `ApplicationEventPublisher` |
| Доменный | Бизнес-правила, инварианты, объекты-значения | `Sim.activate()`, `Money`, доменные исключения |
| Инфраструктурный | База данных, внешние API, часы | JPA-репозитории, шлюзы на `RestClient`, outbox relay |
- Проверка слоёв: прикладной сервис должен компилироваться, если удалить контроллер и JPA-классы.

### Оркестрация и доменная логика
- Оркестрация: «загрузить, вызвать, сохранить, опубликовать, отобразить». Если в прикладном сервисе есть `if (sim.status() == ACTIVE && profile.isReleased())`, этот `if` — правило, которому место в `Sim`.
- Координация нескольких агрегатов — законная прикладная логика: загрузить `Order`, спросить у `PricingPolicy` (доменного сервиса) цену, вызвать `order.applyPrice(...)`. **Доменные сервисы** (domain services) содержат правила, которые не помещаются в один агрегат (конвертация валют, распределение по SIM); они без состояния и принимают доменные объекты, никогда не DTO.
- Прикладной слой владеет **границей транзакции**: один сценарий = одна транзакция = один агрегат ([[spring-transactional]]). Длинные процессы, охватывающие несколько транзакций, становятся [[saga]] или Temporal workflow ([[temporal-concepts]]), что само по себе задача прикладного слоя.

### Команды и запросы
- **Команда**: неизменяемый объект, называющий намерение, `ActivateProfile(simId, iccid, requestedBy)`. Один обработчик, возвращает ничего или id. Синтаксически валидируется на краю (`@Valid`), семантически — в домене.
- **Запрос**: `GetSimStatus(simId)`; обработчик читает напрямую по пути, оптимизированному для чтения (проекция, `JdbcClient`, реплика для чтения), и возвращает DTO. Никакой загрузки агрегата, никакого `@Transactional`, кроме `readOnly = true`.
- Зачем разделять: `SimService` с 30 методами превращается в тридцать классов с одним методом, каждый из которых зависит только от того, что использует ([[solid|ISP, SRP]]); для теста одного сценария нужен один фейковый порт ([[testing-types-and-dev-cycles]]).
- Именование: обработчики называют по сценарию (`SuspendSimHandler`), а не по сущности (`SimService`).

### Сквозная функциональность
- Проверка ключа идемпотентности, авторизация («может ли этот пользователь приостановить эту SIM») и аудит оборачивают обработчик: декоратор, аспект ([[spring-aop]]) или небольшой `CommandBus`, который выполняет middleware перед `handle()`.
- Логирование и трассировка: имя обработчика — естественное имя span ([[distributed-tracing]]).
- Отображение результата в модель API происходит в адаптере, поэтому один и тот же обработчик обслуживает REST, gRPC и топик команд Kafka ([[bff-and-api-composition]]).

### Где живут DTO и мапперы
- Request DTO (адаптер) → команда (прикладной слой) → доменные типы. Для CRUD три формы кажутся избыточными, но именно они позволяют менять API, не трогая домен. Для тривиальных endpoint-ов на чтение средний шаг можно пропустить: контроллер → обработчик запроса → DTO.
- Маппинг — скучный код: MapStruct или написанные вручную статические методы, никогда не глубокое копирование доменных объектов через рефлексию.

## Пример
Обработчики команды и запроса для одного агрегата; контроллер — тонкий адаптер.

```java
public record ActivateProfile(UUID simId, Iccid iccid, String requestedBy) {}

@Service
@RequiredArgsConstructor
public class ActivateProfileHandler {
    private final SimRepository sims;              // порт, реализован через JPA в инфраструктуре
    private final ApplicationEventPublisher events;

    @Transactional
    public void handle(ActivateProfile cmd) {
        Sim sim = sims.findById(cmd.simId()).orElseThrow(() -> new SimNotFoundException(cmd.simId()));
        sim.activate(cmd.iccid());                 // правило живёт здесь, а не в обработчике
        sims.save(sim);
        sim.pullEvents().forEach(events::publishEvent);
    }
}

public record SimStatusView(UUID simId, String msisdn, String activeIccid, Instant updatedAt) {}

@Service
@RequiredArgsConstructor
public class GetSimStatusHandler {
    private final JdbcClient jdbc;                 // сторона чтения: без агрегата, без ленивой загрузки

    @Transactional(readOnly = true)
    public SimStatusView handle(UUID simId) {
        return jdbc.sql("""
                SELECT s.id, s.msisdn, p.iccid, s.updated_at
                FROM sim s LEFT JOIN sim_profile p ON p.sim_id = s.id AND p.state = 'ACTIVE'
                WHERE s.id = :id""")
            .param("id", simId)
            .query(SimStatusView.class).single();
    }
}

@RestController
@RequestMapping("/sims")
@RequiredArgsConstructor
class SimController {
    private final ActivateProfileHandler activate;
    private final GetSimStatusHandler status;

    @PostMapping("/{id}/activate")
    @ResponseStatus(HttpStatus.ACCEPTED)
    void activate(@PathVariable UUID id, @Valid @RequestBody ActivateRequest body, Principal user) {
        activate.handle(new ActivateProfile(id, new Iccid(body.iccid()), user.getName()));
    }

    @GetMapping("/{id}")
    SimStatusView get(@PathVariable UUID id) { return status.handle(id); }
}
```

## Подводные камни
- `@Transactional` на контроллере или на доменном объекте: граница транзакции должна совпадать со сценарием, иначе соединения удерживаются во время сериализации JSON или один сценарий разбивается на несколько транзакций.
- Прикладные сервисы, вызывающие друг друга: `OrderHandler`, вызывающий `PaymentHandler`, вкладывает транзакции и скрывает процесс; лучше породить доменное событие или сделать явную сагу.
- Возврат JPA-сущностей из обработчиков в контроллеры: ленивая загрузка в сериализаторе, `LazyInitializationException`, и сущность становится API ([[jpa-persistence-context-and-entity-lifecycle]]).
- `GenericCrudService<T>` в роли прикладного слоя: в нём нет сценариев, поэтому правила оказываются в контроллерах.
- Связанные заметки: [[clean-architecture]] (сценарии как внутреннее кольцо), [[layered-architecture]] (что этот подход заменяет), [[cqrs]], [[ddd-basics]].
