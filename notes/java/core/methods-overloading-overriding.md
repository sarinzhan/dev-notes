---
title: Methods, signatures, overloading and overriding
tags: [methods, overloading, overriding, signature, loops]
learned: 2026-10-22
---
## TL;DR
A method's **signature** is its name plus parameter types; return type, exceptions and modifiers are not part of it. **Overloading** = several methods with the same name and different signatures in one class, resolved at compile time. **Overriding** = a subclass replacing a superclass method with the same signature, resolved at run time by the object's actual class. Also here, because they are too small for their own notes: the kinds of loops and the forms of expressions.

## Key points
### Method and signature
- A method is a named block that takes parameters, executes statements and returns a value (or `void`). Under the hood it is a function with a hidden `this` ([[oop-principles]]).
- Signature = name + ordered parameter types. `int parse(String s)` and `long parse(String s)` have the same signature and cannot coexist. Generic types erase: `void f(List<String>)` and `void f(List<Integer>)` clash ([[generics]]).

### Overloading (compile time, ad-hoc polymorphism)
- Same name, different parameter lists; may differ in return type as well, but not only in return type.
- Resolution order when several overloads could apply: exact match → widening primitive (`int` → `long`) → boxing/unboxing → varargs. `print(null)` with overloads `print(String)` and `print(Object)` picks `String` (most specific); with `print(String)` and `print(Integer)` it is a compile error (ambiguous).
- Static methods can be overloaded but not overridden ([[modifiers-final-static-access]]).

### Overriding (run time, subtype polymorphism)
- Same signature in a subclass; `@Override` makes the compiler check it (a typo otherwise silently creates an overload).
- Rules:
  - Access may be **widened**, never narrowed: `protected` → `protected` or `public`; package-private → package-private, `protected` or `public`. `private` methods are not inherited and cannot be overridden.
  - Return type may be **covariant**: the override may return a subtype of the original return type (`Object clone()` → `Order clone()`).
  - Checked exceptions may be the same, narrower or removed, never new or wider; unchecked exceptions are unrestricted ([[exceptions]]).
  - `final`, `static` and `private` methods cannot be overridden; `static` with the same signature is *hiding*.
- Dispatch: `invokevirtual`/`invokeinterface` look up the method in the actual class's vtable/itable; the JIT turns monomorphic sites into direct calls with a type guard.

### Loops
| Kind | Condition checked | Java form | Use when |
|---|---|---|---|
| Pre-condition | before each iteration | `while (cond)` | unknown iteration count, may run zero times |
| Post-condition | after each iteration | `do { } while (cond)` | body must run at least once (read-then-check, retry loops) |
| Counter | start, end, step in the header | `for (int i = 0; i < n; i++)` | index-based, fixed count |
| For-each | iterator exhausted | `for (T x : iterable)` | every element, no index needed; works on arrays and `Iterable` ([[iterators-and-fail-fast]]) |

- `break`, `continue` and labelled forms (`outer:`) control nested loops; a `return` inside a loop exits the method. Streams ([[stream-api]]) replace many counter loops but cannot `break` early except via short-circuit operations.

### Expression forms (operator placement)
- **Prefix** `++a`, `-a`, `!b`: operator before the operand; `++a` increments and yields the new value.
- **Infix** `a + b`: between operands; all binary operators in Java.
- **Postfix** `a++`: after; yields the old value, then increments.
- An operator is a symbol or keyword (`+`, `instanceof`, `? :`) applied to operands (values or variables). Java has no user-defined operator overloading; `+` on `String` is the single built-in exception.

## Example
```java
public class Money {
    private final long minor;
    private final Currency currency;
    Money(long minor, Currency currency) { this.minor = minor; this.currency = currency; }

    // Overloading: same name, different parameter lists, chosen at compile time
    public Money plus(Money other)  { return new Money(minor + other.minor, currency); }
    public Money plus(long minor)   { return new Money(this.minor + minor, currency); }

    // Overriding: same signature as Object.toString, chosen at run time
    @Override public String toString() { return minor / 100 + "." + minor % 100 + " " + currency; }
}

class AuditedMoney extends Money {
    AuditedMoney(long minor, Currency c) { super(minor, c); }
    @Override public AuditedMoney plus(Money other) {        // covariant return, same signature
        AuditedMoney result = new AuditedMoney(0, null);     // (illustrative)
        return result;
    }
}
```

## Pitfalls
- Overloading on `Object` vs a specific type with a variable of static type `Object` calls the `Object` version even if the object is a `String`: overload resolution never looks at the runtime type.
- `remove(int index)` vs `remove(Object o)` on `List<Integer>`: `list.remove(1)` removes by index; box explicitly to remove the value.
- Forgetting `@Override` on a method with a slightly different parameter type (`equals(Order)` instead of `equals(Object)`) creates an overload that collections never call ([[equals-and-hashcode]]).
- Varargs overloads combined with autoboxing produce ambiguous-call compile errors that are hard to read; avoid mixing them.
- Related: [[abstract-classes-and-interfaces]], [[casting-and-autoboxing]], [[constructors-and-initialization]].
