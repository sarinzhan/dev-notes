---
title: Пулы потоков, ExecutorService и Future
tags: [executor, thread-pool, future, async, spring-boot]
learned: 2026-10-22
---
## Кратко
**Пул потоков** (thread pool) — это набор заранее созданных потоков, которые берут задачи из очереди и используются повторно. С Java 5 пакет `java.util.concurrent` предоставляет для этого `Executor` / `ExecutorService`, `Future` для результата отправленной задачи и `Executors` как фабрику. Никогда не создавай потоки вручную в коде приложения.

## Основное
### Интерфейсы
- `Executor`: один метод, `execute(Runnable)`.
- `ExecutorService`: добавляет `submit(Callable)` → `Future`, `invokeAll`, `shutdown()` / `shutdownNow()` / `awaitTermination()`. С Java 19 он `AutoCloseable`, так что `try (var pool = ...)` работает и при выходе ждёт задачи.
- `ScheduledExecutorService`: запуск с задержкой или периодически.
- Фабрики `Executors`: `newFixedThreadPool(n)`, `newCachedThreadPool()` (повторно использует простаивающие потоки, создаёт новые по требованию, убивает потоки, простаивающие 60 с; без ограничения), `newSingleThreadExecutor()`, `newVirtualThreadPerTaskExecutor()` (Java 21, см. [[virtual-threads]]). За ними стоит `ThreadPoolExecutor(core, max, keepAlive, queue, factory, rejectionHandler)`.

### Future и FutureTask
- `Future<V>`: `get()` блокируется, пока результат не готов (используй перегрузку с таймаутом), `cancel(mayInterrupt)`, `isDone()`, `isCancelled()`.
- `FutureTask` реализует и `Runnable`, и `Future`: это и задача, которую выполняет пул, и дескриптор, который ты держишь. Полезно, когда нужно выполнить одно и то же вычисление один раз и дать многим потокам его дождаться.
- Компонуемая неблокирующая альтернатива: [[completable-future]].

### Размер пула
- Работа, ограниченная процессором (CPU-bound): примерно от `cores` до `cores + 1` потоков.
- Блокирующий ввод-вывод: `cores * (1 + waitTime / computeTime)` или просто виртуальные потоки.
- Для максимальной пропускной способности при минимальной конкуренции распространённое эмпирическое правило — `cores * 1.5`. Измеряй; реальным ограничением часто оказывается пул базы данных ([[hikaricp-pool-sizing]]).

### @Async в Spring
- `@EnableAsync` + `@Async` выполняют метод бина в другом потоке через прокси (те же правила, что для `@Transactional`: public, вызов из другого бина).
- В чистом Spring по умолчанию используется `SimpleAsyncTaskExecutor` — новый поток на каждый вызов, без пула. Spring Boot 2+ автоматически настраивает `ThreadPoolTaskExecutor` (`spring.task.execution.pool.*`, core size 8, очередь по умолчанию без ограничения). Настраивается через `AsyncConfigurer` или именованный бин `Executor`. `spring.threads.virtual.enabled=true` переключает его на виртуальные потоки.

## Пример
```java
ExecutorService pool = Executors.newFixedThreadPool(8);
try {
    List<Future<Report>> futures = regions.stream()
        .map(r -> pool.submit(() -> buildReport(r)))   // Callable<Report>
        .toList();
    for (Future<Report> f : futures) {
        reports.add(f.get(10, TimeUnit.SECONDS));
    }
} finally {
    pool.shutdown();
}
```

## Подводные камни
- Неограниченная очередь (`newFixedThreadPool`) никогда не отклоняет задачи, поэтому при перегрузке съедает память; ограниченная очередь с политикой отказа вместо этого быстро падает.
- `newCachedThreadPool` при всплеске создаёт тысячи потоков.
- Исключение задачи сохраняется в `Future` и проявляется только при `get()`; при `execute()` оно уходит в обработчик неперехваченных исключений потока и исчезает из логов.
- Отлаживай асинхронный код через thread dump и давай потокам имена (`ThreadFactory`), чтобы дамп был читаемым.
- Потоки из пула сохраняют значения `ThreadLocal` между задачами ([[threads-basics]]).
