---
title: Iterator, Iterable и fail-fast против fail-safe
tags: [iterator, iterable, fail-fast, concurrentmodificationexception]
learned: 2026-10-22
---
## Кратко
`Iterable` — это «то, из чего можно получить `Iterator`», а `for-each` — синтаксический сахар ровно над этим. Большинство итераторов `java.util` — **fail-fast**: они бросают `ConcurrentModificationException`, если коллекцию структурно изменили у них за спиной. Конкурентные коллекции дают **fail-safe** итераторы, которые работают со снимком и никогда не бросают исключение.

## Основное
### Интерфейсы
- `Iterator<E>`: `hasNext()`, `next()` (возвращает текущий элемент и продвигается дальше), `remove()` (удаляет последний возвращённый элемент; единственный безопасный способ удалять во время итерации).
- `ListIterator<E>` расширяет `Iterator`: ходит в обе стороны (`hasPrevious`, `previous`), знает индекс и умеет `add()` и `set()`, а не только `remove()`.
- `Iterable<E>`: один метод `iterator()`; его реализация позволяет использовать любой класс в `for (E e : obj)`. Компилятор превращает цикл в `Iterator it = obj.iterator(); while (it.hasNext()) …`.
- `Enumeration`: версия до 1.2 (`hasMoreElements`, `nextElement`), которую до сих пор отдают `Vector`, `Stack`, `Hashtable`. Нет `remove`, имена длиннее. Legacy.

### Fail-fast
- Fail-fast в общем смысле: останавливаться сразу при обнаружении ошибки (или состояния, ведущего к ней), а не продолжать с испорченными данными.
- Реализация: коллекция хранит `modCount`, который меняется при каждом структурном изменении (add/remove, но не `set`). Итератор копирует его при создании и сравнивает при каждом `next()`; несовпадение бросает `ConcurrentModificationException`.
- Это проверка по мере возможности (best-effort), а не гарантия: однопоточные баги она ловит надёжно, многопоточные — лишь иногда.

### Fail-safe
- Итераторы `CopyOnWriteArrayList` (снимок массива) и представлений `ConcurrentHashMap` (слабо согласованные: отражают некоторые, возможно не все, конкурентные изменения) никогда не бросают исключение. Цена: можно проитерировать устаревшие данные. См. [[concurrent-collections]].

## Пример
```java
List<String> names = new ArrayList<>(List.of("a", "b", "c"));

for (String n : names)                 // ConcurrentModificationException на следующем шаге
    if (n.equals("b")) names.remove(n);

Iterator<String> it = names.iterator(); // правильно
while (it.hasNext())
    if (it.next().equals("b")) it.remove();

names.removeIf(n -> n.equals("b"));     // проще всего
```

## Подводные камни
- Удаление внутри `for-each` — классический CME; используй `Iterator.remove`, `removeIf` или итерируй по копии.
- Изменение `HashMap` во время итерации по её `keySet()` тоже бросает исключение; исправления — `entrySet().removeIf` или `ConcurrentHashMap`.
- `Collections.synchronizedList` не делает итерацию безопасной; блокируй вокруг цикла или используй конкурентную коллекцию.
- Место всего этого во фреймворке: [[collections-framework]].
