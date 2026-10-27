---
title: Неизменяемые объекты
tags: [immutability, final, defensive-copy, records, thread-safety]
learned: 2026-10-22
---
## Кратко
Неизменяемый (immutable) объект никогда не меняет состояние после создания. Рецепт: `final`-класс, поля `private final`, всё задаётся в конструкторе, никаких сеттеров, защитные копии изменяемых входных данных на входе и изменяемых полей на выходе (или неизменяемые представления). Неизменяемые объекты потокобезопасны бесплатно, безопасны как ключи словарей и тривиально кешируются; `record` даёт большую часть рецепта в одну строку.

## Основное
### Рецепт
1. Объяви класс `final`, чтобы подкласс не мог добавить изменяемое состояние или переопределить методы (так делают `String`, обёртки, `LocalDate`). Альтернатива: приватный конструктор плюс статические фабрики, что тоже запрещает наследование.
2. Сделай каждое поле `private final`. Финальные поля к тому же получают гарантию безопасной инициализации JMM: любой поток, который видит объект, видит полностью построенные поля без синхронизации ([[java-memory-model]]).
3. Инициализируй всё в конструкторе (или через билдер, который его вызывает, см. [[constructors-and-initialization]]). Там же проверяй значения.
4. Никаких сеттеров и методов, изменяющих поля. «Изменяющие» операции возвращают новый экземпляр (`plus`, `withStatus`).
5. **Защитные копии**: Java передаёт ссылки по значению, поэтому вызывающий код по-прежнему держит переданный тебе `List` или `Date`. Копируй на входе (`List.copyOf(input)`, `new ArrayList<>(input)`, `date.toInstant()`), а на выходе возвращай копию, неизменяемое представление (`Collections.unmodifiableList`) или неизменяемую коллекцию.
6. Поля изменяемых типов (`StringBuilder`, массивы, `Date`) никогда не должны утекать; для полей предпочитай неизменяемые типы (`String`, `Instant`, `List.of`, собственные value-объекты).

### Что это даёт
- Потокобезопасность без блокировок; разделение между потоками и кеширование (кеш `Integer`, пул строк) возможны только потому, что никто не может изменить общий экземпляр.
- Безопасные ключи `HashMap`/`HashSet`: хеш не может измениться после вставки ([[equals-and-hashcode]]).
- Атомарность при сбое: операция, бросившая исключение, не может оставить объект наполовину обновлённым.
- Проще рассуждать и тестировать; `equals` и `hashCode` стабильны.
- Цена: больше выделений памяти на «изменённые копии». Escape analysis в JIT и сборщик молодого поколения делают короткоживущие объекты дешёвыми ([[garbage-collection]]), так что это редко важно; для горячего пути запасной выход — изменяемый билдер (`StringBuilder` рядом со `String`).

### Records (Java 16)
- `record Range(Instant from, Instant to)` неявно `final`, поля `private final`, есть канонический конструктор, методы доступа, `equals`/`hashCode`/`toString`. Компактный конструктор проверяет значения и может заменять компоненты (`from = from.truncatedTo(SECONDS)`), в том числе делать защитные копии.
- Records неизменяемы *поверхностно*: `record Config(List<String> hosts)` по-прежнему раскрывает список вызывающего кода, если компактный конструктор не делает `hosts = List.copyOf(hosts)`.

### Неизменяемые коллекции
- `List.of`, `Set.of`, `Map.of`, `List.copyOf` (Java 9/10): по-настоящему неизменяемы, отвергают элементы `null`, бросают `UnsupportedOperationException` при попытке изменения. Их создают `Collectors.toUnmodifiableList` (10) и `Stream.toList()` (16).
- `Collections.unmodifiableList(x)` — это *представление*: исходный список всё ещё может меняться через оригинальную ссылку.

### В JDK
- `String`, все обёртки ([[primitives-and-wrappers]]), `BigDecimal`/`BigInteger`, типы `java.time`, `Optional`, `UUID`, `Path`. Старые `Date` и `Calendar` изменяемы, и это одна из причин появления `java.time`.

## Пример
```java
public final class TopUp {
    private final String msisdn;
    private final long amountMinor;
    private final Instant at;
    private final List<String> channels;          // изменяемый тип -> копия на входе, неизменяемый на выходе

    public TopUp(String msisdn, long amountMinor, Instant at, List<String> channels) {
        if (amountMinor <= 0) throw new IllegalArgumentException("amount must be positive");
        this.msisdn = Objects.requireNonNull(msisdn);
        this.amountMinor = amountMinor;
        this.at = Objects.requireNonNull(at);
        this.channels = List.copyOf(channels);    // защитная копия; List.copyOf уже неизменяем
    }

    public String msisdn() { return msisdn; }
    public long amountMinor() { return amountMinor; }
    public Instant at() { return at; }
    public List<String> channels() { return channels; }   // безопасно: результат List.copyOf нельзя изменить

    public TopUp withAmount(long newAmountMinor) {         // «изменение» возвращает новый объект
        return new TopUp(msisdn, newAmountMinor, at, channels);
    }
}

// То же самое через record и компактный конструктор:
record TopUpRecord(String msisdn, long amountMinor, Instant at, List<String> channels) {
    TopUpRecord {
        if (amountMinor <= 0) throw new IllegalArgumentException("amount must be positive");
        channels = List.copyOf(channels);
    }
}
```

## Подводные камни
- `final` на поле замораживает ссылку, а не объект: `private final List<String> tags = new ArrayList<>()` полностью изменяем через `getTags().add(...)`.
- Возврат массива из геттера приводит к утечке: массивы всегда изменяемы; клонируй его или возвращай `List`.
- Jackson и JPA нужен способ создать объект: для Jackson используй `@JsonCreator`/records; сущности JPA не могут быть неизменяемыми в строгом смысле (Hibernate нужен конструктор без аргументов и доступ к полям), поэтому value-объекты держи неизменяемыми, а сущности — изменяемыми, но инкапсулированными ([[jpa-entities-and-relationships]]).
- «Почти неизменяемому» классу с одним лениво вычисляемым полем-кешем нужно, чтобы это поле было `volatile` или вычислялось под блокировкой; `String.hash` безопасен только потому, что запись `int` атомарна, а повторное вычисление идемпотентно.
- Связанные заметки: [[strings-and-string-pool]], [[equals-and-hashcode]], [[concurrency-hazards]].
