---
title: Конструкторы и инициализация объектов
tags: [constructors, initialization, builder, records, object-creation]
learned: 2026-10-22
---
## Кратко
Конструктор — это метод с именем класса и без типа возврата, который выполняется, когда `new` создаёт объект; его задача — оставить объект в корректном состоянии. Первой инструкцией всегда идёт вызов другого конструктора (`this(...)`) или конструктора суперкласса (`super(...)`, вставляется неявно). Records и билдеры — современные способы избежать длинных цепочек конструкторов.

## Основное
### Основы
- Объявляется как метод без типа возврата; может быть перегружен; может иметь любой модификатор доступа (`private` — для синглтонов и фабрик, см. [[creational-patterns]]).
- Если в классе не объявлено ни одного конструктора, компилятор добавляет публичный **конструктор по умолчанию** без аргументов, который вызывает `super()`. Как только объявлен любой конструктор, конструктор по умолчанию исчезает; фреймворки, которым нужен конструктор без аргументов (сущности JPA, Jackson без `@JsonCreator`), после этого падают.
- Конструктор — это функция, инициализирующая объект; он не наследуется, поэтому подкласс обязан объявить свой, если у родителя нет доступного конструктора без аргументов.
- Конструкторы не могут быть `abstract`, `static`, `final` или `synchronized` (объект ещё никому не доступен). Они могут объявлять `throws`.

### Цепочки вызовов
- `this(args)` делегирует другому конструктору того же класса; `super(args)` — родителю. Любой из них должен быть **первой инструкцией** (preview-возможность Java 22 «flexible constructor bodies» ослабляет это правило и разрешает перед ним инструкции, не трогающие `this`).
- Если не написано ни то, ни другое, подразумевается `super()`; если у родителя нет доступного конструктора без аргументов, код не компилируется.
- Полный порядок событий при `new`: см. [[class-initialization-order]] (поля и блоки родителя → конструктор родителя → поля и блоки наследника → конструктор наследника).

### Что происходит внутри `new`
1. JVM выделяет память в куче ([[jvm-memory-areas]]), и каждое поле получает значение по умолчанию (`0`, `false`, `null`); JIT может выделить объект на стеке, если escape analysis доказывает, что объект не убегает.
2. Выполняются инициализаторы экземпляра, затем тело конструктора.
3. Возвращается ссылка. Как только конструктор завершился, `final`-поля безопасно публикуются для всех потоков ([[java-memory-model]]).

### Другие способы создать объект
- Статический фабричный метод (`LocalDate.of`, `List.of`): имеет имя, может возвращать кешированные экземпляры или подтипы, может скрывать реализацию.
- `Class.getDeclaredConstructor().newInstance()` через [[object-class-and-reflection]] (так делают Spring и Hibernate).
- `clone()` ([[object-class-and-reflection]]) и десериализация полностью обходят конструкторы.
- **Records** (Java 16): `record Point(int x, int y)` генерирует канонический конструктор, методы доступа, `equals`/`hashCode`/`toString`. *Компактный конструктор* `Point { if (x < 0) throw ...; }` проверяет или нормализует значения без повторения присваиваний.
- **Билдер (builder)** — для классов с множеством необязательных параметров; Lombok `@Builder` или вручную. Он предпочтительнее «телескопических» перегрузок конструктора.

## Пример
```java
public final class EsimProfile {
    private final String iccid;
    private final String activationCode;
    private final Instant createdAt;
    private final List<String> tags;

    private EsimProfile(Builder b) {                       // private: создаёт только билдер
        this.iccid = Objects.requireNonNull(b.iccid, "iccid");
        this.activationCode = Objects.requireNonNull(b.activationCode, "activationCode");
        this.createdAt = b.createdAt != null ? b.createdAt : Instant.now();
        this.tags = List.copyOf(b.tags);                   // защитная копия
    }

    public static Builder builder() { return new Builder(); }

    public static final class Builder {
        private String iccid, activationCode;
        private Instant createdAt;
        private final List<String> tags = new ArrayList<>();

        public Builder iccid(String v) { iccid = v; return this; }
        public Builder activationCode(String v) { activationCode = v; return this; }
        public Builder createdAt(Instant v) { createdAt = v; return this; }
        public Builder tag(String v) { tags.add(v); return this; }
        public EsimProfile build() { return new EsimProfile(this); }
    }
}

// Та же идея в одну строку для простого значения:
record Msisdn(String value) {
    Msisdn { if (!value.matches("996\\d{9}")) throw new IllegalArgumentException(value); }
}
```

## Подводные камни
- Вызов переопределяемого метода из конструктора выполняет код подкласса на недостроенном объекте (поля подкласса ещё `null`).
- Утечка `this` из конструктора (регистрация слушателя, запуск потока с `this`) публикует объект до окончания создания; другие потоки могут увидеть значения полей по умолчанию.
- Сохранение изменяемой коллекции или `Date` вызывающего кода напрямую, без копирования, ломает неизменяемость ([[immutable-objects]]).
- Тяжёлая работа в конструкторах (ввод-вывод, обращения к БД) делает классы нетестируемыми; конструкторы должны присваивать, проверять — и ничего больше. Внедрение через конструктор в Spring ([[spring-ioc-and-di]]) следует этому правилу.
- Связанные заметки: [[class-initialization-order]], [[creational-patterns]], [[modifiers-final-static-access]].
