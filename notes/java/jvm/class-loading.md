---
title: Class loading, class loaders and parent delegation
tags: [jvm, class-loading, classloader, parent-delegation, static-init]
learned: 2026-11-25
---
## TL;DR
A class goes through three stages: **loading** (a ClassLoader finds the bytes, the JVM builds metadata in Metaspace and a `Class` object on the heap), **linking** (verify → prepare → resolve) and **initialization** (`<clinit>` runs). HotSpot does all of it lazily, and loading and initialization are separate events: `Foo.class` or `loadClass("Foo")` loads a class without running its `static {}` block. Loaders form a chain (Bootstrap → Platform → Application → custom), and each one asks its parent first, so core classes cannot be replaced and each class has exactly one defining loader.

## Key points
### The three stages
```
Loading  →  Linking (Verify → Prepare → Resolve)  →  Initialization
```
1. **Loading.** A ClassLoader finds the `.class` bytes by the binary name (`com.acme.Foo`): from a jar, a folder, the network, or generated at runtime (proxies, CGLIB). The JVM builds the class metadata in **Metaspace** and creates a `java.lang.Class` object on the **heap** ([[jvm-memory-areas]]).
2. **Linking.**
   - **Verification:** the bytecode is type-safe, the operand stack can't overflow, no jump leaves the method. Failure → `VerifyError`.
   - **Preparation:** static fields are allocated and get **default** values (`0`, `null`, `false`), not the values from your code. Compile-time constants (`static final int X = 5`) already get their value here.
   - **Resolution:** symbolic references in the constant pool (`"java/util/List"`) become direct references. HotSpot does it lazily, on first use.
3. **Initialization.** The JVM runs `<clinit>`: static field assignments and `static {}` blocks in source order, under a per-class lock, exactly once. The holder-idiom singleton relies on that lock ([[creational-patterns]]). The detailed static and instance order is in [[class-initialization-order]].

### Metaspace vs the `Class` object
Loading creates two halves of one class, linked to each other:
- **Metaspace** (native memory) is the class's **blueprint for the JVM**: field layout and object size, methods and bytecode, the constant pool, vtables, JIT profiling data. The JVM reads it on `new Foo()` (size, layout), on `foo.bar()` (which bytecode, which implementation), on first resolution (names → addresses) and when it JIT-compiles hot code.
- **The `java.lang.Class` object** (heap) is the **handle for Java code**, because Java can only reference Java objects: `Foo.class`, `getClass()` and reflection go through it, **static fields live in it**, `static synchronized` locks on it, and while it is reachable the class and its loader stay alive.

### When a class is loaded
- **Startup:** Bootstrap loads the core classes (`Object`, `String`, `Thread`…), then the Application loader loads the main class.
- **Resolving a reference:** the first time running code touches `Foo` (`new Foo()`, `Foo.bar()`, `new Foo[3]`).
- **Verifying another class:** the verifier may load `Foo` just to check that it is a subtype of `Bar`, so a class can be loaded before any of its code runs.
- **Explicit calls:** `Class.forName`, `loader.loadClass`, the `Foo.class` literal, reflection and proxy frameworks (Spring loads thousands of classes while scanning packages).
- See it: `java -Xlog:class+load -jar app.jar`. A Spring Boot app loads 10–20k classes; that is a big part of its startup time and Metaspace use.

### When a class is initialized
- **Triggers (first active use):** `new`, a static method call, a read or write of a non-constant static field, `Class.forName(name)`, reflection on a member, initializing a subclass (the superclass goes first), being the main class.
- **Not triggers:** `loader.loadClass(name)`, `Class.forName(name, false, loader)`, the `Foo.class` literal, `new Foo[10]`, reading a compile-time constant (javac inlines it).
- This is why `Class.forName("com.mysql.cj.jdbc.Driver")` registers a JDBC driver and `loadClass` does not: registration lives in the driver's `static {}` block.

### Class loaders (Java 9+)
| Loader | Loads | Notes |
|---|---|---|
| Bootstrap | `java.base` and other core modules | Native code; `String.class.getClassLoader()` returns `null` |
| Platform | Other JDK modules (`java.sql`, …) | Called the Extension loader before Java 9 |
| Application (System) | Classpath / module path | Default loader for your code |
| Custom | Anything | Tomcat, Spring Boot fat-jar loader, plugin systems |

### Parent delegation
`loadClass(name)` works in three steps:
1. `findLoadedClass` — return the class if this loader already loaded it.
2. Ask the parent, recursively up to Bootstrap.
3. Only if the parent fails, `findClass` — the loader finds the bytes itself.

Why:
- **Security:** nobody can slip in their own `java.lang.String`, because Bootstrap always wins.
- **One copy of each class:** class identity is (defining loader, full name). The same bytes loaded by two loaders are two types: `instanceof` fails and you get `ClassCastException: Foo cannot be cast to Foo`.

Where the rule is deliberately broken:
- **Tomcat:** a web app's loader checks `WEB-INF` first (child-first), so every app can use its own library versions.
- **JDBC / `ServiceLoader` (SPI):** `DriverManager` lives in a JDK loader that can't see your driver jar, so it uses the **thread context ClassLoader** to reach down into the Application loader.
- **Spring Boot DevTools:** a separate restart loader for your code; hot reload throws it away and creates a new one.

### Unloading
A class is unloaded only when its loader becomes unreachable and is garbage-collected. Classes of the Bootstrap, Platform and Application loaders stay in Metaspace until the JVM exits.

## Example
Loaded vs initialized, step by step. Run with `java -Xlog:class+load ClassLoadingDemo` (no package).
```java
public class ClassLoadingDemo {

    static class Lazy {
        static { System.out.println("   -> Lazy: <clinit> ran"); }
        static final int CONSTANT = 42;   // compile-time constant
        static void hello() { System.out.println("   Lazy.hello()"); }
    }

    static class Parent { static { System.out.println("   -> Parent: <clinit> ran"); } }
    static class Child extends Parent { static { System.out.println("   -> Child: <clinit> ran"); } }

    public static void main(String[] args) throws Exception {
        ClassLoader loader = ClassLoadingDemo.class.getClassLoader();
        String name = "ClassLoadingDemo$Lazy";

        System.out.println("1. Lazy.CONSTANT = " + Lazy.CONSTANT); // inlined by javac
        System.out.println("2. new Lazy[3]: " + new Lazy[3].length); // loads Lazy, no init
        System.out.println("3. Lazy.class: " + Lazy.class.getSimpleName()); // no init
        loader.loadClass(name);
        System.out.println("4. loader.loadClass(...) done"); // no init
        Class.forName(name, false, loader);
        System.out.println("5. Class.forName(name, false, loader) done"); // no init
        System.out.println("6. Lazy.hello():");
        Lazy.hello(); // first active use -> <clinit>
        System.out.println("7. new Child():");
        new Child(); // superclass initialized first
    }
}
```
Real output (JDK lines trimmed):
```
[class,load] ClassLoadingDemo source: file:/.../
1. Lazy.CONSTANT = 42
[class,load] ClassLoadingDemo$Lazy source: file:/.../
2. new Lazy[3]: 3
3. Lazy.class: Lazy
4. loader.loadClass(...) done
5. Class.forName(name, false, loader) done
6. Lazy.hello():
   -> Lazy: <clinit> ran
   Lazy.hello()
7. new Child():
[class,load] ClassLoadingDemo$Parent source: file:/.../
[class,load] ClassLoadingDemo$Child source: file:/.../
   -> Parent: <clinit> ran
   -> Child: <clinit> ran
```
What happens when it runs:
1. **JVM start:** Bootstrap loads core classes; the Platform and Application loaders are created.
2. **Main class:** the Application loader asks Platform, which asks Bootstrap; neither has it, so it loads `ClassLoadingDemo` from the classpath. It has no static state, so there is no `<clinit>`; `main` starts.
3. **Step 1:** `Lazy.CONSTANT` was replaced by `42` at compile time — `Lazy` is not even loaded.
4. **Step 2:** creating `Lazy[]` needs `Lazy` resolved, so it is **loaded**, but creating an array is not an active use, so it is **not initialized**.
5. **Steps 3–5:** `Lazy` comes from the loader's cache; `forName(name, false, …)` explicitly skips initialization.
6. **Step 6:** a static method call is the first active use: `<clinit>` runs once, then `hello()`.
7. **Step 7:** loading `Child` loads its superclass `Parent` first; `Parent` is initialized before `Child`.

Change step 5 to `Class.forName(name)` and `<clinit>` moves up to step 5.

## Pitfalls
- A `static {}` block that throws gives `ExceptionInInitializerError` once; every later use of the class throws `NoClassDefFoundError`, with the original cause long gone from the logs. Keep static blocks trivial.
- `ClassCastException: Foo cannot be cast to Foo` means two loaders loaded `Foo` (app servers, plugins, DevTools restart loader with a library outside it).
- A leaked class loader (a thread or `ThreadLocal` still holding one of its classes) keeps every class it loaded in Metaspace; repeated redeploys end in `OutOfMemoryError: Metaspace`.
- `ClassNotFoundException` (checked, an explicit `forName`/`loadClass` failed) is not `NoClassDefFoundError` (the class was there at compile time but can't be loaded or initialized now).
- Related: [[class-initialization-order]], [[jvm-memory-areas]], [[compilation-interpretation-jit]], [[object-class-and-reflection]].
