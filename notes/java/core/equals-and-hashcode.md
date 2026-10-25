---
title: equals and hashCode contracts
tags: [equals, hashcode, object-identity, contracts, records]
learned: 2026-10-22
---
## TL;DR
`equals` defines logical equality, `hashCode` gives a fast pre-check that hash-based collections use to find the bucket. The contract: equal objects must have equal hash codes (the reverse is not required), so **whenever you override `equals` you must override `hashCode`**, and both must use the same fields. `==` compares references, `equals` compares whatever you define.

## Key points
### The equals contract (JLS / `Object.equals` Javadoc)
- **Reflexive**: `x.equals(x)` is `true`.
- **Symmetric**: `x.equals(y)` iff `y.equals(x)`. Easy to break with `instanceof` against a subclass that adds fields.
- **Transitive**: `x.equals(y)` and `y.equals(z)` imply `x.equals(z)`.
- **Consistent**: repeated calls return the same result while the objects do not change; so do not use fields that depend on time, randomness or external state.
- **Null**: `x.equals(null)` is `false` (never throws). The source's "`== null`" is a typo for "returns false".

### The hashCode contract
- Repeated calls on the same unchanged object return the same `int` within one JVM run (it need not be stable across runs or versions; it is not for persistence).
- If `a.equals(b)` then `a.hashCode() == b.hashCode()`.
- Unequal objects *may* share a hash code (a collision); the better distributed the hashes, the faster `HashMap` is ([[hashmap-internals]]).

### How collections use them
- `HashMap`/`HashSet`: `hashCode` picks the bucket; inside the bucket the stored hash is compared first, then `equals`. Override `equals` only, and two "equal" keys land in different buckets: `contains` and `get` miss.
- `TreeMap`/`TreeSet` ignore both and use `compareTo`/`Comparator`; keep `compareTo` consistent with `equals` or sets will behave oddly.
- `List.contains`, `remove(Object)`, `indexOf` use only `equals`.

### Default implementations in Object
- `Object.equals` is `this == obj`: reference identity.
- `Object.hashCode` is `native`; HotSpot generates the identity hash lazily on first call (default algorithm since JDK 8 is a thread-local **xorshift** PRNG, `-XX:hashCode=5`) and stores it in the object header (mark word). It is **not** the memory address: objects move during GC ([[garbage-collection]]). Computing it inflates the header, which is one reason biased locking was removed.
- `System.identityHashCode(o)` gives the identity hash even if `hashCode` is overridden; `IdentityHashMap` is built on it.

### Writing them correctly
1. `equals(Object o)`: identity shortcut, `null`/class check, cast, compare significant fields (`==` for primitives, `Double.compare` for floating point, `Objects.equals` for references, `Arrays.equals` for arrays).
2. `hashCode`: `Objects.hash(f1, f2, ...)` (allocates an array, fine outside hot paths) or the manual `31 * h + field` chain.
3. Use the **same fields** in both; prefer immutable fields ([[immutable-objects]]).
4. Class check: `getClass() != o.getClass()` keeps symmetry strict and is what IDEs generate; `instanceof` allows subclasses to be equal to parents but breaks symmetry as soon as a subclass adds state. Hibernate proxies are the known exception where `instanceof` (or `Hibernate.getClass`) is required.
5. Or avoid the whole thing: **records** (Java 16) generate both from all components; Lombok `@EqualsAndHashCode`.

### JPA entities
- Do not base `equals` on the auto-generated id: it is `null` before the first flush, so an entity changes hash while sitting in a `HashSet`. Options: a business key (ICCID, MSISDN), a client-generated UUID assigned in the constructor, or identity semantics plus a stable `hashCode` constant/class-based (what the Hibernate docs suggest). See [[jpa-persistence-context-and-entity-lifecycle]].

## Example
```java
public final class Iccid {
    private final String value;                 // 19-20 digits, business identity of an eSIM profile

    public Iccid(String value) {
        if (!value.matches("\\d{19,20}")) throw new IllegalArgumentException("bad ICCID: " + value);
        this.value = value;
    }

    @Override public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        return value.equals(((Iccid) o).value);
    }

    @Override public int hashCode() { return value.hashCode(); }   // same field as equals

    @Override public String toString() { return value; }
}

// Equivalent in one line, with the same contract guaranteed by the compiler:
record IccidRecord(String value) {}
```

## Pitfalls
- `equals(Iccid other)` instead of `equals(Object)` is an overload that `HashMap` never calls ([[methods-overloading-overriding]]); always annotate with `@Override`.
- Mutating a field that takes part in `hashCode` while the object is a key in a `HashMap` or element of a `HashSet` makes it unfindable; the entry leaks until the map is dropped.
- Including a lazily loaded JPA collection or a bidirectional back-reference in `equals`/`hashCode` causes `LazyInitializationException` or infinite recursion; Lombok `@Data` on entities does exactly that.
- `Objects.hash` on a single field boxes and allocates; for hot keys write `value.hashCode()` directly.
- Related: [[hashmap-internals]], [[object-class-and-reflection]], [[strings-and-string-pool]].
