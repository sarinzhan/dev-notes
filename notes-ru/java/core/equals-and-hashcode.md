---
title: Контракты equals и hashCode
tags: [equals, hashcode, object-identity, contracts, records]
learned: 2026-10-22
---
## Кратко
`equals` определяет логическое равенство, `hashCode` даёт быструю предварительную проверку, с помощью которой хеш-коллекции находят корзину (bucket). Контракт: у равных объектов хеш-коды должны совпадать (обратное не требуется), поэтому **если переопределяешь `equals`, обязательно переопредели `hashCode`**, и оба должны использовать одни и те же поля. `==` сравнивает ссылки, `equals` — то, что ты определишь.

## Основное
### Контракт equals (JLS / Javadoc `Object.equals`)
- **Рефлексивность**: `x.equals(x)` равно `true`.
- **Симметричность**: `x.equals(y)` тогда и только тогда, когда `y.equals(x)`. Её легко нарушить через `instanceof` при сравнении с подклассом, который добавляет поля.
- **Транзитивность**: из `x.equals(y)` и `y.equals(z)` следует `x.equals(z)`.
- **Согласованность**: повторные вызовы возвращают один и тот же результат, пока объекты не меняются; поэтому нельзя использовать поля, зависящие от времени, случайности или внешнего состояния.
- **Null**: `x.equals(null)` равно `false` (и никогда не бросает исключение). «`== null`» в источнике — опечатка вместо «возвращает false».

### Контракт hashCode
- Повторные вызовы на одном и том же неизменённом объекте возвращают один и тот же `int` в пределах одного запуска JVM (стабильность между запусками или версиями не требуется; для хранения он не предназначен).
- Если `a.equals(b)`, то `a.hashCode() == b.hashCode()`.
- У неравных объектов хеш-код *может* совпадать (коллизия); чем лучше распределены хеши, тем быстрее работает `HashMap` ([[hashmap-internals]]).

### Как их используют коллекции
- `HashMap`/`HashSet`: `hashCode` выбирает корзину; внутри корзины сначала сравнивается сохранённый хеш, затем `equals`. Если переопределить только `equals`, два «равных» ключа попадут в разные корзины: `contains` и `get` их не найдут.
- `TreeMap`/`TreeSet` игнорируют оба метода и используют `compareTo`/`Comparator`; держи `compareTo` согласованным с `equals`, иначе множества будут вести себя странно.
- `List.contains`, `remove(Object)`, `indexOf` используют только `equals`.

### Реализации по умолчанию в Object
- `Object.equals` — это `this == obj`: идентичность ссылок.
- `Object.hashCode` — `native`-метод; HotSpot генерирует identity hash лениво при первом вызове (алгоритм по умолчанию начиная с JDK 8 — потоколокальный ГПСЧ **xorshift**, `-XX:hashCode=5`) и сохраняет его в заголовке объекта (mark word). Это **не** адрес в памяти: объекты перемещаются во время GC ([[garbage-collection]]). Его вычисление «раздувает» заголовок, и это одна из причин, по которой убрали biased locking.
- `System.identityHashCode(o)` возвращает identity hash, даже если `hashCode` переопределён; на нём построен `IdentityHashMap`.

### Как написать их правильно
1. `equals(Object o)`: быстрая проверка идентичности, проверка на `null` и класс, приведение, сравнение значимых полей (`==` для примитивов, `Double.compare` для чисел с плавающей точкой, `Objects.equals` для ссылок, `Arrays.equals` для массивов).
2. `hashCode`: `Objects.hash(f1, f2, ...)` (выделяет массив, вне горячих путей это нормально) или ручная цепочка `31 * h + field`.
3. Используй **одни и те же поля** в обоих методах; предпочитай неизменяемые поля ([[immutable-objects]]).
4. Проверка класса: `getClass() != o.getClass()` сохраняет строгую симметричность, и именно это генерируют IDE; `instanceof` позволяет подклассам быть равными родителям, но ломает симметричность, как только подкласс добавляет состояние. Известное исключение — прокси Hibernate, где нужен `instanceof` (или `Hibernate.getClass`).
5. Или обойтись без всего этого: **records** (Java 16) генерируют оба метода по всем компонентам; Lombok `@EqualsAndHashCode`.

### Сущности JPA
- Не стоит строить `equals` на автоматически сгенерированном id: до первого flush он равен `null`, поэтому хеш сущности меняется, пока она лежит в `HashSet`. Варианты: бизнес-ключ (ICCID, MSISDN), UUID, сгенерированный на клиенте и присвоенный в конструкторе, или семантика идентичности плюс стабильный `hashCode` (константа или на основе класса; так советует документация Hibernate). См. [[jpa-persistence-context-and-entity-lifecycle]].

## Пример
```java
public final class Iccid {
    private final String value;                 // 19-20 цифр, бизнес-идентичность eSIM-профиля

    public Iccid(String value) {
        if (!value.matches("\\d{19,20}")) throw new IllegalArgumentException("bad ICCID: " + value);
        this.value = value;
    }

    @Override public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        return value.equals(((Iccid) o).value);
    }

    @Override public int hashCode() { return value.hashCode(); }   // то же поле, что и в equals

    @Override public String toString() { return value; }
}

// Эквивалент в одну строку, тот же контракт гарантирует компилятор:
record IccidRecord(String value) {}
```

## Подводные камни
- `equals(Iccid other)` вместо `equals(Object)` — это перегрузка, которую `HashMap` никогда не вызывает ([[methods-overloading-overriding]]); всегда ставь `@Override`.
- Изменение поля, участвующего в `hashCode`, пока объект является ключом в `HashMap` или элементом `HashSet`, делает его ненаходимым; запись утекает, пока словарь не будет выброшен.
- Включение лениво загружаемой коллекции JPA или обратной ссылки двунаправленной связи в `equals`/`hashCode` вызывает `LazyInitializationException` или бесконечную рекурсию; Lombok `@Data` на сущностях делает ровно это.
- `Objects.hash` для одного поля упаковывает и выделяет память; для горячих ключей пиши `value.hashCode()` напрямую.
- Связанные заметки: [[hashmap-internals]], [[object-class-and-reflection]], [[strings-and-string-pool]].
