---
title: Circuit breaker, retry, bulkhead — Resilience4j в Spring Boot
tags: [microservices, resilience, resilience4j, spring-boot]
learned: 2026-10-27
---
## Кратко
**Автоматический выключатель** (circuit breaker) оборачивает удалённый вызов и отслеживает долю ошибок в скользящем окне. Когда доля превышает порог, breaker **размыкается** (open) и сразу отклоняет вызовы, вместо того чтобы ждать мёртвую зависимость; после паузы он пропускает несколько пробных вызовов (**полуоткрытое** состояние, half-open) и снова замыкается, если они успешны. Resilience4j 2.x даёт breaker, retry, time limiter, bulkhead и rate limiter в виде аннотаций в Spring Boot 3. Порядок защиты: **сначала таймаут, потом retry с backoff, потом breaker**, и повторять можно только [[idempotency|идемпотентное]].

## Какую проблему решает
- **Circuit breaker:** без него, когда SM-DP+ зависает, каждый запрос на provisioning ждёт полный таймаут, потоки копятся, и сервис падает вместе со своей зависимостью → он быстро отклоняет вызовы (или возвращает fallback вроде «queued») и даёт зависимости время восстановиться.
- **Retry:** без него один оборванный connection или 503 проваливает загрузку eSIM, которая прошла бы через секунду → он повторяет временные ошибки с backoff и jitter.
- **Bulkhead:** без него один медленный партнёр занимает все потоки Tomcat, и несвязанные endpoint-ы тоже перестают отвечать → он ограничивает число одновременных вызовов на каждую зависимость.
- **Когда не нужно:** вызовы, которые не могут надолго зависнуть (локальная БД с таймаутами пула и запросов), или малонагруженные внутренние инструменты, где достаточно одного таймаута. Retry неуместен для неидемпотентных записей без ключа идемпотентности.

## Основное

### Состояния
- **CLOSED** — вызовы проходят; breaker записывает успехи/ошибки/медленные вызовы в скользящее окно.
- **OPEN** — каждый вызов сразу падает с `CallNotPermittedException` (или выполняется fallback). Длится `waitDurationInOpenState`.
- **HALF_OPEN** — проходят `permittedNumberOfCallsInHalfOpenState` пробных вызовов; если их доля ошибок ниже порога → CLOSED, иначе → снова OPEN.
- Скользящее окно: `COUNT_BASED` (последние N вызовов) или `TIME_BASED` (последние N секунд). `minimumNumberOfCalls` не даёт одной ошибке из двух вызовов разомкнуть breaker. `slowCallRateThreshold` + `slowCallDurationThreshold` считают медленные ответы ошибками — зависимость, отвечающая за 10 с, так же плоха, как недоступная.
- `recordExceptions` / `ignoreExceptions`: ошибка валидации 4xx от зависимости — не сбой зависимости, и её не следует учитывать.

### Аннотации Resilience4j (Spring Boot 3, `resilience4j-spring-boot3`)
| Аннотация | Назначение | Ключевая конфигурация |
|---|---|---|
| `@CircuitBreaker(name, fallbackMethod)` | Быстрый отказ при больной зависимости | окно, пороги, ожидание в open |
| `@Retry(name, fallbackMethod)` | Повтор временных сбоев | `maxAttempts`, `waitDuration`, `enableExponentialBackoff`, `enableRandomizedWait` |
| `@TimeLimiter(name)` | Ограничение длительности вызова; метод должен возвращать `CompletableFuture` | `timeoutDuration`, `cancelRunningFuture` |
| `@Bulkhead(name, type)` | Ограничение числа одновременных вызовов (`SEMAPHORE`) или отдельный пул потоков (`THREADPOOL`) | `maxConcurrentCalls`, `maxWaitDuration` |
| `@RateLimiter(name)` | Ограничение числа вызовов к зависимости за период | `limitForPeriod`, `limitRefreshPeriod` |

- Порядок аспектов по умолчанию, когда на одном методе несколько аннотаций: `Retry` снаружи, затем `CircuitBreaker`, `RateLimiter`, `TimeLimiter`, `Bulkhead` внутри. Меняется через `resilience4j.retry.retryAspectOrder` и т. п. Retry *снаружи* breaker-а означает, что каждый retry — отдельный вызов breaker-а; обычно это и нужно.
- Аннотации реализованы через прокси [[spring-aop]]: вызов внутри того же бина (self-invocation) их обходит.
- Метод fallback должен иметь ту же сигнатуру плюс параметр-исключение; он может вернуть кэшированное/значение по умолчанию или пробросить доменное исключение.
- Метрики: каждый экземпляр отдаёт `resilience4j_circuitbreaker_state`, `..._calls` и т. д. через Micrometer → Prometheus; алерт на `state == open` ([[otel-metric-types]]).

### Retry как надо
- Повторять только временные ошибки: таймауты, 503, сброс соединения, Kafka `RetriableException`. Никогда не повторять 400/404/409 и бизнес-ошибки.
- **Экспоненциальный backoff + jitter** (`enableRandomizedWait`): без jitter все клиенты повторяют в один и тот же момент и создают **шторм retry** (retry storm), который не даёт зависимости подняться.
- Бюджет retry: 3 попытки × таймаут 2 с = 6 с заблокированного потока; нужно убедиться, что собственный таймаут вызывающего длиннее, иначе retry напрасны.
- Повторённый запрос мог уже выполниться на сервере (потерялся ответ). Операция должна быть идемпотентной или нести ключ идемпотентности — см. [[idempotency]].

### Bulkhead и таймауты
- Таймауты защищают потоки; без таймаута breaker никогда не увидит ошибку, только зависшие потоки. Таймауты соединения и чтения нужно выставить в HTTP-клиенте ([[rest-clients]]) и в JDBC ([[hikaricp-pool-sizing|HikariCP]]) до того, как добавлять какие-либо аннотации устойчивости.
- С [[virtual-threads]] семафорный bulkhead всё ещё полезен, чтобы ограничить нагрузку на партнёра.

## Пример
Вызов SM-DP+ (сервер профилей eSIM) с таймаутом, retry с backoff и jitter и breaker-ом, который откатывается на статус «в очереди».

```yaml
resilience4j:
  circuitbreaker:
    instances:
      smdp:
        slidingWindowType: COUNT_BASED
        slidingWindowSize: 20
        minimumNumberOfCalls: 10
        failureRateThreshold: 50
        slowCallDurationThreshold: 2s
        slowCallRateThreshold: 80
        waitDurationInOpenState: 30s
        permittedNumberOfCallsInHalfOpenState: 3
        automaticTransitionFromOpenToHalfOpenEnabled: true
        ignoreExceptions: [com.example.esim.ProfileNotFoundException]
  retry:
    instances:
      smdp:
        maxAttempts: 3
        waitDuration: 500ms
        enableExponentialBackoff: true
        exponentialBackoffMultiplier: 2
        enableRandomizedWait: true
        randomizedWaitFactor: 0.5
        retryExceptions: [java.io.IOException, org.springframework.web.client.HttpServerErrorException]
  timelimiter:
    instances:
      smdp:
        timeoutDuration: 3s
```

```java
@Service
public class SmdpGateway {
    private final RestClient rest;

    @Retry(name = "smdp")
    @CircuitBreaker(name = "smdp", fallbackMethod = "queueForLater")
    @TimeLimiter(name = "smdp")
    public CompletableFuture<DownloadOrder> downloadOrder(String iccid, String idemKey) {
        return CompletableFuture.supplyAsync(() -> rest.post()
                .uri("/es2plus/downloadOrder")
                .header("Idempotency-Key", idemKey)
                .body(new DownloadOrderRequest(iccid))
                .retrieve()
                .body(DownloadOrder.class));
    }

    private CompletableFuture<DownloadOrder> queueForLater(String iccid, String idemKey, Throwable t) {
        return CompletableFuture.completedFuture(DownloadOrder.queued(iccid));
    }
}
```

## Подводные камни
- Breaker на каждый *экземпляр* зависимости, а не один глобальный: мёртвый SMS-провайдер не должен размыкать breaker для платёжного шлюза.
- Слишком низкий `minimumNumberOfCalls` в сервисах с малым трафиком: две ошибки ночью размыкают breaker на 30 с без реального сбоя; слишком высокий — и он никогда не разомкнётся.
- Fallback, который вызывает другой удалённый сервис, нуждается в собственном breaker-е, иначе fallback падает точно так же.
- Retry внутри метода с `@Transactional` удерживает соединение с БД и блокировки во время backoff; повторять нужно вокруг транзакции, а не внутри неё ([[spring-transactional]]).
- Resilience4j 2.x требует Java 17+ и отказался от Vavr; скопированные конфигурации 1.x с типами `vavr` не скомпилируются.
- Связанные заметки: [[idempotency]], [[rest-clients]], [[saga]] (что делать, когда шаг действительно упал), [[api-gateway-and-service-discovery]] (ограничение частоты на уровне gateway), [[system-design-basics]].
