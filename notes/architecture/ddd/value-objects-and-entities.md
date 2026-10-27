---
title: Value objects and entities
tags: [ddd, java, immutability, jpa]
learned: 2026-10-27
---
## TL;DR
An **entity** is defined by its identity: `Sim #7f3a` stays the same object while its MSISDN, status and profiles change. A **value object** is defined only by its attributes: two `Money(100, KGS)` are interchangeable, so it has no id, is immutable, and validates itself at construction. Modelling `Iccid`, `Msisdn`, `Money`, `DateRange` as value objects instead of `String`/`BigDecimal` moves validation to one place, makes invalid states unrepresentable, and gives the compiler something to check. Java records are the natural value object.

## Key points

### Entity vs value object
| | Entity | Value object |
|---|---|---|
| Equality | By id (`equals` on the identifier only) | By all attributes (record `equals`) |
| Mutability | Changes over time, same identity | Immutable; "change" returns a new instance |
| Lifecycle | Created, persisted, loaded, deleted | Created and thrown away; no repository |
| Examples | `Sim`, `Order`, `Subscriber` | `Iccid`, `Money`, `Address`, `Period`, `EmailAddress` |
| In JPA | `@Entity` with `@Id`, `@Version` | `@Embeddable`, `@ElementCollection`, or a `AttributeConverter` |
- The same concept can be either depending on context: an `Address` is a value in a shipping label and an entity in a postal-service system that tracks address history ([[bounded-contexts-and-context-mapping]]).
- Entity ids: generate in the application (UUID v7 for time ordering, or a ULID) so the aggregate has an id before it hits the database and events can reference it ([[time-money-and-ids]]).

### Why value objects pay off
- **Validation at construction**: `new Msisdn("996 555")` throws once; every method that takes a `Msisdn` can trust it. Compare with checking `msisdn.matches(...)` in five services.
- **Type safety**: `transfer(Msisdn from, Iccid to)` cannot be called with the arguments swapped; `transfer(String, String)` can, and nobody notices until production.
- **Behaviour with the data**: `money.plus(other)` checks the currency; `period.overlaps(other)`; `iccid.checkDigitValid()`. No `MoneyUtils`.
- **Immutability** makes them safe to share between threads and to use as map keys ([[immutable-objects]], [[equals-and-hashcode]]).
- Money: `BigDecimal` amount + `Currency`, scale fixed per currency, never `double` ([[numbers-bits-and-floating-point]]).

### Entity equality
- Implement `equals`/`hashCode` on the id only, and only when the id is assigned at construction; with database-generated ids an entity in a `HashSet` changes its hash after `persist` and gets lost. Hibernate's advice: application-assigned ids, or `equals` on a natural business key.
- Do not use Lombok `@Data` on entities: it generates `equals` over all fields including lazy collections (triggers loads, breaks on proxies) ([[jpa-entities-and-relationships]]).

### Value objects in persistence
- `@Embeddable record`: Hibernate 6.2+ maps records as embeddables; a `Money` becomes two columns `amount`, `currency`.
- Single-column values (`Iccid`, `Msisdn`): `@Converter(autoApply = true) class IccidConverter implements AttributeConverter<Iccid, String>` keeps the column a plain `varchar` and the Java type strong.
- Collections of values: `@ElementCollection` is loaded and replaced as a whole; fine for a few rows per owner, wrong for thousands ([[aggregates-and-invariants]]).
- At the API boundary, serialise value objects as their primitive (`"iccid": "8999..."`), not as nested objects, unless the shape matters; a Jackson `@JsonValue`/`@JsonCreator` pair does it.

### Validation layers
- Syntactic validation (`@NotBlank`, `@Pattern`) on the request DTO ([[spring-mvc-validation-and-uploads]]) gives nice 400 messages. Value-object constructors validate again: the DTO check is UX, the constructor is the guarantee.
- Business validation ("this ICCID belongs to this subscriber") is an aggregate invariant, not a value-object concern.

## Example
Two value objects with validation and behaviour, and the converter that stores one of them.

```java
public record Iccid(String value) {
    private static final Pattern FORMAT = Pattern.compile("89\\d{17,18}");

    public Iccid {
        Objects.requireNonNull(value, "iccid");
        value = value.strip();
        if (!FORMAT.matcher(value).matches()) throw new IllegalArgumentException("invalid ICCID: " + value);
        if (!luhnValid(value)) throw new IllegalArgumentException("ICCID check digit mismatch");
    }

    public String issuer() { return value.substring(2, 7); }     // MII + country + issuer id

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

## Pitfalls
- "Primitive obsession": `String iccid, String msisdn, String eid` in every signature; mixing them up is a runtime bug the type system could have caught.
- A value object with a setter or a mutable field (`List` without `List.copyOf`) is not a value object; two equal objects can diverge.
- Validation only in the DTO: the Kafka listener and the scheduler bypass the controller and feed garbage into the domain.
- Entities compared with `==` across persistence contexts, or `equals` over mutable fields: `Set<Sim>` membership silently changes.
- Related: [[ddd-basics]], [[aggregates-and-invariants]], [[immutable-objects]], [[immutability-and-side-effects]].
