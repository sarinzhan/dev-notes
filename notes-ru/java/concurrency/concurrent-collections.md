---
title: Потокобезопасные коллекции
tags: [collections, concurrenthashmap, copyonwrite, blockingqueue]
learned: 2026-10-22
---
## Кратко
Обёртки `Collections.synchronizedX()` (JDK 1.2) блокируют всю коллекцию при каждом вызове и всё равно оставляют итерацию небезопасной. `java.util.concurrent` (Java 5) заменяет их коллекциями, спроектированными для конкурентности: `ConcurrentHashMap` блокирует только корзину, в которую пишет, `CopyOnWriteArrayList` копирует при записи и никогда не блокирует читателей, а очереди либо неблокирующие (lock-free), либо блокирующие намеренно.

## Основное
### Синхронизированные обёртки
- `synchronizedList / Set / Map / Collection` оборачивают оригинал; каждый метод берёт одну и ту же блокировку.
- Недостатки: одна глобальная блокировка сериализует всё, а итерация небезопасна, если не блокировать вручную вокруг цикла; иначе `ConcurrentModificationException`.

### ConcurrentHashMap
- Чтения никогда не блокируются; записи блокируют только небольшую часть.
- Java 7: map делилась на сегменты, каждый — маленькая независимая хеш-таблица со своей блокировкой.
- Java 8+: сегментов нет. `put` вычисляет хеш и индекс корзины; пустая корзина заполняется через CAS; занятая корзина блокируется на уровне **узла** (`synchronized` на голове), и запись добавляется в цепочку или вставляется в дерево.
- Атомарные составные операции: `putIfAbsent`, `computeIfAbsent`, `merge`. `size()` при конкурентных записях — оценка.
- Null-ключи и null-значения запрещены (в отличие от `HashMap`).

### Копирование при записи (copy-on-write)
- `CopyOnWriteArrayList` / `CopyOnWriteArraySet`: каждая запись (add, set, remove) блокирует, копирует внутренний массив, изменяет копию и атомарно подменяет её. Читатели и итераторы используют старый массив, поэтому никогда не блокируются и никогда не бросают `ConcurrentModificationException`.
- Только для данных, которые в основном читают (слушатели, списки конфигурации). Каждая запись — O(n).

### Очереди
- `ConcurrentLinkedQueue` / `ConcurrentLinkedDeque`: неблокирующие связные структуры на основе CAS.
- `BlockingQueue` / `BlockingDeque` (`ArrayBlockingQueue`, `LinkedBlockingQueue`, `PriorityBlockingQueue`, `SynchronousQueue`): `put` блокируется, когда очередь полна, `take` — когда пуста. Базовый кирпич для producer/consumer и то, что пулы потоков используют внутри ([[executors-and-thread-pools]]).

### Сортированные
- `ConcurrentSkipListMap` / `ConcurrentSkipListSet`: конкурентные аналоги `TreeMap` / `TreeSet`, реализованные как списки с пропусками (skip lists).

## Пример
```java
ConcurrentMap<String, LongAdder> hits = new ConcurrentHashMap<>();
void record(String path) {
    hits.computeIfAbsent(path, k -> new LongAdder()).increment();   // атомарно получить или создать
}
```

## Подводные камни
- `if (!map.containsKey(k)) map.put(k, v)` — гонка даже на `ConcurrentHashMap`; используй `putIfAbsent` / `computeIfAbsent`.
- Долгая работа внутри `computeIfAbsent` блокирует корзину и может привести к deadlock, если обращается к той же map.
- `CopyOnWriteArrayList` на горячем пути записи раздавит GC.
- Как работает лежащий в основе CAS: [[locks-and-lock-free]].
