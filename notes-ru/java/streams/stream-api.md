---
title: Stream API, параллельные стримы и gatherers
tags: [streams, functional, parallel-streams, gatherers, java24]
learned: 2026-10-22
---
## Кратко
Стримы (Java 8) декларативно обрабатывают последовательность элементов: строится конвейер (pipeline) из ленивых **промежуточных** операций, а завершается он одной энергичной **терминальной** операцией. Ничего не выполняется до терминального вызова. Параллельные стримы делят работу на общем `ForkJoinPool`; **gatherers** (Java 24) позволяют писать собственные промежуточные операции.

## Основное
### Создание стрима
- Из коллекции: `list.stream()`, `list.parallelStream()`.
- Из значений или массивов: `Stream.of(a, b, c)`, `Arrays.stream(arr)`, `IntStream.range(0, n)`.
- Из строки: `"hello".chars()` (это `IntStream`).
- Бесконечные: `Stream.iterate(seed, f)`, `Stream.generate(supplier)`; их нужно обрезать через `limit` или `takeWhile`.
- Прочие: `Files.lines(path)`, `random.ints()`, `Stream.ofNullable`.

### Промежуточные (ленивые, возвращают стрим)
`filter`, `map`, `mapToInt/Long/Double`, `flatMap` (один элемент → много), `flatMapToInt…`, `distinct`, `sorted`, `peek` (для отладки), `limit`, `skip`, `takeWhile`, `dropWhile` (Java 9), `mapMulti` (Java 16), `gather` (Java 24).

### Терминальные (энергичные, возвращают результат)
`forEach`, `forEachOrdered`, `collect`, `toList` (Java 16), `reduce`, `count`, `min`, `max`, `findFirst`, `findAny`, `anyMatch` / `allMatch` / `noneMatch`, `toArray`, `iterator`.
- `forEach` на параллельном стриме не соблюдает порядок; `forEachOrdered` сохраняет порядок следования (encounter order).
- `Collectors`: `toList`, `toSet`, `toMap`, `groupingBy`, `partitioningBy`, `joining`, `counting`, `summingInt`, `teeing`.

### Примитивные стримы
`IntStream`, `LongStream`, `DoubleStream` избегают упаковки. Они используют специализированные функциональные интерфейсы (`IntPredicate`, `IntFunction`) и добавляют `sum`, `average`, `summaryStatistics`, `boxed`, `mapToObj`.

### Параллельные стримы
- `parallelStream()` или `.parallel()`; `.sequential()` переключает обратно; `isParallel()` сообщает текущий режим. Весь конвейер выполняется в последнем установленном режиме, а не отдельно для каждой операции.
- Данные делит `Spliterator`, куски обрабатываются на общем `ForkJoinPool` ([[fork-join-framework]]), результаты объединяются.
- Имеет смысл, когда: данных много, деление дешёвое (`ArrayList`, массивы, `IntStream.range`; не `LinkedList` и не `iterate`), операции нагружают CPU и не имеют состояния, ядер много. `unordered()` убирает стоимость сохранения порядка.
- Не имеет смысла, когда: данных мало, есть блокирующий ввод-вывод (истощает общий пул), операции с побочными эффектами или `limit`/`findFirst`, зависящие от порядка.

### Gatherers (Java 24, JEP 485)
`Stream.gather(Gatherer)` — точка расширения для собственных промежуточных операций, так же как `collect(Collector)` — для терминальных. Встроенные в `Gatherers`: `windowFixed(n)`, `windowSliding(n)`, `fold`, `scan`, `mapConcurrent(n, f)`. Это та самая возможность, которая была в preview в Java 22–23.

## Пример
```java
record Txn(String account, BigDecimal amount, LocalDate date) {}

Map<String, BigDecimal> totalsPerAccount = txns.stream()
    .filter(t -> t.date().isAfter(since))
    .collect(Collectors.groupingBy(Txn::account,
             Collectors.reducing(BigDecimal.ZERO, Txn::amount, BigDecimal::add)));

List<List<Txn>> batches = txns.stream()
    .gather(Gatherers.windowFixed(100))      // Java 24
    .toList();
```

## Подводные камни
- Стрим можно потребить один раз; повторное использование бросает `IllegalStateException`.
- `peek` — для отладки; если полагаться на него ради побочных эффектов, это ломается при `count()` (который может пропустить конвейер) и в параллельном режиме.
- `Collectors.toMap` бросает исключение на дублирующихся ключах, если не передать функцию слияния.
- `sorted` и `distinct` хранят состояние и буферизуют весь стрим; на бесконечных стримах они никогда не завершаются.
- Функциональные интерфейсы, на которых построен API: [[functional-interfaces]].
