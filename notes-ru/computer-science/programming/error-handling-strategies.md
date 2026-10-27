---
title: Стратегии обработки ошибок
tags: [programming, exceptions, result-types, fail-fast, retries, resilience]
learned: 2026-10-27
---
## Кратко
Отделяй **ожидаемые исходы** (валидация не прошла, недостаточно средств, не найдено) от **отказов** (база упала, баг, таймаут). Ожидаемые исходы моделируй в системе типов или как доменные исключения, которые вызывающий обязан обработать; отказы пусть распространяются как непроверяемые исключения до границы, которая один раз логирует, преобразует в ответ и решает, делать ли retry. **Падай быстро** (fail fast) внутри процесса (плохая конфигурация, нарушенный инвариант: падай рано и громко); **будь устойчив на границах** (таймауты, ограниченные retry с backoff, circuit breaker, очереди недоставленных сообщений). Механика Java (проверяемые и непроверяемые исключения, try-with-resources, цепочки) описана в [[exceptions]]; эта заметка — о выборе стратегии.

## Основное

### Сначала классифицируй
| Категория | Пример | Кто обрабатывает | Механизм |
|---|---|---|---|
| Ожидаемый доменный исход | карта отклонена, eSIM уже активирована, ошибка валидации | непосредственный вызывающий, как бизнес-логику | result/sealed-тип или конкретное доменное исключение |
| Ошибка программирования | `NullPointerException`, `IllegalArgumentException` от неправильного вызова | никто в месте вызова; исправь баг | непроверяемое, пусть дойдёт до границы, 500, алерт |
| Временный отказ инфраструктуры | таймаут, сброс соединения, 503, взаимоблокировка | политика retry на границе | retry с backoff, circuit breaker ([[circuit-breaker]]) |
| Постоянный отказ инфраструктуры | неверные учётные данные, нет таблицы, неизвестный хост | оператор | падать быстро при старте, алерт |
| Исчерпание ресурсов | OOM, пул исчерпан, диск заполнен | процесс/платформа | упасть и перезапуститься (Kubernetes), обратное давление ([[backpressure-and-flow-control]]) |

### Исключения против типов-результатов
| | Исключения | Result / sealed-типы (`Either`, `Result<T, E>`, `Optional`) |
|---|---|---|
| Видимость в сигнатуре | только проверяемые; непроверяемые не видны | всегда видны |
| Компилятор заставляет обработать | проверяемые: да, но их оборачивают и пробрасывают дальше | исчерпывающий `switch` по sealed-типам: да |
| Композиция с лямбдами/стримами | плохая (проверяемые), нормальная (непроверяемые) | хорошая |
| Стоимость | захват стектрейса дорог (микросекунды, выделение памяти) | небольшой объект |
| Поток управления | нелокальный переход, пропускает фреймы | явный в каждом вызове |
| Лучше всего для | отказов, которые нельзя обработать на месте | ожидаемых альтернативных исходов |

- Go (возврат `err`), Rust (`Result`), Kotlin Arrow и функциональная Java переносят ожидаемые исходы в возвращаемые значения. Java 21 делает это удобным: `sealed interface Outcome permits Success, Declined, NotFound` + `switch` с pattern matching ([[type-systems]]).
- `Optional` — это тип-результат ровно для одного случая: отсутствия значения. Не используй его для полей или параметров; не используй его, чтобы сигнализировать об ошибке с причиной.
- Проверяемые исключения в Java были этой же идеей в форме 1995 года; индустрия в основном перешла на непроверяемые (Spring, Hibernate, Kotlin), потому что проверяемые раскрывают детали реализации и плохо компонуются ([[exceptions]]).

### Падай быстро
- Валидируй на границе (DTO запросов, Bean Validation, конфигурация при старте через `@ConfigurationProperties` + `@Validated`), чтобы плохие входные данные никогда не попадали в домен ([[spring-mvc-validation-and-uploads]]).
- Конструкторы обеспечивают инварианты; если объект существует, он валиден ([[value-objects-and-entities]]).
- `Objects.requireNonNull`, проверки невозможных состояний, `throw new IllegalStateException` вместо молчаливого продолжения: чем раньше баг остановит программу, тем ближе стектрейс к причине.
- Проверки при старте: роняй pod, если нет топиков Kafka, миграций БД ([[schema-migrations-flyway-liquibase]]) или нужных секретов; pod в crash loop заметнее, чем наполовину работающий.

### Устойчивость на границах
- **Таймауты везде**: на подключение, на чтение, на получение соединения из пула, statement timeout; отсутствующий таймаут — это бесконечный таймаут ([[failure-modes-and-timeouts]], [[postgres-performance-checklist]]).
- **Retry** только для идемпотентных операций и временных ошибок, с экспоненциальным backoff и джиттером, ограниченным числом попыток и общим бюджетом; retry усиливают нагрузку во время сбоя ([[service-communication-sync-vs-async]], [[idempotency]]).
- **Circuit breaker** перестаёт вызывать зависимость, которая продолжает отказывать ([[circuit-breaker]]); **bulkhead** изолирует пулы потоков для каждой зависимости.
- **Асинхронные consumer'ы**: перехватывать на уровне сообщения, классифицировать (можно повторить или «ядовитое»), делать retry через топики с задержкой, затем отправлять в dead-letter с причиной отказа ([[dead-letter-queues-and-retries]], [[spring-kafka-consumer]]).
- **Workflow**: Temporal повторяет activity по политике и хранит историю отказов; бизнес-отказы возвращаются как результаты, а не бросаются ([[temporal-concepts]]).
- **Компенсация**, когда многошаговый процесс падает на полпути ([[saga]]).

### Наблюдаемость ошибок
- Логируй один раз, на границе, с контекстом (id, операция), на правильном уровне: ожидаемые исходы — максимум `INFO`/`WARN`, отказы — `ERROR` со стектрейсом. Логирование с пробросом на каждом слое даёт пять трейсов на один инцидент ([[observability-three-pillars]]).
- Последовательно отображай в HTTP: 400/422 — валидация, 404 — не найдено, 409 — конфликт/идемпотентность, 429 — rate limit, 500 — баг, 502/503/504 — зависимость; тела в формате RFC 9457 `application/problem+json`; один `@RestControllerAdvice` ([[rest-and-http]]).
- Трекинг ошибок группирует по типу исключения и месту, поэтому оборачивай в осмысленный тип, а не в `RuntimeException("failed")` ([[error-tracking-with-sentry]]).

## Пример
Ожидаемые исходы как sealed-результат, отказы как непроверяемые исключения, одна граница, которая отображает и то и другое; retry оставлены HTTP-клиенту/слою устойчивости.

```java
sealed interface ActivationResult permits Activated, AlreadyActive, ProfileNotFound, SmdpUnavailable {}
record Activated(String iccid, Instant at) implements ActivationResult {}
record AlreadyActive(String iccid) implements ActivationResult {}
record ProfileNotFound(String iccid) implements ActivationResult {}
record SmdpUnavailable(String iccid, String detail) implements ActivationResult {}

@Service
@RequiredArgsConstructor
class ActivationService {
    private final ProfileRepository profiles;
    private final SmdpClient smdp;                  // имеет свои таймауты + ограниченный retry + circuit breaker

    @Transactional
    public ActivationResult activate(String iccid) {
        Profile p = profiles.findByIccid(iccid).orElse(null);
        if (p == null) return new ProfileNotFound(iccid);
        if (p.isActive()) return new AlreadyActive(iccid);         // ожидаемо, а не исключительно
        try {
            smdp.enable(iccid);                                    // может бросить SmdpException после retry
        } catch (SmdpException e) {
            return new SmdpUnavailable(iccid, e.getMessage());     // ожидаемый временный исход для вызывающего
        }
        p.activate(Instant.now());                                 // проверки инвариантов внутри; IllegalStateException = баг
        return new Activated(iccid, p.activatedAt());
    }
}

@RestController
@RequiredArgsConstructor
class ActivationController {
    private final ActivationService service;

    @PostMapping("/profiles/{iccid}/activate")
    ResponseEntity<?> activate(@PathVariable String iccid) {
        return switch (service.activate(iccid)) {                  // исчерпывающее отображение, проверяемое компилятором
            case Activated a        -> ResponseEntity.ok(a);
            case AlreadyActive a    -> ResponseEntity.status(409).body(a);
            case ProfileNotFound n  -> ResponseEntity.status(404).body(n);
            case SmdpUnavailable u  -> ResponseEntity.status(503).header("Retry-After", "30").body(u);
        };
    }
}
// Неожиданные исключения (баги, БД упала) минуют всё это и доходят до @RestControllerAdvice, который логирует один раз и возвращает 500.
```

## Подводные камни
- `catch (Exception e) { log.error(...); }` и продолжение работы: проглатывает баги, превращает порчу данных в тишину; перехватывай узко или пробрасывай дальше.
- Retry неидемпотентного вызова (списание платежа, `INSERT` без ключа) по таймауту: первая попытка могла завершиться успешно ([[idempotency]]).
- Исключения для ожидаемого потока на горячих путях (`NumberFormatException`, чтобы проверить, число ли строка, `NoSuchElementException` при поиске): захват стектрейса стоит микросекунды на каждый и засоряет дашборды ошибок.
- Оборачивание всех исключений в один `ServiceException` теряет тип, по которому граница решает: 4xx, 5xx или retry; держи небольшую иерархию.
- Связанные заметки: [[exceptions]] — механика Java, [[circuit-breaker]] и [[dead-letter-queues-and-retries]] — сторона устойчивости, [[type-systems]] — sealed-типы результатов.
