---
title: Spring AOP: аспекты, прокси и их ограничения
tags: [spring-core, aop, proxies, cglib]
learned: 2026-10-22
---
## Кратко
**Аспектно-ориентированное программирование (AOP)** выносит *сквозную функциональность (cross-cutting concerns)* — логирование, транзакции, проверки безопасности, метрики, retry — из бизнес-кода в отдельные модули, которые называются аспектами. Spring AOP реализует это во время выполнения с помощью **прокси**: контейнер оборачивает бин в динамический прокси JDK или в подкласс CGLIB, и прокси выполняет advice до, после или вокруг реального метода. Всё, что Spring делает декларативно через аннотации (`@Transactional`, `@Async`, `@Cacheable`, `@PreAuthorize`, `@Retryable`), работает на этом механизме.

## Основное
### Терминология
- **Сквозная функциональность (cross-cutting concern)**: логика, которая нужна во многих не связанных между собой местах; без AOP она *разбросана* (scattered) по модулям и *перепутана* (tangled) с бизнес-логикой.
- **Аспект (aspect)**: класс (`@Aspect`, зарегистрированный как бин), содержащий advice и pointcut'ы.
- **Точка соединения (join point)**: место в ходе выполнения, где может сработать advice; в Spring AOP это всегда *выполнение метода* бина (в отличие от полноценного AspectJ, нет join point'ов на доступ к полю или конструктор).
- **Срез (pointcut)**: выражение, выбирающее join point'ы, например `execution(* kg.beeline.esim.service.*.*(..))` или `@annotation(Audited)`.
- **Совет (advice)**: код, который нужно выполнить. Типы: `@Before`, `@AfterReturning`, `@AfterThrowing`, `@After` (аналог finally), `@Around` (получает `ProceedingJoinPoint` и решает, вызывать ли `proceed()` и как).
- **Связывание (weaving)**: соединение аспектов с кодом. Spring связывает во время выполнения через проксирование; AspectJ умеет связывать на этапе компиляции или загрузки классов и не имеет ограничений прокси.

### Динамический прокси JDK и CGLIB
| | Динамический прокси JDK | CGLIB |
|---|---|---|
| Механизм | `java.lang.reflect.Proxy` реализует **интерфейсы** бина | генерирует **подкласс** класса бина во время выполнения |
| Требования | бин реализует хотя бы один интерфейс; точка внедрения должна иметь тип интерфейса | класс и методы с advice не `final`; видимый конструктор без аргументов больше не нужен (Objenesis) |
| Стоимость | рефлексия при каждом вызове | создаётся чуть медленнее, вызовы идут через `MethodInterceptor`; на практике это не проблема |
| По умолчанию | Spring Framework: JDK, если есть интерфейсы | **Spring Boot по умолчанию использует CGLIB** (`spring.aop.proxy-target-class=true`) начиная с 2.0 |

### Как проходит вызов
`caller → proxy → [advice chain] → target.method()`. Прокси хранит ссылку на реальный объект; реальный объект ничего не знает о прокси.

### Как включить
`spring-boot-starter-aop` в Boot (аннотации AspectJ + `spring-aop`) автоматически включает `@EnableAspectJAutoProxy`. `@Transactional` и `@Async` работают и без стартера, потому что используют собственную инфраструктуру прокси Spring (`InfrastructureAdvisorAutoProxyCreator`).

## Пример
```java
@Aspect
@Component
public class TimingAspect {

    private final MeterRegistry registry;
    TimingAspect(MeterRegistry registry) { this.registry = registry; }

    @Around("@annotation(timed)")
    public Object time(ProceedingJoinPoint pjp, Timed timed) throws Throwable {
        var sample = Timer.start(registry);
        try {
            return pjp.proceed();
        } finally {
            sample.stop(registry.timer(timed.value(),
                    "method", pjp.getSignature().toShortString()));
        }
    }
}

@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.METHOD)
public @interface Timed { String value(); }

@Service
public class EsimOrderService {
    @Timed("esim.order.activate")
    public void activate(String iccid) { /* ... */ }
}
```

## Подводные камни
- **Вызов самого себя (self-invocation) обходит прокси.** `this.activate()` внутри того же бина — обычный Java-вызов: ни транзакции, ни замера времени, ни кэширования. Решения: перенести метод в другой бин, внедрить бин в самого себя через `ObjectProvider`/`@Lazy` (некрасиво) или использовать связывание AspectJ.
- По умолчанию advice применяется только к `public`-методам (CGLIB может перехватывать `protected` и package-private, но `@Transactional` в Spring их игнорирует, если в Spring 6.0+ не настроено иначе).
- `final`-классы и методы CGLIB проксировать не может; для `final`-метода аспект молча пропускается.
- Порядок аспектов важен, когда их применяется несколько (`@Order`); например, аспект retry должен находиться *снаружи* транзакции, чтобы каждая попытка получала новую транзакцию.
- Тип прокси и приведение типов: с прокси JDK `(EsimOrderService) bean` бросает `ClassCastException`; внедряй интерфейс.
- Исключения, брошенные из advice `@Around`, заменяют исходное; пробрасывай их аккуратно.
- Связанные заметки: [[spring-transactional]] — самый важный аспект, которым ты пользуешься каждый день; [[spring-bean-lifecycle]] показывает, на каком шаге создаётся прокси; [[structural-patterns]] (Proxy, Decorator) и [[object-class-and-reflection]] — про лежащие в основе механизмы Java.
