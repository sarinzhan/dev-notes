---
title: Основы Temporal — workflow, activity, replay, Spring Boot
tags: [temporal, workflows, distributed-systems, spring-boot, java]
learned: 2026-10-27
---
## Кратко
Temporal — это движок устойчивого выполнения (durable execution): долгий бизнес-процесс пишется как обычный Java-код (**workflow**), шаги с побочными эффектами — как **activity**, а сервер Temporal сохраняет каждое событие (activity запланирована, завершена, сработал таймер) в **историю событий** (event history). Если воркер умирает, другой воркер **проигрывает заново** (replay) историю, и код workflow продолжается с того места, где остановился, — именно поэтому код workflow обязан быть **детерминированным**. Таймеры, retry, сигналы, запросы и компенсации встроены, что делает Temporal персистентным оркестратором [[saga]].

## Какую проблему решает
- **Без этого:** заказ eSIM — это резерв профиля → заказ загрузки на SM-DP+ → ожидание до 24 ч, пока абонент установит профиль → активация в сети. Если pod перезапустится во время 24-часового ожидания или SM-DP+ ответит таймаутом на третьей попытке, текущий шаг, счётчик попыток и таймер уже должны быть где-то сохранены, а код должен знать, какие компенсации запускать. Вручную это таблица статусов, планировщик и код backoff вокруг каждого вызова.
- **Что меняет:** процесс — это один Java-метод; сервер записывает каждый шаг в историю событий, поэтому после сбоя другой воркер проигрывает её и продолжает. Retry, таймеры, сигналы и компенсации встроены.
- **Когда не нужно:** короткие линейные процессы из двух-трёх шагов без долгих ожиданий, где хватает Kafka + outbox и идемпотентных консьюмеров, или когда команда не может эксплуатировать ещё один кластер с состоянием.

## Основное

### Workflow vs activity
- **Workflow**: логика оркестрации (`@WorkflowInterface` + `@WorkflowMethod`). Должен быть детерминированным; может выполняться месяцами. Получает входные данные, вызывает activity через стабы, спит, ждёт сигналов.
- **Activity**: одна единица реальной работы с побочными эффектами (HTTP к SM-DP+, запись в БД, публикация в Kafka). Недетерминированный код здесь допустим. У activity есть **таймауты** (`startToClose`, `scheduleToClose`, `heartbeat`) и **политика retry** (начальный интервал, коэффициент backoff, максимум попыток, типы ошибок без повтора). Долгие activity отправляют heartbeat, чтобы сервер мог обнаружить мёртвый воркер.
- **Воркер** (worker): процесс, который опрашивает **очередь задач** (task queue) на предмет задач workflow и activity и выполняет зарегистрированные реализации. Масштабирование — добавлением воркеров; особые activity (GPU, локальная сеть on-prem) направляются в свою очередь задач.
- Каждый вызов activity может выполниться больше одного раза (retry, падение воркера после выполнения, но до отчёта) → activity должны быть [[idempotency|идемпотентными]]; в качестве ключа идемпотентности используй `workflowId + activity name` или бизнес-ключ.

### История событий и детерминированный replay
- Сервер записывает команды и результаты. При replay SDK заново выполняет код workflow и сопоставляет каждую команду с историей; результаты завершённых activity возвращаются из истории, а не выполняются повторно.
- **Запрещено в коде workflow**: `System.currentTimeMillis()`, `new Random()`, `UUID.randomUUID()`, `Thread.sleep`, прямой ввод-вывод (БД, HTTP, файлы), потоки/`CompletableFuture`, статическое изменяемое состояние, обход `HashMap`, порядок которого может меняться. Любое из этого приводит к расхождению replay → `NonDeterministicException`.
- **Вместо этого используй**: `Workflow.currentTimeMillis()`, `Workflow.randomUUID()`, `Workflow.sleep(Duration)`, `Workflow.sideEffect(() -> …)` (записывается один раз, при replay берётся из истории), `Workflow.newActivityStub`, `Async.function` + `Promise` для параллелизма, `Workflow.await(condition)`.
- Логирование в workflow: `Workflow.getLogger` подавляет вывод во время replay.

### Взаимодействие и композиция
- **Сигнал** (signal, `@SignalMethod`): асинхронное сообщение в работающий workflow («оплата подтверждена», «абонент отсканировал QR»); workflow ждёт через `Workflow.await(() -> confirmed)`.
- **Запрос** (query, `@QueryMethod`): синхронное чтение состояния workflow без его изменения (статус для UI).
- **Обновление** (update, `@UpdateMethod`, GA в SDK 1.2x / сервере 1.24+): сигнал, который возвращает результат и может валидироваться через `@UpdateValidatorMethod`; заменяет паттерн «сигнал + опрос через query».
- **Дочерние workflow** (child workflows): разбивают большие процессы (один workflow на SIM в массовом заказе); родитель может ждать или запустить и забыть (`ParentClosePolicy`).
- **Continue-as-new**: у workflow, который крутится вечно (ежедневная сверка), история растёт без ограничений; вызов `Workflow.continueAsNew(state)` запускает новый прогон с урезанной историей. Держи историю в пределах ~10k событий / нескольких МБ.
- **Версионирование**: изменение кода workflow при выполняющихся прогонах ломает replay. `int v = Workflow.getVersion("add-kyc-step", Workflow.DEFAULT_VERSION, 1);` направляет старые прогоны по старой ветке, а новые — по новой. SDK 1.2x также предлагает **Worker Versioning** (на основе deployment: старые воркеры доводят старые прогоны).
- **Помощник для саги**: `io.temporal.workflow.Saga` собирает компенсации; `saga.compensate()` выполняет их в обратном порядке — оркестрируемая сага с персистентностью бесплатно.

### Интеграция со Spring Boot
- `temporal-spring-boot-starter`: настрой `spring.temporal.connection.target`, `namespace`, `workers`. Помечай реализации `@WorkflowImpl(taskQueues = "esim")` и `@ActivityImpl(taskQueues = "esim")`; стартер регистрирует их и запускает воркеры. Activity — это Spring-бины, поэтому в них можно внедрять репозитории и клиенты.
- Для запуска workflow внедряется `WorkflowClient`: `WorkflowClient.start(stub::provision, order)`; `WorkflowOptions.workflowId` даёт идемпотентный запуск (`WorkflowIdReusePolicy`, `WorkflowIdConflictPolicy`).
- Тестирование: `temporal-testing` предоставляет `TestWorkflowEnvironment` с пропуском времени (time-skipping), так что 24-часовой `Workflow.sleep` выполняется мгновенно; `WorkflowReplayer` проигрывает продакшн-истории на новом коде в CI.

### Temporal vs Kafka + outbox
- Kafka + [[outbox-pattern]] + самописный конечный автомат достаточно для коротких, в основном линейных процессов, где каждый шаг — одно событие. На тебе остаются: retry, таймеры («ждать 24 ч сканирования QR»), порядок компенсаций, запросы статуса, версионирование выполняющихся процессов.
- Temporal окупается, когда в процессах есть ожидания, шаги с участием людей, много ветвлений и компенсаций или когда на вопрос «где заказ 42 и почему он застрял» нужно отвечать из UI. Цена: ещё один кластер с состоянием (сервер + Postgres/Cassandra + Elasticsearch для visibility) и дисциплина детерминизма.
- Они хорошо сочетаются: событие Kafka запускает workflow; activity публикуют события Kafka через outbox.

## Пример
Провижининг eSIM: зарезервировать профиль, заказать загрузку на SM-DP+, ждать до 24 ч, пока абонент установит профиль, затем активировать в сети; если абонент так и не установил — выполнить компенсацию.

```java
@WorkflowInterface
public interface EsimProvisioningWorkflow {
    @WorkflowMethod
    ProvisioningResult provision(ProvisionOrder order);
    @SignalMethod
    void profileInstalled(String eid);
    @QueryMethod
    String status();
}

@WorkflowImpl(taskQueues = "esim")
public class EsimProvisioningWorkflowImpl implements EsimProvisioningWorkflow {
    private final EsimActivities acts = Workflow.newActivityStub(EsimActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofSeconds(30))
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setInitialInterval(Duration.ofSeconds(2))
                            .setBackoffCoefficient(2.0)
                            .setMaximumAttempts(5)
                            .setDoNotRetry(ProfileNotFoundException.class.getName())
                            .build())
                    .build());
    private String status = "STARTED";
    private boolean installed;

    @Override
    public ProvisioningResult provision(ProvisionOrder order) {
        Saga saga = new Saga(new Saga.Options.Builder().setParallelCompensation(false).build());
        try {
            String iccid = acts.reserveProfile(order.orderId());
            saga.addCompensation(acts::releaseProfile, iccid);
            status = "RESERVED";

            String matchingId = acts.createDownloadOrder(order.orderId(), iccid);
            saga.addCompensation(acts::cancelDownloadOrder, matchingId);
            acts.sendQrCode(order.msisdn(), matchingId);
            status = "WAITING_INSTALL";

            boolean ok = Workflow.await(Duration.ofHours(24), () -> installed);
            if (!ok) { saga.compensate(); status = "EXPIRED"; return ProvisioningResult.expired(iccid); }

            acts.activateOnHlr(order.msisdn(), iccid);   // хвост с retry, без компенсации
            status = "ACTIVE";
            return ProvisioningResult.active(iccid);
        } catch (ActivityFailure e) {
            saga.compensate();
            status = "FAILED";
            throw e;
        }
    }

    @Override public void profileInstalled(String eid) { installed = true; }
    @Override public String status() { return status; }
}

@ActivityInterface
public interface EsimActivities {
    String reserveProfile(String orderId);
    void releaseProfile(String iccid);
    String createDownloadOrder(String orderId, String iccid);
    void cancelDownloadOrder(String matchingId);
    void sendQrCode(String msisdn, String matchingId);
    void activateOnHlr(String msisdn, String iccid);
}

@Component
@ActivityImpl(taskQueues = "esim")
@RequiredArgsConstructor
public class EsimActivitiesImpl implements EsimActivities {
    private final ProfileRepository profiles;
    private final SmdpGateway smdp;
    // каждый метод идемпотентен по orderId/iccid; при исключении Temporal повторяет его
    ...
}
```

## Подводные камни
- Вызов БД или `RestClient` в классе workflow «просто для поиска»: работает до первого replay, потом `NonDeterministicException`.
- В опциях activity по умолчанию **нет** `startToCloseTimeout` → SDK отклоняет стаб; а retry по умолчанию **бесконечный** — activity, бросающая бизнес-ошибку, повторяется вечно, если ошибка не указана в `doNotRetry` или не брошена как `ApplicationFailure.newNonRetryableFailure`.
- Большие данные в аргументах/результатах workflow раздувают историю (лимит 2 МБ на payload, 50 МБ на историю); передавай id, а не документы.
- Deploy изменённого кода workflow без `getVersion` или версионирования воркеров молча ломает при replay каждый выполняющийся прогон. Запускай `WorkflowReplayer` на образцах историй в CI.
- Сигналы, пришедшие до того, как workflow дошёл до `Workflow.await`, не теряются (они буферизуются в истории), но обработчику сигнала, который вызывает activity, нужен `Workflow.await(() -> allHandlersFinished())` перед возвратом из workflow.
- Связанные заметки: [[saga]], [[idempotency]], [[outbox-pattern]], [[esim-provisioning-platform]], [[kafka-consumer]] (альтернатива), [[system-design-basics]].
