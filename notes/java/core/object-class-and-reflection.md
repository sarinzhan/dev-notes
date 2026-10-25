---
title: The Object class, clone and reflection
tags: [object, clone, reflection, finalize, cleaner]
learned: 2026-10-22
---
## TL;DR
`java.lang.Object` is the root of every class and supplies identity (`equals`, `hashCode`), description (`toString`, `getClass`), copying (`clone`), monitor methods (`wait`, `notify`, `notifyAll`) and the deprecated `finalize`. Reflection (`java.lang.Class` and `java.lang.reflect`) lets a program inspect and manipulate classes, fields, methods and constructors at runtime; it is what makes Spring, Hibernate, Jackson and JUnit work.

## Key points
### Methods of Object
| Method | Purpose | Notes |
|---|---|---|
| `equals(Object)` | logical equality | default is `==`; see [[equals-and-hashcode]] |
| `hashCode()` | hash for hash-based collections | `native`, identity hash by default |
| `toString()` | human-readable form | default `ClassName@hexIdentityHash`; override for logs |
| `getClass()` | the runtime `Class<?>` | `final`; entry point to reflection |
| `clone()` | shallow copy | `protected native`; requires `Cloneable` |
| `wait()`, `notify()`, `notifyAll()` | monitor coordination | `final`; must hold the monitor, see [[synchronization-and-monitors]] |
| `finalize()` | hook before GC reclaims the object | deprecated since 9, for removal since 18; never rely on it |

- `finalize` runs on a dedicated finalizer thread at an unknown time (or never), delays reclamation by at least one GC cycle and can resurrect objects. Replacements: `try-with-resources` with `AutoCloseable` for deterministic release, `java.lang.ref.Cleaner` (Java 9) as a safety net ([[reference-types]]).

### clone and Cloneable
- `Object.clone()` is `protected native`: it allocates a new object of the same class and copies every field bit-for-bit, a **shallow copy**. Primitives are copied by value; reference fields point at the same objects as the original.
- `Cloneable` is a marker interface with no methods; if the class does not implement it, `Object.clone()` throws `CloneNotSupportedException`. To make cloning usable you override `clone()` as `public`, call `super.clone()`, and deep-copy mutable fields yourself.
- Arrays implement `Cloneable` and `clone()` on them is public and fast; that is the one place `clone` is idiomatic.
- Everywhere else it is considered a design mistake (bypasses constructors, no `final` field re-assignment, checked exception). Prefer a copy constructor, a static `copyOf` factory, or records' `with`-style methods ([[immutable-objects]]).

### Reflection
- Obtaining a `Class`: `obj.getClass()`, `Type.class`, `Class.forName("fq.Name")` (also initializes the class, see [[class-initialization-order]]), `classLoader.loadClass`.
- What you can do: find the superclass and interfaces; list modifiers, fields, methods, constructors (`getDeclaredX` includes private, `getX` only public including inherited); read annotations ([[annotations]]); instantiate (`getDeclaredConstructor(...).newInstance(...)`); get/set field values; invoke methods; create arrays (`Array.newInstance`); build dynamic proxies (`java.lang.reflect.Proxy`, the basis of JDK-proxy AOP in [[spring-aop]]).
- `setAccessible(true)` bypasses access checks; since Java 9 it is refused for JDK internals unless the module is opened (`--add-opens java.base/java.lang=ALL-UNNAMED`), and since 16 strong encapsulation is the default. Your own classpath code is still fully open to itself.
- Cost: lookups are slow (string matching, security checks); cache `Method`/`Field` objects. Invocation through `Method.invoke` is a few times slower than a direct call and opaque to the JIT; `MethodHandle` (Java 7) and `LambdaMetafactory` are faster once bound, which is why frameworks moved to them.
- Reflection defeats static analysis and GraalVM native-image unless you register the accessed elements.

### Where it is used
- Dependency injection and bean creation ([[spring-ioc-and-di]]), ORM entity hydration ([[jpa-persistence-context-and-entity-lifecycle]]), JSON (de)serialization, test frameworks finding `@Test` methods, serialization, debuggers and profilers.

## Example
```java
// A minimal "mapper" that copies same-named fields between a DTO and an entity, the way
// BeanUtils/ModelMapper do it, with Field objects cached per class pair.
public final class FieldCopier {
    private static final Map<String, List<Field[]>> CACHE = new ConcurrentHashMap<>();

    public static <S, T> T copy(S source, T target) {
        String key = source.getClass().getName() + "->" + target.getClass().getName();
        List<Field[]> pairs = CACHE.computeIfAbsent(key, k -> pair(source.getClass(), target.getClass()));
        try {
            for (Field[] p : pairs) p[1].set(target, p[0].get(source));
        } catch (IllegalAccessException e) {
            throw new IllegalStateException(e);
        }
        return target;
    }

    private static List<Field[]> pair(Class<?> src, Class<?> dst) {
        List<Field[]> result = new ArrayList<>();
        for (Field sf : src.getDeclaredFields()) {
            if (Modifier.isStatic(sf.getModifiers())) continue;
            try {
                Field df = dst.getDeclaredField(sf.getName());
                if (df.getType() != sf.getType()) continue;
                sf.setAccessible(true); df.setAccessible(true);
                result.add(new Field[]{sf, df});
            } catch (NoSuchFieldException ignored) { }
        }
        return result;
    }
}
```

## Pitfalls
- `clone()` on an object with a `List` field gives two objects sharing one list; the classic "copy" that is not a copy.
- Reflective access to private fields couples your code to someone else's implementation details and breaks on upgrade; the JDK now blocks it for its own internals.
- `Class.forName` in a library loaded by a parent loader cannot see classes in child loaders (Tomcat, Spring Boot fat jar); use the thread context class loader.
- `Method.invoke` wraps the target's exception in `InvocationTargetException`; unwrap `getCause()` before logging or the real error is hidden ([[exceptions]]).
- Related: [[annotations]], [[equals-and-hashcode]], [[reference-types]].
