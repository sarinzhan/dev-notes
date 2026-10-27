---
title: ReentrantLock, ReadWriteLock, spin lock и неблокирующие структуры
tags: [locks, reentrantlock, readwritelock, cas, lock-free]
learned: 2026-10-22
---
## Кратко
`synchronized` — языковая конструкция с одним монитором и без настроек. `java.util.concurrent.locks` (Java 5) даёт то же взаимное исключение в виде класса с настройками: попытка с таймаутом, прерываемое ожидание, честность (fairness), несколько условий ожидания и раздельные блокировки на чтение и запись. Неблокирующие (lock-free) структуры полностью обходятся без блокировок и полагаются на CAS.

## Основное
### Lock и synchronized
- `tryLock()` проверяет доступность без ожидания; `tryLock(timeout, unit)` ждёт не дольше указанного. Избегает deadlock за счёт отказа.
- `lockInterruptibly()`: поток, ждущий блокировку, можно прервать. Поток, заблокированный на `synchronized`, прервать нельзя.
- Честный режим (`new ReentrantLock(true)`): потоки получают блокировку в порядке FIFO; медленнее, но без голодания.
- Объекты `Condition` (`lock.newCondition()`): несколько наборов ожидания на одну блокировку, у каждого `await()` / `signal()` / `signalAll()`. У монитора набор ожидания ровно один.
- Реентерабельность (reentrant) означает, что поток-владелец может захватить блокировку повторно, не блокируя сам себя; и `synchronized`, и `ReentrantLock` реентерабельны.

### ReadWriteLock
- `readLock()`: много читателей одновременно. `writeLock()`: один писатель, и читатели блокируются, пока она удерживается.
- Реализация — `ReentrantReadWriteLock`, с необязательной честностью для писателей, чтобы их не уморил голодом поток читателей. Для данных, которые в основном читают, с крошечными критическими секциями быстрее `StampedLock` (оптимистичные чтения).

### Spin lock
Поток, который хочет получить блокировку, проверяет её в цикле вместо того, чтобы засыпать (park). Избегает стоимости переключения контекста, когда ожидание длится микросекунды; сжигает ядро, когда нет. JVM и так немного крутится в цикле, прежде чем усыпить поток на занятом мониторе.

### Неблокирующие (и wait-free) структуры
- Построены на атомарных операциях, таких как CAS, вместо блокировок: прочитать текущее значение, вычислить новое, записать его через CAS, повторить, если кто-то изменил его раньше.
- Lock-free: хотя бы один поток всегда продвигается. Wait-free: каждый поток завершается за ограниченное число шагов (реже и сложнее).
- В JDK: `AtomicInteger`/`AtomicLong`/`AtomicReference`, `LongAdder`, `ConcurrentLinkedQueue`, части `ConcurrentHashMap` ([[concurrent-collections]]).

## Пример
```java
private final ReentrantLock lock = new ReentrantLock();
private final Condition notEmpty = lock.newCondition();

void put(T item) {
    lock.lock();
    try { queue.add(item); notEmpty.signal(); }
    finally { lock.unlock(); }          // всегда в finally
}

T take() throws InterruptedException {
    lock.lock();
    try {
        while (queue.isEmpty()) notEmpty.await();
        return queue.poll();
    } finally { lock.unlock(); }
}
```

## Подводные камни
- Забытый `unlock()` в `finally` навсегда оставляет блокировку захваченной; у `synchronized` такого бага быть не может.
- `ReadWriteLock` медленнее обычной блокировки, когда критические секции короткие или записи частые; измеряй.
- Циклы CAS при сильной конкуренции тратят CPU на повторы; для счётчиков `LongAdder` выигрывает у `AtomicLong`.
- Зачем вообще нужны блокировки: [[synchronization-and-monitors]], [[concurrency-hazards]].
