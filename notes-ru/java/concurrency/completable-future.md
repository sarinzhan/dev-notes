---
title: Основы CompletableFuture
tags: [concurrency, async, java]
learned: 2026-09-30
---
## Кратко
`CompletableFuture` — это промис (promise) в Java: асинхронные шаги компонуются через `thenApply`, `thenCompose`, `thenCombine`, `allOf`, а ошибки обрабатываются через `exceptionally` / `handle` / `whenComplete`. Никогда не запускай блокирующий ввод-вывод в `ForkJoinPool.commonPool()` по умолчанию; передавай свой executor. С [[virtual-threads]] (Java 21) обычный блокирующий код покрывает большинство случаев, но `CompletableFuture` по-прежнему инструмент для fan-out/fan-in и таймаутов.

## Основное

### Future и CompletableFuture
- `Future` (Java 5) можно только опрашивать или блокироваться на нём через `get()`; нет колбэков, нет композиции, нет ручного завершения ([[executors-and-thread-pools]]).
- `CompletableFuture` (Java 8) реализует `Future` и `CompletionStage`: цепочки колбэков, комбинаторы, явные `complete()`/`completeExceptionally()` и (Java 9) `orTimeout`, `completeOnTimeout`, `delayedExecutor`, `copy`, `minimalCompletionStage`.

### Композиция
| Метод | Аналогия | Применение |
|---|---|---|
| `thenApply(fn)` | map | синхронно преобразовать результат |
| `thenCompose(fn -> CF)` | flatMap | следующий шаг сам асинхронный; избегает `CF<CF<T>>` |
| `thenCombine(other, bi)` | zip | два независимых future, нужны оба результата |
| `thenAccept` / `thenRun` | forEach | побочный эффект, возвращает `CF<Void>` |
| `allOf(cf...)` | дождаться всех | возвращает `CF<Void>`; результаты потом читаются через `join()` на каждом входном future |
| `anyOf(cf...)` | гонка | первый завершившийся, в том числе с исключением |
- Варианты `*Async` (`thenApplyAsync`) запускают колбэк на executor, а не на потоке, завершившем предыдущую стадию. Без `Async` колбэк выполняется на потоке, который завершает стадию, или на **регистрирующем потоке**, если стадия уже была завершена в момент построения цепочки.

### Ошибки
- `exceptionally(ex -> fallback)`: восстановиться, как `catch`. `handle((res, ex) -> ...)`: вызывается всегда, выбирает результат. `whenComplete((res, ex) -> ...)`: только наблюдает, исключение распространяется дальше.
- Исключение приходит обёрнутым в `CompletionException`; разворачивай через `ex.getCause()` перед `instanceof`. `join()` бросает непроверяемое `CompletionException`, `get()` — проверяемое `ExecutionException` ([[exceptions]]).
- Java 12 добавила `exceptionallyCompose` для асинхронного запасного варианта.

### Executor и общий пул
- `supplyAsync(s)` без executor использует `ForkJoinPool.commonPool()`: `cores - 1` потоков на всю JVM, включая параллельные стримы ([[fork-join-framework]]). Блокирующие вызовы HTTP или JDBC там душат всё остальное; в контейнере на 2 vCPU в пуле один поток.
- В сервисах всегда передавай выделенный executor, давай его потокам имена, подбирай размер под ввод-вывод, на котором он будет блокироваться, и делай его Spring-бином, чтобы он корректно останавливался. На Java 21 `Executors.newVirtualThreadPerTaskExecutor()` — самый простой выбор для fan-out по вводу-выводу.
- Общий пул не передаёт MDC, `SecurityContext` и транзакцию Spring ([[spring-transactional]]); копируй контекст явно или используй `ContextExecutorService` из Micrometer.

### Таймауты и отмена
- `orTimeout(2, SECONDS)` (Java 9) завершает future с `TimeoutException`; `completeOnTimeout(default, 2, SECONDS)` завершает его запасным значением. Оба работают на одном общем потоке-планировщике; колбэки должны быть лёгкими.
- Таймаут или `cancel(true)` меняет только состояние future; задача продолжает выполняться, а `mayInterruptIfRunning` игнорируется. Чтобы остановить реальную работу, используй клиент с собственным таймаутом (HttpClient, WebClient) или проверяй `Thread.interrupted()` в задаче.
- Отмена производной стадии не отменяет её источник.

### Виртуальные потоки (Java 21)
- С виртуальными потоками «вызвать три сервиса параллельно» — это три вызова `executor.submit` плюс `get()` или `StructuredTaskScope` (preview до Java 24, финальная версия в 25). Блокировка дешёвая, stack trace читаемые, без цепочек колбэков.
- `CompletableFuture` всё ещё выигрывает для: таймаутов в сочетании с запасными вариантами, гонок (`anyOf`), интеграции с библиотеками, возвращающими `CompletionStage` (Spring `@Async`, `send()` в Kafka возвращает `CompletableFuture` начиная со Spring Kafka 3.0).

## Пример
```java
@Service
public class SubscriberSummaryService {
    private final ExecutorService io = Executors.newVirtualThreadPerTaskExecutor();

    public Summary summary(String msisdn) {
        var balance = CompletableFuture.supplyAsync(() -> billing.balance(msisdn), io);
        var plan    = CompletableFuture.supplyAsync(() -> crm.plan(msisdn), io);
        var sims    = CompletableFuture.supplyAsync(() -> provisioning.sims(msisdn), io)
                          .completeOnTimeout(List.of(), 800, TimeUnit.MILLISECONDS); // необязательные данные

        return CompletableFuture.allOf(balance, plan, sims)
            .thenApply(v -> new Summary(balance.join(), plan.join(), sims.join()))
            .orTimeout(2, TimeUnit.SECONDS)
            .exceptionally(ex -> {
                Throwable cause = ex instanceof CompletionException ? ex.getCause() : ex;
                log.warn("summary degraded for {}: {}", msisdn, cause.toString());
                return Summary.fallback(msisdn);
            })
            .join();
    }
}
```
Три удалённых вызова выполняются параллельно; общая задержка — самый медленный вызов, а не сумма. `sims` деградирует до пустого списка самостоятельно, а отказ `balance`/`plan` деградирует весь ответ.

## Подводные камни
- Исключения обёрнуты: разворачивай `CompletionException.getCause()` перед сопоставлением типов; `exceptionally` на стадии после `thenApply` видит обёрнутую форму.
- Таймаут не отменяет нижележащую работу. Сочетай с отменяемым клиентом, иначе пул потоков заполнится вызовами-зомби.
- `allOf(...).join()` после отказа бросает **первый** отказ; остальные future могут ещё выполняться.
- Вызов `join()` внутри колбэка, который выполняется в общем пуле, может привести пул к deadlock (все потоки ждут друг друга).
- `thenApply` после `supplyAsync` на долгой задаче: если задача завершилась до того, как построена цепочка, колбэк выполняется синхронно на твоём потоке. Не делай предположений о потоке.
- Связанные заметки: [[spring-reactive-webclient-sse]] — реактивная альтернатива, [[circuit-breaker]] — запасные варианты, которые помнят отказы, [[synchronizers]] — fan-in в стиле `CountDownLatch` без future.
