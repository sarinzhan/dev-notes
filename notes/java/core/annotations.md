---
title: Annotations
tags: [annotations, metadata, retention, reflection, annotation-processing]
learned: 2026-10-22
---
## TL;DR
An annotation is structured metadata attached to a package, type, member, parameter, local variable or type use. By itself it does nothing; a tool reads it: the compiler (`@Override`), an annotation processor at build time (Lombok, MapStruct), or a framework at runtime through reflection (Spring, JPA, Jackson). `@Retention` decides how far the annotation survives, `@Target` where it may be written.

## Key points
### Declaring one
- `@interface Name { String value() default ""; int[] codes() default {}; }`. Elements may be primitives, `String`, `Class`, enums, other annotations, or arrays of those. An element named `value` can be written without the name: `@Role("admin")`.
- Annotations are interfaces at runtime; the JVM creates proxy instances when you read them reflectively.

### Meta-annotations
| Meta-annotation | Values | Meaning |
|---|---|---|
| `@Retention` | `SOURCE` (discarded by compiler), `CLASS` (in the `.class`, not loaded; the default), `RUNTIME` (readable via reflection) | How long it lives |
| `@Target` | `TYPE, FIELD, METHOD, PARAMETER, CONSTRUCTOR, LOCAL_VARIABLE, ANNOTATION_TYPE, PACKAGE, TYPE_PARAMETER, TYPE_USE, MODULE, RECORD_COMPONENT` | Where it may appear |
| `@Inherited` | – | A class-level annotation is seen on subclasses too (classes only, not interfaces or methods) |
| `@Documented` | – | Shown in Javadoc |
| `@Repeatable(Container.class)` | – | Java 8: the same annotation several times on one element |

- Frameworks need `RUNTIME`; forgetting it is the number one reason a custom annotation "does nothing". `CLASS` retention is for bytecode tools (e.g. nullability checkers reading library jars).

### Kinds of annotation
- **Declaration annotations** mark a declaration (`@Deprecated class X`).
- **Type annotations** (Java 8, `TYPE_USE`) mark any use of a type: `List<@NonNull String>`, `new @Interned Object()`, casts. Checker Framework and JSpecify use them.
- **Meta-annotations** annotate annotation declarations (the table above). Spring builds *composed annotations* this way: `@RestController` is `@Controller` + `@ResponseBody`, and `@SpringBootApplication` stacks three.

### Built-in ones you meet daily
- `@Override` (compile-time check), `@Deprecated(since, forRemoval)`, `@SuppressWarnings("unchecked")`, `@FunctionalInterface` ([[functional-interfaces]]), `@SafeVarargs`.
- Framework: `@Autowired`/`@Component` ([[spring-ioc-and-di]]), `@Transactional` ([[spring-transactional]]), `@Entity`/`@Id` ([[jpa-entities-and-relationships]]), `@NotNull`/`@Valid` (Bean Validation, [[java-ee-and-jakarta]]), `@Test`.

### How they are processed
- **Compile time**: annotation processors (`javax.annotation.processing.Processor`, JSR 269) run inside `javac`, read `SOURCE`/`CLASS` annotations and generate new source files. Lombok goes further and patches the AST, which is why it is fragile across JDK versions. MapStruct, Dagger, Immutables and Spring Boot's configuration-metadata processor are regular processors.
- **Run time**: `element.getAnnotation(X.class)` / `isAnnotationPresent` via [[object-class-and-reflection]]. Spring scans the classpath for `@Component`, reads `@Transactional` to decide which beans to proxy ([[spring-aop]]), Jackson reads `@JsonProperty`.
- Spring's `AnnotationUtils`/`MergedAnnotations` also follow meta-annotations and `@AliasFor`, which plain reflection does not.

## Example
```java
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface Audited {
    String action();
    boolean includeArgs() default false;
}

// Reading it at runtime (what an AOP aspect or a hand-rolled proxy would do)
public final class AuditInvoker {
    public static Object invoke(Object target, Method m, Object... args) throws Exception {
        Audited audited = m.getAnnotation(Audited.class);
        if (audited != null) {
            String details = audited.includeArgs() ? Arrays.toString(args) : "";
            System.out.printf("AUDIT %s %s%n", audited.action(), details);
        }
        return m.invoke(target, args);
    }
}

public class TariffService {
    @Audited(action = "tariff.change", includeArgs = true)
    public void changeTariff(String msisdn, String tariffCode) { /* ... */ }
}
```

## Pitfalls
- Spring annotations on `private` methods or on self-invoked methods (`this.changeTariff()`) are ignored because the proxy is bypassed ([[spring-aop]]).
- `@Inherited` does not apply to method annotations: an overriding method must repeat `@Transactional`; Spring partially compensates by looking at the interface/superclass method, but do not rely on it.
- Annotation element values must be compile-time constants; `@Scheduled(cron = someVariable)` only works with a `${property}` placeholder.
- Reflection-based processing is invisible to GraalVM native-image unless reachability metadata is generated; Spring Boot 3 AOT does this for its own annotations.
- Related: [[object-class-and-reflection]], [[spring-aop]], [[spring-bean-scopes-and-configuration]].
