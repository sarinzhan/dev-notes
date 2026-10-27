---
title: Проектирование системы выпуска eSIM
tags: [system-design, case-study, temporal, orchestration, telecom]
learned: 2026-10-27
---
## Кратко
Выпуск (provisioning) eSIM превращает «абонент нажал „Купить eSIM“» в профиль, загруженный на телефон, и номер, активный в сети: зарезервировать профиль на SM-DP+, создать абонента в биллинге и HLR/HSS, выдать клиенту код активации, дождаться (возможно, часами) подтверждения загрузки и затем активировать. Это долгая многошаговая оркестрация внешних систем, которые падают по таймауту, отвечают с опозданием или молча выполняют операцию, и каждый шаг нужно повторить или компенсировать. Архитектура: тонкий REST API, по одному Temporal workflow на заказ, который держит конечный автомат, идемпотентные activity с ключами, выведенными из заказа, outbox, питающий Kafka для всех остальных, и запрос статуса для поддержки. Это обобщённая версия [[esim-provisioning-platform]]; механика Temporal — в [[temporal-concepts]].

## Основное

### Требования и оценки
- Функциональные: заказать eSIM (новый номер или замена физической SIM), получить QR/код активации, опрашивать статус, отменить; уведомлять о каждом шаге; экран оператора с зависшими заказами; массовые заказы для бизнес-клиентов.
- Нефункциональные: никакого двойного резервирования профиля или двойного списания; заказ должен завершиться или откатиться даже после deploy или падения worker-а; видимое состояние для поддержки; у внешних систем разные SLA (SM-DP+ 200 мс–30 с, HLR — секунды, биллинг может занимать минуты в пакетных окнах).
- Числа: 5 000 активаций в день ≈ 0.06/с в среднем, в пиках кампаний — несколько в секунду; у каждого заказа 6–8 шагов, живёт он от минут до дней (ждём, пока абонент отсканирует код). Пропускная способность крошечная; проблема в длительности и надёжности, а не в QPS.

### API
```
POST /v1/esim/orders       Idempotency-Key   { msisdn | newNumber: true, tariffId, deviceEid? }
  -> 202 { orderId, status: CREATED }
GET  /v1/esim/orders/{id}  -> { status, step, activationCode?, lastError?, updatedAt }
POST /v1/esim/orders/{id}/cancel
POST /v1/callbacks/smdp    (SM-DP+ notifications: profile DOWNLOADED / INSTALLED / ENABLED)
```
- Статус берётся из Temporal query к работающему workflow (живой) или из строки заказа после завершения; клиент опрашивает `GET` или получает push ([[design-notification-service]]).

### Модель данных
- `esim_order(id, msisdn, subscriber_id, tariff_id, status, iccid, matching_id, workflow_id, created_at, updated_at)` — модель чтения для списков и экранов поддержки; авторитетное состояние незавершённых заказов держит workflow.
- `profile(iccid, eid?, state: AVAILABLE|RESERVED|DOWNLOADED|ACTIVE|RELEASED, order_id, reserved_at)` — зеркало пула SM-DP+ для локальных проверок и поиска утечек.
- `step_log(order_id, step, attempt, result, error, at)` — только добавление (append-only), для поддержки и сверки.
- `outbox` для `EsimOrderCreated`, `EsimActivated`, `EsimFailed` ([[outbox-pattern]]).

### Общая архитектура
```
app / web ──▶ API (Spring) ──tx: order row + outbox──▶ Postgres ──relay──▶ Kafka esim.events
                 │ start workflow (workflowId = orderId)                       └─▶ notifications, CRM, analytics
                 ▼
           Temporal server ◀──poll──▶ workers (activities)
                                        ├─▶ SM-DP+ (reserve profile, confirm order)   idem key orderId:reserve
                                        ├─▶ Billing (create subscriber, assign tariff)  idem key orderId:billing
                                        ├─▶ HLR/HSS (provision IMSI/MSISDN)             idem key orderId:hlr
                                        └─▶ Notify (activation code, done)
SM-DP+ ──callback DOWNLOADED/ENABLED──▶ API ──signal──▶ workflow (waiting up to 72 h) ──▶ activate ──▶ done
```

### Подробно: оркестрация, которая переживёт всё
- **Workflow на заказ**, `workflowId = orderId` с политикой отклонения дубликатов: запустить один и тот же заказ дважды невозможно — это первый уровень идемпотентности. Код workflow и есть конечный автомат: reserve → billing → HLR → отправка кода → ожидание сигнала о загрузке (таймер 72 ч) → activate → notify.
- **Activity идемпотентны по построению**: каждый внешний вызов несёт `orderId:step`; адаптер сначала запрашивает внешнюю систему по этому ключу и только потом действует. Повторный `reserveProfile` после таймаута SM-DP+ находит уже зарезервированный профиль, а не резервирует второй ([[idempotency]]).
- **Таймауты и retry для каждой activity**: `startToClose` 30 с для SM-DP+ с 5 повторами и экспоненциальной задержкой (exponential backoff); для биллинга — более длинный `scheduleToClose` из-за пакетных окон; неповторяемые ошибки (`NoProfilesAvailable`, `InvalidTariff`) сразу приводят к отказу.
- **Компенсация**: workflow регистрирует отмену для каждого завершённого шага (`releaseProfile`, `deleteSubscriber`, `deprovisionHlr`) и при окончательном сбое выполняет их в обратном порядке — это персистентная оркестрируемая [[saga]]. Компенсации тоже являются activity, поэтому повторяются.
- **Ожидание человека**: абонент может отсканировать QR завтра. Workflow блокируется на `Workflow.await(() -> downloaded || cancelled)` с таймером 72 ч; ни потока, ни опроса БД. Callback от SM-DP+ приходит как signal; отмена — другой signal. Если таймер срабатывает, профиль освобождается, а заказ истекает ([[background-jobs-and-schedulers]]).
- **Детерминизм и версионирование**: внутри workflow никаких `now()`, случайных значений и ввода-вывода; `Workflow.getVersion`, когда шаги меняются, пока заказы ещё в работе.
- **Наблюдаемость**: `@QueryMethod status()` возвращает текущий шаг и последнюю ошибку; один trace id от приложения до activity ([[distributed-tracing]], [[observability-three-pillars]]).
- **Сверка (reconciliation)**: ночная задача сравнивает состояния `profile` с инвентарём SM-DP+ и освобождает профили в `RESERVED` старше 72 ч. Оркестрация уменьшает расхождения; сверка ловит остальное.

### Компромиссы
| Решение | Выбрано | Альтернатива | Почему |
|---|---|---|---|
| Оркестратор | Temporal workflow | хореография на Kafka с таблицей состояний | таймеры, retry, история и «где застряло» достаются бесплатно; хореография размазывает конечный автомат по consumer-ам ([[workflow-engines-vs-sagas]]) |
| Источник истины для незавершённых заказов | состояние workflow, проецируемое в `esim_order` | колонка статуса в БД, которую обновляет каждый шаг | одно место для переходов; строка в БД — для запросов ([[read-write-splitting-and-cqrs-lite]]) |
| Ожидание загрузки | signal + таймер 72 ч | опрос SM-DP+ каждую минуту | нулевая нагрузка во время ожидания; callback-и всё равно приходят push-ем |
| Массовые заказы | дочерний workflow на каждую SIM | один гигантский workflow | ограниченный размер истории, retry и компенсация для каждой SIM |
| Масштабирование worker-ов | больше worker-ов на task queue; отдельная очередь для activity SM-DP+ | одна очередь | изолировать медленную внешнюю зависимость ([[horizontal-vs-vertical-scaling]]) |

## Пример
Каркас workflow с компенсацией и ожиданием сигнала о загрузке:

```java
public class EsimOrderWorkflowImpl implements EsimOrderWorkflow {
    private final ProvisioningActivities acts = Workflow.newActivityStub(ProvisioningActivities.class,
        ActivityOptions.newBuilder()
            .setStartToCloseTimeout(Duration.ofSeconds(30))
            .setRetryOptions(RetryOptions.newBuilder()
                .setMaximumAttempts(5).setBackoffCoefficient(2.0)
                .setDoNotRetry("NoProfilesAvailable", "InvalidTariff").build())
            .build());
    private String step = "CREATED";
    private boolean downloaded, cancelled;

    @Override
    public void run(EsimOrder order) {
        Saga saga = new Saga(new Saga.Options.Builder().setParallelCompensation(false).build());
        try {
            step = "RESERVE";  Profile p = acts.reserveProfile(order.id());      // ключ идемпотентности orderId:reserve
            saga.addCompensation(acts::releaseProfile, order.id(), p.iccid());
            step = "BILLING";  acts.createSubscriber(order.id(), p.iccid());
            saga.addCompensation(acts::deleteSubscriber, order.id());
            step = "HLR";      acts.provisionHlr(order.id(), p.iccid());
            saga.addCompensation(acts::deprovisionHlr, order.id());
            step = "WAIT_DOWNLOAD"; acts.sendActivationCode(order.id(), p.matchingId());
            boolean ok = Workflow.await(Duration.ofHours(72), () -> downloaded || cancelled);
            if (!ok || cancelled) throw ApplicationFailure.newNonRetryableFailure("not downloaded", "Expired");
            step = "ACTIVATE"; acts.activate(order.id(), p.iccid());
            step = "DONE";     acts.notifyDone(order.id());
        } catch (ActivityFailure | ApplicationFailure e) {
            step = "COMPENSATING"; saga.compensate(); step = "FAILED"; throw e;
        }
    }
    @Override public void profileDownloaded() { downloaded = true; }   // @SignalMethod
    @Override public void cancel()            { cancelled = true; }    // @SignalMethod
    @Override public String status()          { return step; }         // @QueryMethod
}
```

## Подводные камни
- На собеседовании спрашивают: «SM-DP+ упал по таймауту после резервирования — что дальше?» Перед повторным резервированием запросить по ключу идемпотентности; компенсация, если более поздний шаг окончательно провалился. «Worker умер посреди заказа?» Другой worker воспроизводит историю (replay) и продолжает. «Deploy, когда 300 заказов ждут загрузки?» Ветвление через `getVersion`; старые запуски доходят до конца по старому пути.
- «Почему не просто колонка статуса и повтор по cron?» Это работает, пока не понадобятся таймеры, компенсации и наблюдаемость; тогда ты переписал половину Temporal ([[workflow-engines-vs-sagas]]).
- `LocalDateTime.now()` или случайный id внутри workflow ломают replay после следующего deploy; в истории проекта остался этот шрам.
- Activity, которые отдельно пишут в БД и публикуют в Kafka, возвращают проблему двойной записи (dual write); используй outbox внутри транзакции activity.
- Связанные заметки: [[esim-provisioning-platform]], [[temporal-concepts]], [[saga]], [[async-processing-and-event-driven-scaling]], [[design-payment-processing]] — та же проблема «неизвестного исхода», но с деньгами.
