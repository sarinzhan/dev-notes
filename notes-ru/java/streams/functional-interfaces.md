---
title: Функциональные интерфейсы и лямбды
tags: [functional, lambda, method-reference, java.util.function]
learned: 2026-10-22
---
## Кратко
У функционального интерфейса ровно один абстрактный метод, и именно это позволяет подставить вместо него лямбду или ссылку на метод. `java.util.function` поставляет стандартные формы (`Function`, `Predicate`, `Consumer`, `Supplier` и родственные); `@FunctionalInterface` заставляет компилятор следить за правилом одного метода.

## Основное
### Стандартные формы
| Интерфейс | Сигнатура | Смысл |
|---|---|---|
| `Function<T,R>` | `R apply(T)` | преобразовать |
| `BiFunction<T,U,R>` | `R apply(T,U)` | объединить два |
| `UnaryOperator<T>` | `T apply(T)` | тот же тип на входе и выходе |
| `BinaryOperator<T>` | `T apply(T,T)` | свести два значения одного типа |
| `Predicate<T>` | `boolean test(T)` | фильтр |
| `Consumer<T>` | `void accept(T)` | побочный эффект |
| `BiConsumer<T,U>` | `void accept(T,U)` | например, `Map.forEach` |
| `Supplier<T>` | `T get()` | ленивое значение, фабрика |
| `Runnable` / `Callable<V>` | `void run()` / `V call()` | задачи, см. [[threads-basics]] |
Примитивные варианты избегают упаковки: `IntPredicate`, `IntFunction<R>`, `ToIntFunction<T>`, `IntUnaryOperator`, `IntSupplier` и т. д.

### Композиция
- `Function`: `andThen`, `compose`, `Function.identity()`.
- `Predicate`: `and`, `or`, `negate`, `Predicate.not(p)` (Java 11).
- `Comparator` — тоже функциональный интерфейс: `comparing`, `thenComparing`, `reversed`.

### Лямбды и ссылки на методы
- Лямбда `(a, b) -> a + b` преобразуется в тот функциональный интерфейс, который ожидает контекст (целевая типизация, target typing).
- Ссылки на методы: статический `Integer::parseInt`, связанный метод экземпляра `str::length`, несвязанный метод экземпляра `String::length`, конструктор `ArrayList::new`.
- Захваченные локальные переменные должны быть effectively final; у лямбд нет собственного `this` (в отличие от анонимных классов).
- Default- и static-методы в функциональном интерфейсе допустимы; значение имеет только количество абстрактных методов. Функциональный интерфейс может расширять другой интерфейс, если у него по-прежнему один абстрактный метод.

## Пример
```java
@FunctionalInterface
interface Validator<T> {
    Optional<String> validate(T value);

    default Validator<T> and(Validator<T> other) {
        return v -> validate(v).or(() -> other.validate(v));
    }
}

Validator<String> notBlank = s -> s.isBlank() ? Optional.of("blank") : Optional.empty();
Validator<String> maxLen   = s -> s.length() > 20 ? Optional.of("too long") : Optional.empty();
Optional<String> error = notBlank.and(maxLen).validate(input);
```

## Подводные камни
- Проверяемые исключения не могут вылететь из лямбды, реализующей стандартные интерфейсы; оборачивай их или объяви собственный интерфейс с `throws`.
- `Function<Integer,Integer>` упаковывает значения при каждом вызове; в горячих циклах используй `IntUnaryOperator`.
- Длинные лямбды ухудшают читаемость и стек-трейсы; вынеси метод и используй ссылку на него.
- Используются повсюду в [[stream-api]] и в [[completable-future]].
