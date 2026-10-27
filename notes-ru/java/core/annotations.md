---
title: Аннотации
tags: [annotations, metadata, retention, reflection, annotation-processing]
learned: 2026-10-22
---
## Кратко
Аннотация — это структурированные метаданные, прикреплённые к пакету, типу, члену класса, параметру, локальной переменной или месту использования типа. Сама по себе она ничего не делает; её читает инструмент: компилятор (`@Override`), обработчик аннотаций (annotation processor) во время сборки (Lombok, MapStruct) или фреймворк во время выполнения через рефлексию (Spring, JPA, Jackson). `@Retention` определяет, как долго аннотация сохраняется, а `@Target` — где её можно писать.

## Основное
### Объявление
- `@interface Name { String value() default ""; int[] codes() default {}; }`. Элементами могут быть примитивы, `String`, `Class`, перечисления, другие аннотации или массивы из них. Элемент с именем `value` можно указывать без имени: `@Role("admin")`.
- Во время выполнения аннотации — это интерфейсы; JVM создаёт для них прокси-экземпляры, когда их читают через рефлексию.

### Мета-аннотации
| Мета-аннотация | Значения | Смысл |
|---|---|---|
| `@Retention` | `SOURCE` (отбрасывается компилятором), `CLASS` (есть в `.class`, но не загружается; по умолчанию), `RUNTIME` (доступна через рефлексию) | Как долго живёт |
| `@Target` | `TYPE, FIELD, METHOD, PARAMETER, CONSTRUCTOR, LOCAL_VARIABLE, ANNOTATION_TYPE, PACKAGE, TYPE_PARAMETER, TYPE_USE, MODULE, RECORD_COMPONENT` | Где может стоять |
| `@Inherited` | – | Аннотация уровня класса видна и на подклассах (только для классов, не для интерфейсов и методов) |
| `@Documented` | – | Показывается в Javadoc |
| `@Repeatable(Container.class)` | – | Java 8: одна и та же аннотация несколько раз на одном элементе |

- Фреймворкам нужен `RUNTIME`; забытый `RUNTIME` — причина номер один, почему собственная аннотация «ничего не делает». Retention `CLASS` нужен инструментам, работающим с байткодом (например, проверкам nullability, которые читают jar-файлы библиотек).

### Виды аннотаций
- **Аннотации объявлений** помечают объявление (`@Deprecated class X`).
- **Аннотации типов** (Java 8, `TYPE_USE`) помечают любое использование типа: `List<@NonNull String>`, `new @Interned Object()`, приведения. Их используют Checker Framework и JSpecify.
- **Мета-аннотации** аннотируют объявления аннотаций (таблица выше). Так Spring строит *составные аннотации* (composed annotations): `@RestController` — это `@Controller` + `@ResponseBody`, а `@SpringBootApplication` объединяет три.

### Встроенные, которые встречаются каждый день
- `@Override` (проверка при компиляции), `@Deprecated(since, forRemoval)`, `@SuppressWarnings("unchecked")`, `@FunctionalInterface` ([[functional-interfaces]]), `@SafeVarargs`.
- Фреймворки: `@Autowired`/`@Component` ([[spring-ioc-and-di]]), `@Transactional` ([[spring-transactional]]), `@Entity`/`@Id` ([[jpa-entities-and-relationships]]), `@NotNull`/`@Valid` (Bean Validation, [[java-ee-and-jakarta]]), `@Test`.

### Как они обрабатываются
- **Во время компиляции**: обработчики аннотаций (`javax.annotation.processing.Processor`, JSR 269) работают внутри `javac`, читают аннотации `SOURCE`/`CLASS` и генерируют новые исходные файлы. Lombok идёт дальше и правит AST, поэтому он хрупок при смене версий JDK. MapStruct, Dagger, Immutables и обработчик метаданных конфигурации Spring Boot — обычные обработчики.
- **Во время выполнения**: `element.getAnnotation(X.class)` / `isAnnotationPresent` через [[object-class-and-reflection]]. Spring сканирует classpath в поисках `@Component`, читает `@Transactional`, чтобы решить, какие бины проксировать ([[spring-aop]]), Jackson читает `@JsonProperty`.
- `AnnotationUtils`/`MergedAnnotations` в Spring также учитывают мета-аннотации и `@AliasFor`, чего обычная рефлексия не делает.

## Пример
```java
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface Audited {
    String action();
    boolean includeArgs() default false;
}

// Чтение во время выполнения (то, что сделал бы AOP-аспект или самописный прокси)
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

## Подводные камни
- Аннотации Spring на `private`-методах или на методах, вызываемых изнутри самого объекта (`this.changeTariff()`), игнорируются, потому что вызов идёт в обход прокси ([[spring-aop]]).
- `@Inherited` не действует на аннотации методов: переопределяющий метод должен повторить `@Transactional`; Spring частично это компенсирует, заглядывая в метод интерфейса/суперкласса, но полагаться на это не стоит.
- Значения элементов аннотации должны быть константами времени компиляции; `@Scheduled(cron = someVariable)` работает только с плейсхолдером `${property}`.
- Обработка через рефлексию невидима для GraalVM native-image, если не сгенерированы метаданные достижимости (reachability metadata); Spring Boot 3 AOT делает это для своих аннотаций.
- Связанные заметки: [[object-class-and-reflection]], [[spring-aop]], [[spring-bean-scopes-and-configuration]].
