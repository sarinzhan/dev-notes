---
title: Движки workflow vs самописные саги — Temporal, Camunda, конечные автоматы
tags: [workflows, temporal, camunda, saga, architecture]
learned: 2026-10-27
---
## Кратко
Долгому бизнес-процессу (заказ eSIM: зарезервировать → оплатить → загрузить → ждать до 24 ч → активировать) нужны сохранённое состояние, retry, таймеры, компенсации и способ спросить «где заказ 42». Это можно построить самому (**конечный автомат** в Postgres, управляемый событиями Kafka и планировщиком), взять **BPMN-движок** (Camunda 7/8, Flowable), где процесс — это диаграмма, исполняемая движком, или **движок устойчивого выполнения** (durable execution: Temporal, а также та же модель в Cadence, Restate, DBOS), где процесс — обычный код, проигрываемый из истории. Самописный вариант выигрывает при 2–4 шагах без ожиданий; Temporal — когда в процессах есть таймеры, сигналы и компенсации; Camunda — когда бизнес-аналитики должны видеть и менять процесс.

## Основное

### Что должен давать любой вариант
- **Устойчивое состояние**: переживает перезапуск pod посреди процесса.
- **Retry с backoff** для каждого шага, с классами ошибок без повтора.
- **Таймеры**: «ждать 24 ч, пока абонент установит профиль», «напомнить через 1 ч».
- **Внешние сигналы**: webhook или событие Kafka, сообщающее «профиль установлен».
- **Компенсация** в обратном порядке, когда падает более поздний шаг ([[saga]]).
- **Наблюдаемость** (visibility): список застрявших процессов, какой шаг упал и почему, ручной retry или отмена.
- **Версионирование**: deploy изменённого процесса, пока выполняются тысячи старых прогонов.
- **Идемпотентность** каждого шага, потому что любой вариант повторно выполняет шаги после сбоя ([[idempotency]]).

### Три подхода
| | Самописный конечный автомат | BPMN-движок (Camunda 8, Flowable) | Устойчивое выполнение (Temporal) |
|---|---|---|---|
| Определение процесса | Колонка `status` + переходы через `switch`/enum, Kafka-консьюмеры, таймеры `@Scheduled` | XML-диаграмма BPMN, сервисные задачи, привязанные к воркерам | Обычный код на Java/Go/TS в методе workflow ([[temporal-concepts]]) |
| Хранение состояния | Твоя таблица в Postgres | БД движка (лог Zeebe в Camunda 8) | Сервер Temporal (Postgres/Cassandra) в виде истории событий |
| Retry/таймеры | Пишешь сам (outbox, планировщик, топики с задержкой) | Встроены, настраиваются на задачах | Встроены, `RetryOptions`, `Workflow.sleep` |
| Компенсация | Сам упорядочиваешь и сохраняешь | События компенсации в BPMN | Помощник `Saga`, код |
| Наблюдаемость | Своя админка / SQL | UI Operate / Cockpit, просмотр экземпляров процесса | Web UI, атрибуты поиска, история каждого прогона |
| Версионирование | Колонка `flow_version` + ветвления | Версии определения процесса; старые экземпляры доходят по старой диаграмме | `getVersion` / версионирование воркеров |
| Тестирование | Модульные тесты переходов | Тестовая обвязка движка, медленнее | `TestWorkflowEnvironment` с пропуском времени |
| Эксплуатационные затраты | Дополнительных нет | Кластер движка (брокеры Zeebe + Elasticsearch для Camunda 8) | Кластер Temporal (сервер + БД + хранилище visibility) |
| Кто читает процесс | Разработчики | Аналитики и разработчики | Разработчики |
| Подходит для | Коротких, линейных, с малым числом шагов | Регулируемых процессов или процессов, которыми владеют аналитики, задач для людей, согласований | Интеграций с большим объёмом кода, многих ветвлений, таймеров, сигналов |

### Когда прав самописный вариант
- Процесс из 2–4 шагов, каждый — одно событие, ожиданий дольше одного retry нет, и у команды уже есть [[outbox-pattern]] + идемпотентные консьюмеры. Таблица `provisioning_order(status, step_data, updated_at)` с `CHECK` на допустимые статусы, Kafka-консьюмер на каждый шаг и задача-«жнец» (reaper) для застрявших строк.
- Скрытые затраты проявляются позже: таблица таймеров, дашборд «застрявших заказов», логика backoff в трёх местах и вопрос «почему заказ 42 перешёл из PAID в FAILED», на который отвечают грепом по логам ([[logging-stacks]]). Каждая мелочь невелика; вместе они — движок workflow с худшими тестами.
- Spring StateMachine или самописный автомат на enum помогают с переходами, но сами по себе не дают ни устойчивости, ни таймеров, ни retry.

### Когда прав BPMN-движок
- Процесс *и есть* продукт (одобрение кредита, KYC, онбординг с ручной проверкой), изменения согласуются с не-разработчиками, а аудит хочет видеть ту диаграмму, которая выполнялась. Задачи для людей (формы, назначения, эскалации) — полноправная часть модели.
- Camunda 7 встраивается в Spring Boot (общая БД); Camunda 8 — это отдельный кластер Zeebe с gRPC-воркерами задач и коммерческой лицензией для продакшна начиная с 8.6. Flowable остаётся встраиваемым и open source.
- Цена: BPMN-диаграммы становятся кодом, который нельзя рефакторить в IDE, выражения (FEEL/JUEL) прячут логику, а обновление движка — это отдельный проект.

### Когда прав Temporal
- Процессы с настоящими ожиданиями (часы/дни), многими компенсациями, параллельными ветками, retry с разными политиками на шаг и необходимостью запрашивать живое состояние из UI. Процесс провижининга eSIM — хрестоматийный случай ([[esim-provisioning-platform]], [[design-esim-provisioning]]).
- Также хорош для «cron-задач, которые не должны выполниться дважды» и для оркестрации пакетной обработки (дочерний workflow на каждый элемент).
- Цена: дисциплина детерминизма, ещё один кластер (или Temporal Cloud) и соблазн положить в workflow бизнес-логику, которой место в доменных сервисах ([[use-cases-and-application-layer]]).

### Сочетание с Kafka
- События запускают workflow (`WorkflowClient.start` из листенера), activity публикуют события через outbox. Kafka — интеграционная шина, движок — оркестратор ([[event-driven-architecture]]).
- Не используй Kafka как состояние workflow: offset консьюмера — это не бизнес-состояние, а повторная обработка топика — не возобновление процесса.

## Пример
Минимальная самописная версия, чтобы увидеть, что заменяют движки: таблица статусов, исполнитель шагов, который за вызов продвигает один переход под блокировкой строки, и «жнец», который повторяет застрявшие заказы с backoff.

```java
@Service
@RequiredArgsConstructor
public class ProvisioningStateMachine {
    private final JdbcClient jdbc;
    private final ProfileClient profiles;
    private final SmdpGateway smdp;
    private final OutboxRepository outbox;

    enum Status { NEW, PROFILE_RESERVED, DOWNLOAD_ORDERED, WAITING_INSTALL, ACTIVE, FAILED }

    /** Продвигает на один шаг; безопасно вызывать конкурентно и повторно (блокировка строки + идемпотентные шаги). */
    @Transactional
    public void advance(UUID orderId) {
        var row = jdbc.sql("SELECT status, iccid, attempts FROM provisioning_order WHERE id = :id FOR UPDATE SKIP LOCKED")
                .param("id", orderId).query(OrderRow.class).optional().orElse(null);
        if (row == null) return;                                   // её держит другой воркер
        try {
            switch (Status.valueOf(row.status())) {
                case NEW -> {
                    String iccid = profiles.reserve(orderId);                      // идемпотентно по orderId
                    move(orderId, Status.PROFILE_RESERVED, iccid, Duration.ZERO);
                }
                case PROFILE_RESERVED -> {
                    smdp.downloadOrder(row.iccid(), orderId.toString());
                    outbox.save(OutboxRow.of("esim.notifications", orderId, new SendQrV1(orderId, row.iccid())));
                    move(orderId, Status.WAITING_INSTALL, row.iccid(), Duration.ofHours(24));   // таймер = next_check_at
                }
                case WAITING_INSTALL -> {                                        // сюда попадает только «жнец» через 24 ч
                    profiles.release(row.iccid());                               // компенсация
                    move(orderId, Status.FAILED, row.iccid(), Duration.ZERO);
                }
                default -> {}
            }
        } catch (TransientException e) {
            long backoffSec = Math.min(3600, (long) Math.pow(2, row.attempts()) * 30);
            jdbc.sql("UPDATE provisioning_order SET attempts = attempts + 1, next_check_at = now() + make_interval(secs => :s), last_error = :err WHERE id = :id")
                .param("s", backoffSec).param("err", e.getMessage()).param("id", orderId).update();
        }
    }

    @Scheduled(fixedDelay = 10_000)
    public void reaper() {                                                       // «служба таймеров»
        jdbc.sql("SELECT id FROM provisioning_order WHERE status NOT IN ('ACTIVE','FAILED') AND next_check_at <= now() LIMIT 100")
            .query(UUID.class).list().forEach(this::advance);
    }

    private void move(UUID id, Status to, String iccid, Duration nextCheck) {
        jdbc.sql("UPDATE provisioning_order SET status = :s, iccid = :i, attempts = 0, next_check_at = now() + :d WHERE id = :id")
            .param("s", to.name()).param("i", iccid).param("d", nextCheck).param("id", id).update();
    }
}
```

Сигнал «профиль установлен» — это обработчик webhook, который напрямую ставит `status = ACTIVE`. Сравни 60 строк выше (без версионирования, без истории, без UI) с workflow Temporal в [[temporal-concepts]], который покрывает всё это.

## Подводные камни
- Выбор Temporal для процесса из двух шагов: кластер, правила детерминизма и новая ментальная модель там, где справились бы outbox и один консьюмер.
- Выбор самописного автомата для процесса с ожиданием людей и пятью компенсациями: «временная» таблица таймеров и админка переживут проект.
- Бизнес-логика в оркестраторе (в любом из трёх): workflow решает про цены и право на услугу вместо вызова доменных сервисов; он становится нетестируемым и неверсионируемым.
- Неидемпотентные шаги: любой движок повторно выполняет шаг после сбоя между «сделано» и «записано» (см. обсуждение поворотного шага — pivot — в [[saga]]).
- Связанные заметки: [[temporal-concepts]], [[saga]], [[background-jobs-and-schedulers]], [[outbox-pattern]], [[design-esim-provisioning]].
