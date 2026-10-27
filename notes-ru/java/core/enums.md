---
title: Перечисления (enum)
tags: [enum, constants, singleton, enummap, switch]
learned: 2026-10-22
---
## Кратко
`enum` — это класс с фиксированным набором именованных экземпляров, которые JVM создаёт при инициализации класса. Каждая константа — синглтон, поэтому `==` безопасно; у перечислений могут быть поля, конструкторы, методы, они могут реализовывать интерфейсы и задавать каждой константе собственное поведение. Они заменили шаблон «int-констант» времён до Java 5, который не был ни типобезопасным, ни читаемым при выводе.

## Основное
### Что даёт enum
- Каждое перечисление неявно расширяет `java.lang.Enum<E>` и поэтому является `final` (кроме того, что константы с телом — анонимные подклассы) и не может расширять другой класс. Оно может реализовывать сколько угодно интерфейсов.
- Конструкторы неявно `private`; `new` запрещён, как и создание экземпляров через рефлексию и десериализацию. JVM создаёт константы один раз при статической инициализации, в порядке объявления ([[class-initialization-order]]). Это делает enum самым простым корректным синглтоном (Effective Java, item 3, [[creational-patterns]]): потокобезопасным, устойчивым к сериализации и к рефлексии.
- Встроенные методы: `name()`, `ordinal()` (позиция, с 0), `values()` (статический, при каждом вызове новый массив, в порядке объявления), `valueOf(String)` (бросает `IllegalArgumentException` на неизвестное имя), `compareTo` (по ordinal; перечисления реализуют `Comparable`), `toString()` (по умолчанию возвращает имя, можно переопределить), `getDeclaringClass()`.
- `equals` и `hashCode` финальные и основаны на идентичности; `==` и `equals` эквивалентны, причём `==` безопасно к null.

### До enum
- В Java 1–4 использовали `public static final int STATUS_ACTIVE = 1;`: нет типобезопасности (проходит любой `int`), нет пространства имён, бессмысленно в логах, ломается при перестановке значений без перекомпиляции клиентов (константы встраиваются). Шаблон типобезопасного перечисления (класс с приватным конструктором и public static final экземплярами) был рукописным предшественником; Java 5 сделала его возможностью языка вместе с `EnumSet`, `EnumMap` и `switch` по перечислениям.

### Поведение для каждой константы
- Поля и конструктор дают каждой константе данные: `KGS("som", 2)`.
- Абстрактный метод в перечислении должен быть реализован каждой константой в её теле (`PLUS { int apply(int a, int b) { return a + b; } }`); как вариант, можно передать лямбду в конструктор — это короче.
- `switch` по перечислению: начиная с Java 14 стрелочная форма `case A, B -> ...` является выражением; с `sealed`-типами и pattern matching (21) компилятор требует исчерпываемости, поэтому новая константа становится ошибкой компиляции, а не молчаливым проваливанием в `default`.

### Коллекции
- `EnumSet` (битовый вектор) и `EnumMap` (массив, индексируемый по ordinal) — самые быстрые множество и словарь для ключей-перечислений; используй их вместо `HashSet`/`HashMap` ([[collections-framework]]).

### Хранение и сериализация
- JPA: всегда `@Enumerated(EnumType.STRING)`; `ORDINAL` по умолчанию ломается, когда константы переставляют или вставляют новые ([[jpa-entities-and-relationships]]).
- Jackson сериализует по `name()`; `@JsonValue` на поле меняет это. Неизвестное входящее значение приводит к ошибке, если не настроен `READ_UNKNOWN_ENUM_VALUES_AS_NULL` или fallback через `@JsonCreator`.
- Стандартная сериализация Java записывает только имя, поэтому синглтоны на enum её переживают.

## Пример
```java
public enum SubscriptionStatus {
    PENDING("pending", false),
    ACTIVE("active", true),
    SUSPENDED("suspended", true),
    TERMINATED("terminated", false);

    private final String code;
    private final boolean billable;

    SubscriptionStatus(String code, boolean billable) { this.code = code; this.billable = billable; }

    public String code() { return code; }
    public boolean billable() { return billable; }

    public boolean canTransitionTo(SubscriptionStatus next) {
        return switch (this) {
            case PENDING    -> next == ACTIVE || next == TERMINATED;
            case ACTIVE     -> next == SUSPENDED || next == TERMINATED;
            case SUSPENDED  -> next == ACTIVE || next == TERMINATED;
            case TERMINATED -> false;
        };                                                   // исчерпывающий: default не нужен
    }

    private static final Map<String, SubscriptionStatus> BY_CODE =
            Arrays.stream(values()).collect(Collectors.toUnmodifiableMap(s -> s.code, s -> s));

    public static SubscriptionStatus fromCode(String code) {
        var s = BY_CODE.get(code);
        if (s == null) throw new IllegalArgumentException("unknown status: " + code);
        return s;
    }
}
```

## Подводные камни
- Хранение или раскрытие `ordinal()` (колонки БД, форматы передачи данных) привязывает внешние данные к порядку объявления; используй `name()` или явный код.
- `values()` клонирует массив при каждом вызове; на горячих путях кешируй его в статическом поле.
- Статическое поле перечисления нельзя читать из конструктора, потому что константы инициализируются раньше остальных статических полей; строй словари для поиска в статическом блоке или лениво, как выше.
- `switch` с `default` скрывает пропущенные случаи при добавлении константы; убирай `default` в switch-выражениях, чтобы получить ошибку компиляции.
- Связанные заметки: [[abstract-classes-and-interfaces]], [[creational-patterns]], [[immutable-objects]].
