---
title: Карта Java Collections Framework
tags: [collections, list, set, map, queue, big-o]
learned: 2026-10-22
---
## Кратко
Фреймворк делится на два корня: `Collection` (последовательности элементов: `List`, `Set`, `Queue`/`Deque`) и `Map` (пары ключ–значение). Над `Collection` стоит `Iterable` — именно он делает возможным `for-each`. Реализацию выбирают по нужным операциям и их стоимости, а не по привычке: `ArrayList` + `HashMap` покрывают 90% случаев.

## Основное
### Интерфейсы
- `Iterable` → предоставляет `Iterator` (а также `forEach`, `spliterator`). См. [[iterators-and-fail-fast]].
- `Collection` → `List` (упорядочен, дубликаты разрешены, доступ по индексу), `Set` (без дубликатов), `Queue` (FIFO: `offer/poll/peek` возвращают специальные значения, `add/remove/element` бросают исключение), `Deque` (оба конца: `offerFirst/pollLast…`, используется и как стек).
- `Map` (не `Collection`): `SortedMap`/`NavigableMap` добавляют упорядочивание по ключу (`firstKey`, `headMap`, `ceilingKey`).

### Реализации
| Интерфейс | Реализация | Основа | Примечания |
|---|---|---|---|
| List | `ArrayList` | растущий массив | доступ по индексу O(1); ёмкость растёт ×1,5 и автоматически никогда не уменьшается (`trimToSize`) |
| List / Deque | `LinkedList` | двусвязные узлы | вставка на концах O(1), по индексу O(n); редко лучше `ArrayDeque` |
| List | `Vector`, `Stack` | синхронизированный массив | legacy; используй `ArrayList` / `ArrayDeque` |
| Set | `HashSet` | `HashMap` с фиктивными значениями | без порядка |
| Set | `LinkedHashSet` | хеш-таблица + связный список | порядок вставки |
| Set | `TreeSet` | красно-чёрное дерево | сортировка по `Comparable` или `Comparator`; нет ни того ни другого → `ClassCastException` |
| Queue | `PriorityQueue` | двоичная куча | голова — наименьший по компаратору; при итерации не отсортирована |
| Deque | `ArrayDeque` | кольцевой массив | стек и очередь по умолчанию; без null |
| Map | `HashMap` | массив корзин (buckets) | один null-ключ, любые null-значения; см. [[hashmap-internals]] |
| Map | `LinkedHashMap` | + связный список | порядок вставки или доступа (LRU-кэши через `removeEldestEntry`) |
| Map | `TreeMap` | красно-чёрное дерево | отсортированные ключи, запросы по диапазону |
| Map | `Hashtable` | синхронизированная | legacy, без null |
| Map | `WeakHashMap`, `IdentityHashMap` | особые ключи | см. [[hashmap-internals]] |

### Сложность (Big O)
Big O описывает, как растут время или память с ростом размера входа n. O(1) — константа, O(log n) — логарифм (деревья, двоичный поиск), O(n) — линейная, O(n log n) — сортировка, O(n²) — вложенные циклы.

| | get | add | contains | remove |
|---|---|---|---|---|
| `ArrayList` | O(1) | O(1) амортизированно, O(n) в середине | O(n) | O(n) |
| `LinkedList` | O(n) | O(1) на концах | O(n) | O(1) через итератор |
| `HashMap` / `HashSet` | O(1) в среднем | O(1) в среднем | O(1) в среднем | O(1) в среднем |
| `TreeMap` / `TreeSet` | O(log n) | O(log n) | O(log n) | O(log n) |
| `PriorityQueue` | peek O(1) | O(log n) | O(n) | poll O(log n) |

### Утилиты и обёртки
- `Collections` и `Arrays` — статические вспомогательные классы (`sort`, `binarySearch`, `shuffle`, `asList`), не путать с типом `Collection` и массивами.
- Представления только для чтения: `Collections.unmodifiableList/Set/Map(x)` (представление: оригинал всё ещё может меняться) и неизменяемые `List.of`, `Set.of`, `Map.of`, `List.copyOf` (Java 9+, отвергают null).
- Синхронизированные обёртки `Collections.synchronizedList/Map(x)`: одна глобальная блокировка, итерация всё равно требует ручной блокировки. Лучше [[concurrent-collections]].
- Разнородные коллекции (элементы разных типов) возможны только с `Object` или сырыми типами (raw types); см. [[generics]].

### Comparable и Comparator
- `Comparable<T>`: **естественный** порядок типа, один метод `compareTo(T)`, который реализует сам класс (`String`, числа, `LocalDate`).
- `Comparator<T>`: **внешний** порядок, `compare(a, b)`, их может быть сколько угодно. Строятся так: `Comparator.comparing(User::lastName).thenComparing(User::age).reversed()`.

## Пример
```java
record Order(String id, Instant created, BigDecimal total) {}

List<Order> orders = new ArrayList<>(repo.findAll());
orders.sort(Comparator.comparing(Order::created).reversed());

Map<String, List<Order>> byDay = new TreeMap<>();              // отсортированные ключи
for (Order o : orders)
    byDay.computeIfAbsent(o.created().toString().substring(0, 10), k -> new ArrayList<>()).add(o);

Deque<Order> stack = new ArrayDeque<>();                        // не java.util.Stack
stack.push(orders.get(0));
```

## Подводные камни
- `Arrays.asList(arr)` — представление фиксированного размера поверх массива; `add` бросает исключение. `List.of` полностью неизменяем.
- Изменение полей элемента после того, как он положен в `HashSet` или стал ключом `HashMap`, теряет его (меняется хеш).
- `TreeSet` считает элементы с `compareTo == 0` дубликатами, даже если `equals` говорит иначе.
- `LinkedList` почти никогда не правильный выбор списка; его единственное преимущество — удаление за O(1) через итератор.
