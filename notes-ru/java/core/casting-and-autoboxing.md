---
title: Приведение типов, instanceof и автоупаковка
tags: [casting, autoboxing, instanceof, pattern-matching, type-conversion]
learned: 2026-10-22
---
## Кратко
Java — статически типизированный язык: у каждого выражения есть тип времени компиляции, а преобразование между типами бывает неявным (расширение, упаковка) или явным (сужение, нисходящее приведение). Восходящее приведение (upcast) и расширение всегда безопасны; нисходящее приведение ссылки (downcast) проверяется во время выполнения и при несовпадении бросает `ClassCastException`, поэтому его защищают проверкой `instanceof`, лучше в форме pattern matching. Автоупаковка (autoboxing) скрывает преобразование примитива в обёртку, а вместе с ним — проверку на null и выделение памяти.

## Основное
### Виды преобразований
| Преобразование | Примитивы | Ссылки | Явное? | Риск |
|---|---|---|---|---|
| Тождественное | `int` → `int` | `Dog` → `Dog` | нет | нет |
| Расширение / upcast | `int` → `long`, `float` → `double` | `Dog` → `Animal`, `ArrayList` → `List` | нет | `long` → `float`/`double` и `int` → `float` могут молча потерять точность |
| Сужение / downcast | `long` → `int`, `double` → `int` | `Animal` → `Dog` | да, `(int) x` | примитивы: усечение, переполнение по кругу; ссылки: `ClassCastException` |
| Упаковка / распаковка | `int` ↔ `Integer` | | нет | `NullPointerException` при распаковке `null` |
| Преобразование в строку | что угодно + `""` | | нет | скрытая стоимость `toString` |

- Нисходящее приведение не меняет объект: оно лишь говорит компилятору считать ссылку подтипом. JVM проверяет реальный класс во время выполнения (`checkcast`).
- Порядок расширения примитивов: `byte → short → int → long → float → double`; `char → int`. Арифметика над `byte`/`short`/`char` повышает тип до `int`, поэтому `byte b = a + 1` требует приведения (если `a` не константа).
- `(int) 3.99` равно `3` (усечение к нулю), `(int) Double.NaN` равно `0`, `(int) 1e20` насыщается до `Integer.MAX_VALUE`; `(byte) 200` равно `-56` (переполнение по кругу).
- Дженерики: приведение к параметризованному типу (`(List<String>) obj`) непроверяемое (unchecked) из-за стирания типов ([[generics]]); во время выполнения проверяется только сырой тип.

### instanceof и pattern matching
- `x instanceof T` равно `true`, когда `x` не null и его класс во время выполнения — `T` или подтип; `null instanceof T` равно `false`.
- Java 16: `if (o instanceof Dog d)` связывает `d` после проверки, и приведение не нужно. Java 21: `switch` с шаблонами типов и деконструкцией record (`case Card(String pan) -> ...`), исчерпывающий по `sealed`-иерархиям ([[abstract-classes-and-interfaces]]).
- Цепочки `instanceof` обычно говорят о том, что метод должен принадлежать типу (полиморфизм, [[oop-principles]]) или что sealed-иерархия со switch была бы чище.

### Автоупаковка и распаковка (Java 5)
- Компилятор вставляет `Integer.valueOf(i)`, когда примитив используется там, где ожидается обёртка (присваивание, аргумент метода, элемент коллекции), и `x.intValue()` в обратном случае. Поэтому `List<Integer>.add(5)` компилируется.
- Упаковка выделяет память, если значение не попадает в кеш (`-128..127` для целочисленных типов, [[primitives-and-wrappers]]); распаковка `null` бросает NPE.
- Смешанные сравнения: `Integer == int` распаковывает обёртку; `Integer == Integer` сравнивает ссылки. `Integer == Long` не компилируется. Тернарный оператор смешивает типы: `cond ? 1 : nullInteger` распаковывает и бросает NPE.
- При выборе перегрузки расширение пробуется раньше упаковки: для аргумента `int` будет выбран `m(long)`, а не `m(Integer)` ([[methods-overloading-overriding]]).

### Числовые преобразования помимо приведения
- `Math.toIntExact(long)` — чтобы падать, а не переполняться по кругу; `Math.round` — для округления вместо усечения; `BigDecimal.setScale` — для денег; `Integer.parseInt`/`Long.parseLong` — для строк.
- Методы `Number` (`longValue()`) преобразуют любую обёртку без приведения.

## Пример
```java
sealed interface ChargeEvent permits Debit, Refund {}
record Debit(String msisdn, long amountMinor) implements ChargeEvent {}
record Refund(String msisdn, long amountMinor, String reason) implements ChargeEvent {}

// Старый стиль: приведение после instanceof
static long signedAmountOld(Object event) {
    if (event instanceof Debit) {
        Debit d = (Debit) event;                 // явное нисходящее приведение
        return d.amountMinor();
    }
    throw new IllegalArgumentException(String.valueOf(event));
}

// Java 21: pattern matching, без приведений, исчерпываемость проверяет компилятор
static long signedAmount(ChargeEvent event) {
    return switch (event) {
        case Debit d                       -> d.amountMinor();
        case Refund(var m, var amt, var r) -> -amt;
    };
}

static int percentOf(long amountMinor, double rate) {
    long fee = Math.round(amountMinor * rate);   // double -> long с округлением, а не усечением
    return Math.toIntExact(fee);                 // long -> int, бросает исключение вместо переполнения
}

Map<String, Integer> attempts = new HashMap<>();
int n = attempts.getOrDefault("996555000111", 0);   // избегает NPE при распаковке отсутствующего ключа
```

## Подводные камни
- `int total = map.get(key)` бросает NPE, если ключа нет; используй `getOrDefault` или локальную переменную `Integer` с явной проверкой.
- `long x = intA * intB` переполняется в `int` ещё до расширения; сначала приведи один из операндов: `(long) intA * intB`.
- `List<Integer>.remove(int)` против `remove(Object)`: `list.remove(3)` удаляет элемент с индексом 3; `list.remove(Integer.valueOf(3))` удаляет значение.
- Приведение объекта к интерфейсу, который он не реализует, компилируется, если статический тип не final (компилятор не может исключить подкласс), и падает только во время выполнения.
- Связанные заметки: [[primitives-and-wrappers]], [[generics]], [[oop-principles]].
