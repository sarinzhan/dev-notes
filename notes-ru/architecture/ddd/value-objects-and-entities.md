---
title: Объекты-значения и сущности
tags: [ddd, java, immutability, jpa]
learned: 2026-10-27
---
## Кратко
**Сущность** (entity) определяется своей идентичностью: `Sim #7f3a` остаётся тем же объектом, пока меняются её MSISDN, статус и профили. **Объект-значение** (value object) определяется только своими атрибутами: два `Money(100, KGS)` взаимозаменяемы, поэтому у него нет id, он неизменяем и валидирует себя при создании. Моделирование `Iccid`, `Msisdn`, `Money`, `DateRange` как объектов-значений вместо `String`/`BigDecimal` переносит валидацию в одно место, делает невалидные состояния непредставимыми и даёт компилятору что проверять. Java records — естественная форма объекта-значения.

## Основное

### Сущность и объект-значение
| | Сущность | Объект-значение |
|---|---|---|
| Равенство | По id (`equals` только по идентификатору) | По всем атрибутам (`equals` у record) |
| Изменяемость | Меняется со временем, идентичность та же | Неизменяемый; «изменение» возвращает новый экземпляр |
| Жизненный цикл | Создаётся, сохраняется, загружается, удаляется | Создаётся и выбрасывается; репозитория нет |
| Примеры | `Sim`, `Order`, `Subscriber` | `Iccid`, `Money`, `Address`, `Period`, `EmailAddress` |
| В JPA | `@Entity` с `@Id`, `@Version` | `@Embeddable`, `@ElementCollection` или `AttributeConverter` |
- Одно и то же понятие может быть и тем, и другим в зависимости от контекста: `Address` — значение в транспортной этикетке и сущность в системе почтовой службы, которая хранит историю адресов ([[bounded-contexts-and-context-mapping]]).
- Id сущностей: генерировать в приложении (UUID v7 для упорядочивания по времени или ULID), чтобы у агрегата был id до попадания в базу и события могли на него ссылаться ([[time-money-and-ids]]).

### Почему объекты-значения окупаются
- **Валидация при создании**: `new Msisdn("996 555")` бросает исключение один раз; каждый метод, принимающий `Msisdn`, может ему доверять. Сравни с проверкой `msisdn.matches(...)` в пяти сервисах.
- **Типобезопасность**: `transfer(Msisdn from, Iccid to)` нельзя вызвать с перепутанными аргументами; `transfer(String, String)` можно, и никто не заметит до продакшена.
- **Поведение рядом с данными**: `money.plus(other)` проверяет валюту; `period.overlaps(other)`; `iccid.checkDigitValid()`. Никаких `MoneyUtils`.
- **Неизменяемость** делает их безопасными для разделения между потоками и использования в качестве ключей map ([[immutable-objects]], [[equals-and-hashcode]]).
- Деньги: сумма `BigDecimal` + `Currency`, масштаб фиксирован для каждой валюты, никогда `double` ([[numbers-bits-and-floating-point]]).

### Равенство сущностей
- `equals`/`hashCode` реализуются только по id и только когда id присваивается при создании; с id, генерируемыми базой, сущность в `HashSet` меняет хэш после `persist` и теряется. Совет Hibernate: id, присваиваемые приложением, или `equals` по естественному бизнес-ключу.
- Не использовать Lombok `@Data` на сущностях: он генерирует `equals` по всем полям, включая ленивые коллекции (вызывает загрузки, ломается на прокси) ([[jpa-entities-and-relationships]]).

### Объекты-значения в хранении
- `@Embeddable record`: Hibernate 6.2+ отображает records как embeddable; `Money` становится двумя колонками `amount`, `currency`.
- Значения в одной колонке (`Iccid`, `Msisdn`): `@Converter(autoApply = true) class IccidConverter implements AttributeConverter<Iccid, String>` оставляет колонку обычным `varchar`, а тип в Java — строгим.
- Коллекции значений: `@ElementCollection` загружается и заменяется целиком; нормально для нескольких строк на владельца, неправильно для тысяч ([[aggregates-and-invariants]]).
- На границе API объекты-значения сериализуются как их примитив (`"iccid": "8999..."`), а не как вложенные объекты, если форма не важна; это делает пара Jackson `@JsonValue`/`@JsonCreator`.

### Уровни валидации
- Синтаксическая валидация (`@NotBlank`, `@Pattern`) на request DTO ([[spring-mvc-validation-and-uploads]]) даёт понятные сообщения 400. Конструкторы объектов-значений валидируют ещё раз: проверка DTO — это UX, конструктор — гарантия.
- Бизнес-валидация («этот ICCID принадлежит этому абоненту») — инвариант агрегата, а не забота объекта-значения.

## Пример
Два объекта-значения с валидацией и поведением и конвертер, который сохраняет один из них.

```java
public record Iccid(String value) {
    private static final Pattern FORMAT = Pattern.compile("89\\d{17,18}");

    public Iccid {
        Objects.requireNonNull(value, "iccid");
        value = value.strip();
        if (!FORMAT.matcher(value).matches()) throw new IllegalArgumentException("invalid ICCID: " + value);
        if (!luhnValid(value)) throw new IllegalArgumentException("ICCID check digit mismatch");
    }

    public String issuer() { return value.substring(2, 7); }     // MII + страна + id эмитента

    private static boolean luhnValid(String digits) {
        int sum = 0;
        boolean dbl = false;
        for (int i = digits.length() - 1; i >= 0; i--) {
            int d = digits.charAt(i) - '0';
            if (dbl) { d *= 2; if (d > 9) d -= 9; }
            sum += d;
            dbl = !dbl;
        }
        return sum % 10 == 0;
    }
}

public record Money(BigDecimal amount, Currency currency) {
    public Money {
        Objects.requireNonNull(amount); Objects.requireNonNull(currency);
        amount = amount.setScale(currency.getDefaultFractionDigits(), RoundingMode.HALF_EVEN);
    }
    public static Money of(String amount, String currency) { return new Money(new BigDecimal(amount), Currency.getInstance(currency)); }
    public Money plus(Money o) {
        if (!currency.equals(o.currency)) throw new IllegalArgumentException("currency mismatch " + currency + "/" + o.currency);
        return new Money(amount.add(o.amount), currency);
    }
    public boolean isNegative() { return amount.signum() < 0; }
}

@Converter(autoApply = true)
public class IccidConverter implements AttributeConverter<Iccid, String> {
    @Override public String convertToDatabaseColumn(Iccid iccid) { return iccid == null ? null : iccid.value(); }
    @Override public Iccid convertToEntityAttribute(String db) { return db == null ? null : new Iccid(db); }
}
```

## Подводные камни
- «Одержимость примитивами» (primitive obsession): `String iccid, String msisdn, String eid` в каждой сигнатуре; перепутать их — ошибка времени выполнения, которую могла бы поймать система типов.
- Объект-значение с сеттером или изменяемым полем (`List` без `List.copyOf`) — не объект-значение; два равных объекта могут разойтись.
- Валидация только в DTO: Kafka listener и планировщик обходят контроллер и подают в домен мусор.
- Сущности, сравниваемые через `==` между разными контекстами персистентности, или `equals` по изменяемым полям: принадлежность к `Set<Sim>` молча меняется.
- Связанные заметки: [[ddd-basics]], [[aggregates-and-invariants]], [[immutable-objects]], [[immutability-and-side-effects]].
