---
title: Access modifiers, final, static, this and super
tags: [modifiers, final, static, access-control, effectively-final]
learned: 2026-10-22
---
## TL;DR
Access modifiers (`private`, package-private, `protected`, `public`) define who can see a member; `final` forbids change (reassignment, overriding, subclassing); `static` ties a member to the class instead of an instance. `this` is the hidden receiver argument of every instance method and `super` is the same object viewed as its superclass.

## Key points
### Access modifiers
| Modifier | Same class | Same package | Subclass (any package) | Everyone |
|---|---|---|---|---|
| `private` | yes | | | |
| (none) package-private | yes | yes | | |
| `protected` | yes | yes | yes | |
| `public` | yes | yes | yes | yes |

- Top-level classes can only be `public` or package-private. Nested classes may use all four ([[nested-and-anonymous-classes]]).
- `protected` includes package access, which surprises people: a `protected` field is visible to every class in the same package, subclass or not.
- Since Java 9 the module system adds a layer above `public`: a `public` type in a non-exported package is invisible outside its module.
- Overriding may widen access but never narrow it ([[methods-overloading-overriding]]).

### final
- **Class**: cannot be extended (`String`, wrappers, records are implicitly final). Reason: immutability guarantees and security ([[immutable-objects]]).
- **Method**: cannot be overridden. The JIT can inline any monomorphic call anyway, so `final` on methods is about design, not speed.
- **Field**: must be assigned exactly once, in the declaration, an initializer block or every constructor; afterwards it cannot change. Note that it freezes the reference, not the object: a `final List` can still be mutated.
- **Parameter / local variable**: cannot be reassigned after initialization.
- `final` fields also get a memory-model guarantee: a correctly constructed object's final fields are visible to every thread without synchronization ([[java-memory-model]]).
- **Effectively final** (Java 8): a local variable that is never reassigned after initialization even though it is not declared `final`. Lambdas and anonymous/local classes may capture only final or effectively final locals, because the value is copied into the closure; allowing later changes would make the copy and the original diverge.

### static
- A `static` field or method belongs to the class and is reachable without an instance; there is exactly one copy per class per class loader.
- A static method cannot use `this` or `super` and can only directly access other static members. It can of course take an instance as a parameter.
- Static methods can be overloaded but **not overridden**: a subclass declaring the same static signature *hides* the parent's method, and the call is resolved by the static type of the reference at compile time.
- `static` applies to nested classes (a static nested class does not hold an outer instance) and to initializer blocks (run once at class initialization, [[class-initialization-order]]).
- Interface static methods (Java 8) are not inherited by implementing classes; call them as `Interface.method()`.

### this and super
- `this` is a reference to the current object; under the hood it is the hidden first argument the JVM passes to every instance method (`invokevirtual` pushes the receiver, see [[oop-principles]]). Uses: disambiguate field from parameter (`this.name = name`), call another constructor (`this(...)`, must be the first statement), pass the current object (`registry.register(this)`), return it for a fluent API.
- `super` refers to the superclass view of the same object: `super(...)` calls the parent constructor (implicit no-arg call if omitted), `super.method()` calls the overridden implementation, `super.field` reaches a hidden field.
- Inside a lambda `this` is the enclosing instance; inside an anonymous class `this` is the anonymous object ([[nested-and-anonymous-classes]]).

## Example
```java
public class RateLimiter {
    private static final int DEFAULT_PERMITS = 100;        // one per class, constant
    private static int instances;                           // shared mutable: needs care

    private final int permits;                              // set once per object
    protected long windowMillis = 1_000;                    // visible to subclasses and the package

    public RateLimiter() { this(DEFAULT_PERMITS); }         // constructor chaining via this(...)

    public RateLimiter(int permits) {
        this.permits = permits;
        instances++;                                        // static method could not use 'this' here
    }

    public static int instances() { return instances; }

    public Runnable onExhausted(String clientId) {
        int snapshot = permits;              // effectively final: captured by the lambda
        return () -> log(clientId + " exceeded " + snapshot + " in " + this.windowMillis + " ms");
    }

    protected void log(String msg) { System.out.println(msg); }
}
```

## Pitfalls
- A `static` mutable field is global state shared by every thread; guard it or make it immutable. Static collections are a classic memory leak ([[garbage-collection]]).
- Calling an overridable method from a constructor runs the subclass version before the subclass fields are initialized ([[constructors-and-initialization]]).
- `final` on a method parameter or local is mostly noise; `final` on fields is what matters for immutability and thread safety.
- Hiding static methods and fields in subclasses is almost always a mistake; IDEs warn about it for a reason.
- Related: [[class-initialization-order]], [[immutable-objects]], [[abstract-classes-and-interfaces]].
