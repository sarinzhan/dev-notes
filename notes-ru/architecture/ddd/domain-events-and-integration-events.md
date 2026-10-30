---
title: Доменные и интеграционные события
tags: [ddd, events, spring, kafka, outbox]
learned: 2026-10-27
---
## Кратко
**Доменное событие** (domain event) — это факт, произошедший внутри одного ограниченного контекста, названный в прошедшем времени (`ProfileActivated`), порождённый агрегатом и обработанный в том же процессе. **Интеграционное событие** (integration event) — версия этого факта, которая покидает контекст: публикуется в Kafka со стабильной схемой для других сервисов. Доменные события — инструмент проектирования в памяти (развязка агрегатов, побочные эффекты после commit); интеграционные события — это контракт, поэтому они идут через [[outbox-pattern]] и версионируются как API ([[message-schema-evolution]]).

## Какую проблему решает
- **Без этого:** `ProfileService.activate()` заодно отправляет SMS, обновляет read-модель биллинга и напрямую пишет в Kafka. Каждая новая реакция правит тот же метод, после rollback SMS всё равно отправлена, а другие команды потребляют твою JPA-сущность как JSON, и переименование колонки их ломает.
- **Что меняет:** агрегат только фиксирует факт; обработчики реагируют на него после commit, а отдельное версионируемое интеграционное событие, опубликованное через outbox, — единственный контракт, который видят другие сервисы.
- **Когда не нужно:** одно действие с одним побочным эффектом или контекст, который никто снаружи не потребляет; прямой вызов метода понятнее.

## Основное

### Сравнение двух видов
| | Доменное событие | Интеграционное событие |
|---|---|---|
| Область | Внутри ограниченного контекста | Между контекстами / сервисами |
| Транспорт | `ApplicationEventPublisher`, список в памяти агрегата | Kafka, RabbitMQ, webhooks |
| Форма | Богатые доменные типы (`Iccid`, `Money`) | Плоское, сериализуемое, версионированное (JSON/Avro/Protobuf), только то, что нужно потребителям |
| Кто связан | Обработчики в той же кодовой базе | Неизвестные потребители, возможно, другие команды |
| Доставка | В той же транзакции или сразу после commit | At-least-once, асинхронно, может прийти поздно или дважды |
| Имя | `OrderConfirmed` | `esim.order.confirmed.v1` |
- Одно доменное событие часто порождает одно интеграционное, но не всегда: внутренние события (`LineAdded`) остаются внутри, а некоторые интеграционные события объединяют несколько доменных фактов.

### Порождение и обработка доменных событий в Spring
- Агрегат записывает события (`events.add(...)`), прикладной сервис публикует их после `save()` через `ApplicationEventPublisher.publishEvent(e)` ([[aggregates-and-invariants]]). `@DomainEvents` / `@AfterDomainEventPublication` из Spring Data на агрегате автоматизируют это при `repository.save()` ([[spring-data-repositories]]).
- `@EventListener`: выполняется синхронно внутри транзакции публикующего кода. Подходит для реакций внутри контекста, которые должны закоммититься вместе (обновить модель чтения в той же БД).
- `@TransactionalEventListener(phase = AFTER_COMMIT)` (фаза по умолчанию): выполняется, только если транзакция закоммитилась. Правильное место для побочных эффектов, которые не должны происходить при откате: отправить письмо, записать строку outbox... вот только строка outbox должна быть в *той же* транзакции, поэтому для неё нужен `BEFORE_COMMIT` или запись в сервисе. Слушатели после commit выполняются без транзакции, если не добавить `@Transactional(propagation = REQUIRES_NEW)` ([[spring-transactional]]).
- `@Async` на слушателе переносит его в другой поток: нет транзакции, нет MDC/контекста трассировки, если их не передать ([[distributed-tracing]]), а исключения поглощаются executor-ом. Нормально для уведомлений, неправильно для всего, что нельзя потерять.
- Порядок между слушателями: `@Order`; если порядок очень важен, дизайну, вероятно, нужен один обработчик.

### Превращение доменного события в интеграционное
1. Слушатель `BEFORE_COMMIT` (или прикладной сервис) отображает `ProfileActivated` в payload `ProfileActivatedV1` и вставляет его в outbox в той же транзакции.
2. Relay публикует его в Kafka с ключом по id агрегата.
3. Потребители в других контекстах переводят его в *свою* модель через антикоррупционный слой ([[bounded-contexts-and-context-mapping]]) и обрабатывают идемпотентно по id события ([[idempotency]]).
- Payload — это контракт: включать `eventId`, `occurredAt`, `aggregateId`, `version` и факты, но никогда не всю JPA-сущность.

### Перенос состояния в событии и уведомление
- **Уведомление** (notification): `{"simId": "..."}`; потребители запрашивают детали обратно. Маленькое, всегда свежее, но создаёт синхронную зависимость.
- **Перенос состояния в событии** (event-carried state transfer): событие содержит то, что нужно потребителям (`msisdn`, `iccid`, `activatedAt`). Потребители остаются независимыми; производитель должен решить, что публично. Для интеграционных событий предпочтителен этот вариант ([[event-driven-architecture]]).

### Команды и события
- Команда — это просьба что-то сделать (`ActivateProfile`), адресованная одному обработчику, её могут отклонить. Событие — это факт, рассылается всем, отклонить его нельзя. Топики Kafka, несущие команды (`esim.activation.requested`), допустимы, но их нужно честно так называть и ожидать одного потребителя ([[cqrs]]).

## Пример
Доменное событие, порождённое агрегатом, слушатель в процессе, который пишет строку outbox до commit, и слушатель после commit для побочного эффекта.

```java
public record ProfileActivated(UUID simId, Iccid iccid, Instant occurredAt) implements DomainEvent {}

@Service
@RequiredArgsConstructor
public class ActivateProfileHandler {
    private final SimRepository sims;
    private final ApplicationEventPublisher events;

    @Transactional
    public void handle(ActivateProfile cmd) {
        Sim sim = sims.findById(cmd.simId()).orElseThrow();
        sim.activate(cmd.iccid());
        sims.save(sim);
        sim.pullEvents().forEach(events::publishEvent);   // всё ещё внутри транзакции
    }
}

@Component
@RequiredArgsConstructor
class ProfileActivatedListeners {
    private final OutboxRepository outbox;
    private final SmsGateway sms;

    // та же транзакция: интеграционное событие существует тогда и только тогда, когда активация закоммичена
    @TransactionalEventListener(phase = TransactionPhase.BEFORE_COMMIT)
    public void toOutbox(ProfileActivated e) {
        var payload = new ProfileActivatedV1(e.simId(), e.iccid().value(), e.occurredAt());
        outbox.save(OutboxRow.of(UUID.randomUUID(), "esim.profile.activated", e.simId().toString(), payload, 1));
    }

    // после commit: потерянное SMS допустимо, фантомное SMS при откате — нет
    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    @Async
    public void notifySubscriber(ProfileActivated e) {
        sms.send(e.simId(), "Your eSIM profile is active");
    }
}
```

## Подводные камни
- Публикация в Kafka из слушателя `AFTER_COMMIT`: commit уже сделан, отправка может упасть, никто не делает retry. Это та самая двойная запись (dual write), которую outbox и призван устранить.
- `@TransactionalEventListener` на методе, вызванном без активной транзакции: событие молча теряется (если не указать `fallbackExecution = true`).
- JPA-сущности внутри события: ленивые коллекции взрываются в асинхронном слушателе после закрытия сессии ([[jpa-persistence-context-and-entity-lifecycle]]).
- События, названные в повелительном наклонении (`ActivateProfile`), — это замаскированные команды; если обработчик может «отклонить» событие, в дизайне проблема.
- Связанные заметки: [[outbox-pattern]], [[event-driven-architecture]], [[event-sourcing]] (события как источник истины, а не просто уведомления), [[spring-kafka-consumer]].
