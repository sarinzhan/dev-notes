---
title: Class loading and initialization order
tags: [class-loading, static-init, initialization-order, classloader]
learned: 2026-10-22
---
## TL;DR
A class is loaded, linked and initialized lazily on first active use. Static initialization runs once per class, parent before child, in textual order of static fields and static blocks. Instance initialization runs on every `new`: for each class from the top of the hierarchy down, instance field initializers and instance blocks in textual order, then the constructor body.

## Key points
### Class loading phases
1. **Loading**: the class loader finds the `.class` bytes (classpath, module path, jar, network) and creates the `Class` object in the metaspace ([[jvm-memory-areas]]). Delegation: a loader asks its parent first (bootstrap → platform → application → custom), so `java.lang.String` can never be replaced by a user jar.
2. **Linking**: verification (bytecode is well-formed and type-safe), preparation (static fields get default values: `0`, `null`, `false`), resolution (symbolic references turned into direct ones, often lazily).
3. **Initialization**: the static initializer `<clinit>` runs: static field initializers and `static {}` blocks in source order. The JVM takes a lock per class so this happens exactly once even if several threads race (that lock is what makes the holder idiom a safe lazy singleton, see [[creational-patterns]]).

### What triggers initialization
- `new`, a static method call, a non-constant static field read or write, `Class.forName(name)` (but not `loadClass`), reflection, initializing a subclass, being the main class.
- Not triggers: referencing a `static final` compile-time constant (it is inlined into the user), creating an array of the type, `SomeClass.class` literal.

### Static order (once per class)
1. Superclass static initialization completes first, all the way up from `Object`.
2. Then the subclass: static field initializers and static blocks interleaved in textual order.
- Interfaces are initialized only when one of their non-constant fields is read or a default method is needed; implementing a class does not initialize its interfaces.

### Instance order (every `new`)
For each class from the top of the hierarchy down:
1. instance field initializers and instance initializer blocks `{ ... }` in textual order;
2. the constructor body (after the implicit or explicit `super(...)` call has returned).

So for `new Child()` the full sequence is: `Parent` static → `Child` static (first time only) → `Parent` fields/blocks → `Parent` constructor → `Child` fields/blocks → `Child` constructor. The source's shorthand "field, then dynamic block, then constructor, pair by pair from parent to child" is exactly this.

### Same class, two loaders
- Class identity is (loader, fully qualified name). The same bytes loaded by two loaders are two different classes: `instanceof` fails and casting throws `ClassCastException`. This bites in app servers, plugin systems and hot-reload tools.
- A class is unloaded only when its loader becomes unreachable; that is why metaspace grows with repeated redeploys that leak the old loader.

## Example
```java
class Parent {
    static { System.out.println("1 Parent static"); }
    { System.out.println("3 Parent instance block"); }
    Parent() { System.out.println("4 Parent ctor"); describe(); }
    void describe() { System.out.println("Parent.describe"); }
}

class Child extends Parent {
    private final String name = "child";
    static { System.out.println("2 Child static"); }
    { System.out.println("5 Child instance block"); }
    Child() { System.out.println("6 Child ctor"); }
    @Override void describe() { System.out.println("Child.describe name=" + name); }
}

// new Child(); prints 1,2,3,4, then "Child.describe name=null"  <-- overridable call from ctor
//              then 5,6. A second new Child() starts at 3.
```

## Pitfalls
- Calling an overridable method from a constructor runs the subclass version before the subclass fields are initialized (`name=null` above). Mark such methods `final` or `private`, or move the logic out of the constructor ([[constructors-and-initialization]]).
- A static initializer that throws causes `ExceptionInInitializerError`; every later use of the class throws `NoClassDefFoundError`, with the original cause long gone from the logs. Keep static blocks trivial.
- Circular static initialization (A's static init touches B, whose static init touches A) sees half-initialized state (default `null`/`0`) with no error at all.
- `static final` constants are inlined at compile time; changing the constant in a library and not recompiling the client leaves the old value in the client's bytecode.
- Related: [[modifiers-final-static-access]], [[jvm-jre-jdk]], [[constructors-and-initialization]].
