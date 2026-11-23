---
title: Блокирующие очереди
tags: [blockingqueue, producer-consumer, backpressure, thread-pools]
learned: 2026-11-23
---
## Кратко
`BlockingQueue` — это потокобезопасная очередь, у которой `put` ждёт, пока она полна, а `take` ждёт, пока она пуста. Поэтому это стандартный способ передавать работу между потоками (producer/consumer) и то, вокруг чего построен любой `ThreadPoolExecutor`. Реализацию выбирают по двум вопросам: ограничена ли очередь (backpressure, нет OOM) и как она блокирует (одна блокировка, две или ни одной)?

## Основное
### Четыре способа вызвать каждую операцию
| | Бросает исключение | Возвращает значение | Блокирует | Ждёт с таймаутом |
|---|---|---|---|---|
| Вставка | `add(e)` → `IllegalStateException` | `offer(e)` → `false` | `put(e)` | `offer(e, time, unit)` |
| Извлечение | `remove()` → `NoSuchElementException` | `poll()` → `null` | `take()` | `poll(time, unit)` |
| Просмотр | `element()` | `peek()` | — | — |

Первые два столбца пришли из `Queue`; `BlockingQueue` добавляет последние два. Блокирующие очереди отвергают `null`, потому что `poll()` использует `null` в значении «пусто». `drainTo(list, max)` переносит до `max` элементов за один вызов — так потребители читают пачками.

### Реализации
| Класс | Ограничена | Основа | Блокировки | Примечания |
|---|---|---|---|---|
| `ArrayBlockingQueue` | да, фиксированная ёмкость | кольцевой массив | один `ReentrantLock`, два условия (`notEmpty`, `notFull`) | нет аллокации на элемент; есть честный (fair) режим |
| `LinkedBlockingQueue` | по желанию, по умолчанию `Integer.MAX_VALUE` | связные узлы | **две блокировки**: `putLock` и `takeLock` | производители и потребители не блокируют друг друга; один узел на элемент |
| `LinkedBlockingDeque` | по желанию | связные узлы | одна блокировка | оба конца: например, срочная работа идёт в начало |
| `PriorityBlockingQueue` | нет | двоичная куча | одна блокировка | `take` возвращает наименьший; `put` никогда не блокирует |
| `DelayQueue` | нет | приоритетная куча | одна блокировка | элементы реализуют `Delayed`; `take` возвращает только просроченные: повторы, таймауты |
| `SynchronousQueue` | ёмкость 0 | — | lock-free передача | каждый `put` ждёт `take`; `offer` без ожидающего потребителя возвращает `false` |
| `LinkedTransferQueue` | нет | связные узлы | lock-free | `transfer(e)` ждёт, пока потребитель его получит; `tryTransfer` не ждёт |

Не блокирующие: `ConcurrentLinkedQueue` / `ConcurrentLinkedDeque` (lock-free, `poll` возвращает `null`, когда пусто); см. [[concurrent-collections]].

### Почему выбор важен для пулов потоков
- `ThreadPoolExecutor` вызывает `queue.offer(task)`, который никогда не блокирует. Если очередь приняла задачу, новых потоков сверх core size не создаётся. Только когда `offer` возвращает `false`, пул растёт до `maximumPoolSize`, а после этого срабатывает политика отказа.
- `newFixedThreadPool` использует неограниченную `LinkedBlockingQueue`: `offer` никогда не падает, поэтому пул никогда не растёт, а очередь под перегрузкой может съесть всю память.
- `newCachedThreadPool` использует `SynchronousQueue`: `offer` успешен, только если ждёт простаивающий поток, иначе создаётся новый поток. Поэтому он может создать тысячи потоков.
- В продакшене используй ограниченную `ArrayBlockingQueue` или `LinkedBlockingQueue` с явной политикой отказа (`CallerRunsPolicy` притормаживает производителя). Подробнее: [[executors-and-thread-pools]].

### Backpressure и остановка
- Ограниченная очередь — это бесплатный backpressure: быстрый производитель блокируется на `put`, а не заполняет кучу.
- `put` и `take` бросают `InterruptedException`. Либо пробрось его дальше, либо восстанови флаг через `Thread.currentThread().interrupt()`; никогда не глотай.
- Чтобы остановить потребителей, прерви их или отправь **poison pill** — особый элемент, означающий «работы больше нет».
- Блокироваться на этих очередях на виртуальных потоках нормально: они используют `ReentrantLock` и условия, поэтому поток-носитель освобождается ([[virtual-threads]]).

## Пример
```java
record Event(String type, String payload) {}
static final Event STOP = new Event("STOP", "");               // poison pill

BlockingQueue<Event> queue = new ArrayBlockingQueue<>(10_000);  // ограничена: backpressure

// производитель: ждёт, а не исчерпывает память, когда писатель медленный
void onEvent(Event e) throws InterruptedException { queue.put(e); }

// потребитель: один блокирующий take, затем забрать остальное и записать пачкой
void writerLoop() throws InterruptedException {
    List<Event> batch = new ArrayList<>(500);
    while (true) {
        batch.add(queue.take());
        queue.drainTo(batch, 499);
        boolean stop = batch.remove(STOP);
        if (!batch.isEmpty()) repository.insertAll(batch);
        if (stop) return;
        batch.clear();
    }
}
```

## Подводные камни
- «Неограниченные» очереди ограничены кучей. `new LinkedBlockingQueue<>()` без ёмкости — классический медленный OOM.
- `size()` и `remainingCapacity()` — снимки: к следующей строке другой поток мог их изменить. Не пиши `if (queue.size() < max) queue.put(e)`.
- Обход `PriorityBlockingQueue` не возвращает элементы в порядке приоритета; это делают только `take`/`poll`.
- `SynchronousQueue` ничего не хранит, поэтому `peek()` всегда возвращает `null`, а `size()` всегда 0.
- Связанное: [[concurrent-collections]], [[executors-and-thread-pools]], [[synchronizers]].
