---
title: Потокобезопасные коллекции
tags: [collections, concurrenthashmap, copyonwrite, skip-list, weakly-consistent]
learned: 2026-10-22
---
## Кратко
Обёртки `Collections.synchronizedX()` (JDK 1.2) блокируют всю коллекцию на каждом вызове и всё равно оставляют итерацию небезопасной. `java.util.concurrent` (Java 5) заменяет их коллекциями, спроектированными для конкурентности: `ConcurrentHashMap` блокирует только ту корзину, в которую пишет, `CopyOnWriteArrayList` копирует массив при записи и никогда не блокирует читателей, `ConcurrentSkipListMap` держит ключи отсортированными без глобальной блокировки, а очереди либо lock-free, либо намеренно блокирующие. Их итераторы никогда не бросают `ConcurrentModificationException`, но могут не увидеть изменений, сделанных во время обхода.

## Основное
### Что выбрать
| Однопоточная | Потокобезопасная | Как обеспечивается безопасность | Когда использовать |
|---|---|---|---|
| `HashMap` | `ConcurrentHashMap` | CAS в пустые корзины, блокировка на корзину | общая map по умолчанию, кэши, счётчики |
| `HashSet` | `ConcurrentHashMap.newKeySet()` | так же | общее множество |
| `TreeMap` / `TreeSet` | `ConcurrentSkipListMap` / `Set` | CAS на ссылках skip list | отсортированные ключи, запросы по диапазону |
| `ArrayList` | `CopyOnWriteArrayList` | копия массива на каждую запись | списки, которые в основном читают: слушатели, конфиг |
| `ArrayDeque` / `LinkedList` как очередь | `ConcurrentLinkedQueue` / `Deque` | lock-free CAS | неблокирующая передача |
| (любая очередь) | `ArrayBlockingQueue`, `LinkedBlockingQueue`… | блокировки + условия | producer/consumer, см. [[blocking-queues]] |
| `Hashtable`, `Vector` | (legacy) | `synchronized` на каждом методе | никогда в новом коде |

### Синхронизированные обёртки
- `synchronizedList / Set / Map / Collection` оборачивают оригинал; каждый метод берёт одну и ту же блокировку (объект-обёртку).
- Одна глобальная блокировка выстраивает в очередь всё, включая читателей.
- Итерация не защищена: нужно держать блокировку на весь цикл (`synchronized (list) { for (…) }`), иначе fail-fast итератор бросит `ConcurrentModificationException` ([[iterators-and-fail-fast]]).
- Составные действия (`if (!contains) add`) — гонка, если не блокировать вокруг них тоже.

### ConcurrentHashMap
- Чтения никогда не блокируются; запись блокирует только одну корзину. Java 7 использовала 16 сегментов с собственными блокировками; в Java 8+ сегментов нет.
- Атомарные составные операции: `putIfAbsent`, `computeIfAbsent`, `compute`, `merge`, `replace(k, old, new)`.
- Null-ключи и null-значения запрещены: `get`, вернувший null, должен однозначно значить «нет», ведь заблокировать map, чтобы сначала проверить `containsKey`, нельзя.
- Как внутри устроены `put`, resize и `size()`: [[concurrenthashmap-internals]].

### Copy-on-write
- `CopyOnWriteArrayList` / `CopyOnWriteArraySet`: каждая запись (add, set, remove) берёт блокировку, копирует массив, меняет копию и публикует её через volatile-поле. Читатели работают с тем массивом, который получили, поэтому никогда не блокируются.
- Итераторы — **снимки** массива на момент создания: они не видят последующих записей, а `iterator.remove()` бросает `UnsupportedOperationException`.
- Каждая запись стоит O(n) по времени и памяти; `CopyOnWriteArraySet.contains` — O(n). Только для маленьких данных, которые в основном читают.

### Skip list: сортировка без поворотов
- `ConcurrentSkipListMap` / `ConcurrentSkipListSet` — конкурентные `TreeMap` / `TreeSet` с полным API `NavigableMap` (`ceilingKey`, `headMap`, `pollFirstEntry`).
- Skip list — это отсортированный связный список плюс «экспресс-полосы»: каждый узел получает случайное число уровней индекса, поэтому на каждом более высоком уровне лишь часть узлов. Поиск начинается с верхнего уровня и спускается вниз, что даёт O(log n) в среднем.
- Почему нет конкурентного красно-чёрного дерева: одна вставка может перекрашивать и поворачивать узлы до самого корня, а это трудно сделать без большой блокировки. Вставка в skip list — это CAS на нескольких ссылках `next` нижнего уровня; уровни индекса добавляются потом и никогда не требуют перебалансировки.
- `size()` обходит весь список, O(n).

### Слабо согласованные итераторы
- Итераторы `ConcurrentHashMap`, `ConcurrentSkipListMap`, `ConcurrentLinkedQueue` и блокирующих очередей **слабо согласованные** (weakly consistent): они никогда не бросают `ConcurrentModificationException`, никогда не возвращают элемент дважды и видят каждый элемент, который существовал при создании итератора и не был удалён. Изменения во время обхода могут попасть в него, а могут и не попасть.
- То же для `size()`, `isEmpty()`, `toArray()`: при конкурентных записях это оценка, а не снимок под блокировкой.

## Пример
```java
// отсортированная потокобезопасная книга заявок: лучшая цена без блокировок
ConcurrentNavigableMap<BigDecimal, Long> bids = new ConcurrentSkipListMap<>(Comparator.reverseOrder());
bids.merge(new BigDecimal("101.50"), 300L, Long::sum);   // атомарно добавить к уровню
Map.Entry<BigDecimal, Long> best = bids.firstEntry();   // самая высокая цена

// слушатели, которых в основном читают: обход никогда не блокируется и не бросает
List<OrderListener> listeners = new CopyOnWriteArrayList<>();
void publish(Order o) { listeners.forEach(l -> l.onOrder(o)); }
```

## Подводные камни
- `if (!map.containsKey(k)) map.put(k, v)` — гонка даже на `ConcurrentHashMap`; используй `putIfAbsent` / `computeIfAbsent`.
- `CopyOnWriteArrayList` на горячем пути записи копирует массив на каждую запись и задушит GC.
- Потокобезопасная коллекция не делает потокобезопасными объекты внутри неё: изменяемое значение, взятое из `ConcurrentHashMap`, всё равно требует своей синхронизации.
- `Collections.unmodifiableList` не потокобезопасен: это представление только для чтения над списком, который кто-то другой может менять.
- Связанное: [[concurrenthashmap-internals]], [[blocking-queues]], как работает CAS внутри — в [[locks-and-lock-free]].
