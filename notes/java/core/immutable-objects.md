---
title: Immutable objects
tags: [immutability, final, defensive-copy, records, thread-safety]
learned: 2026-10-22
---
## TL;DR
An immutable object never changes state after construction. Recipe: `final` class, `private final` fields, everything set in the constructor, no setters, defensive copies of mutable inputs on the way in and of mutable fields on the way out (or unmodifiable views). Immutable objects are thread-safe for free, safe as map keys, and trivially cacheable; `record` gives you most of the recipe in one line.

## Key points
### The recipe
1. Declare the class `final` so a subclass cannot add mutable state or override methods (`String`, wrappers, `LocalDate` all do this). Alternative: private constructor plus static factories, which also forbids subclassing.
2. Make every field `private final`. Final fields also get the JMM's initialization-safety guarantee: any thread that sees the object sees the fully built fields without synchronization ([[java-memory-model]]).
3. Initialize everything in the constructor (or via a builder that calls it, see [[constructors-and-initialization]]). Validate there.
4. No setters, no methods that mutate fields. "Modifying" operations return a new instance (`plus`, `withStatus`).
5. **Defensive copies**: Java passes references by value, so a caller still holds the `List` or `Date` it gave you. Copy on the way in (`List.copyOf(input)`, `new ArrayList<>(input)`, `date.toInstant()`), and on the way out return the copy, an unmodifiable view (`Collections.unmodifiableList`), or an immutable collection.
6. Fields of mutable types (`StringBuilder`, arrays, `Date`) must never leak; prefer immutable types for fields (`String`, `Instant`, `List.of`, your own value objects).

### What you gain
- Thread safety with no locks; sharing between threads and caching (`Integer` cache, string pool) are only possible because nobody can mutate the shared instance.
- Safe `HashMap`/`HashSet` keys: the hash cannot change after insertion ([[equals-and-hashcode]]).
- Failure atomicity: an operation that throws cannot leave the object half-updated.
- Easier reasoning and testing; `equals` and `hashCode` are stable.
- Cost: more allocation for "modified copies". The JIT's escape analysis and the young-generation collector make short-lived objects cheap ([[garbage-collection]]), so this rarely matters; for a hot path a mutable builder is the escape hatch (`StringBuilder` next to `String`).

### Records (Java 16)
- `record Range(Instant from, Instant to)` is implicitly `final`, fields are `private final`, canonical constructor, accessors, `equals`/`hashCode`/`toString`. A compact constructor validates and can replace components (`from = from.truncatedTo(SECONDS)`), including making defensive copies.
- Records are *shallowly* immutable: a `record Config(List<String> hosts)` still exposes the caller's list unless the compact constructor does `hosts = List.copyOf(hosts)`.

### Immutable collections
- `List.of`, `Set.of`, `Map.of`, `List.copyOf` (Java 9/10): truly immutable, reject `null` elements, throw `UnsupportedOperationException` on mutation. `Collectors.toUnmodifiableList` (10) and `Stream.toList()` (16) produce them.
- `Collections.unmodifiableList(x)` is a *view*: the underlying list can still change through the original reference.

### In the JDK
- `String`, all wrappers ([[primitives-and-wrappers]]), `BigDecimal`/`BigInteger`, `java.time` types, `Optional`, `UUID`, `Path`. The old `Date` and `Calendar` are mutable, which is one reason `java.time` exists.

## Example
```java
public final class TopUp {
    private final String msisdn;
    private final long amountMinor;
    private final Instant at;
    private final List<String> channels;          // mutable type -> copy in, immutable out

    public TopUp(String msisdn, long amountMinor, Instant at, List<String> channels) {
        if (amountMinor <= 0) throw new IllegalArgumentException("amount must be positive");
        this.msisdn = Objects.requireNonNull(msisdn);
        this.amountMinor = amountMinor;
        this.at = Objects.requireNonNull(at);
        this.channels = List.copyOf(channels);    // defensive copy; List.copyOf is already immutable
    }

    public String msisdn() { return msisdn; }
    public long amountMinor() { return amountMinor; }
    public Instant at() { return at; }
    public List<String> channels() { return channels; }   // safe: List.copyOf result cannot be mutated

    public TopUp withAmount(long newAmountMinor) {         // "modification" returns a new object
        return new TopUp(msisdn, newAmountMinor, at, channels);
    }
}

// The same with a record and a compact constructor:
record TopUpRecord(String msisdn, long amountMinor, Instant at, List<String> channels) {
    TopUpRecord {
        if (amountMinor <= 0) throw new IllegalArgumentException("amount must be positive");
        channels = List.copyOf(channels);
    }
}
```

## Pitfalls
- `final` on a field freezes the reference, not the object: `private final List<String> tags = new ArrayList<>()` is fully mutable through `getTags().add(...)`.
- Returning an array from a getter leaks it: arrays are always mutable; clone it or return a `List`.
- Jackson and JPA need a way to construct the object: use `@JsonCreator`/records for Jackson; JPA entities cannot be immutable in the strict sense (Hibernate needs a no-arg constructor and field access), so keep value objects immutable and entities mutable-but-encapsulated ([[jpa-entities-and-relationships]]).
- A "mostly immutable" class with one lazily computed cache field needs that field to be `volatile` or computed under a lock; `String.hash` is safe only because an `int` write is atomic and recomputation is idempotent.
- Related: [[strings-and-string-pool]], [[equals-and-hashcode]], [[concurrency-hazards]].
