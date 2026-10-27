---
title: Структурные паттерны проектирования
tags: [design-patterns, structural, adapter, decorator, proxy]
learned: 2026-10-22
---
## Кратко
Структурные паттерны отвечают за **сборку объектов и классов в более крупные структуры, которые остаются простыми в сопровождении**. Адаптер переводит один интерфейс в другой, Декоратор и Заместитель оборачивают объект с тем же интерфейсом (чтобы добавить поведение или контролировать доступ), Мост отделяет абстракцию от реализации, Фасад прячет подсистему за простой точкой входа, Компоновщик работает с деревом как с одним объектом, Легковес разделяет общее состояние ради экономии памяти.

## Основное
| Паттерн | Назначение | Пример в JDK / Spring |
|---|---|---|
| **Адаптер** (Adapter) | Позволить объектам с несовместимыми интерфейсами работать вместе: переводчик, который преобразует интерфейс или данные одного объекта в форму, понятную другому. | `Arrays.asList()`, `InputStreamReader` (байты в символы), `Collections.enumeration()`; `HandlerAdapter` в Spring MVC ([[spring-mvc-request-lifecycle]]), `WebMvcConfigurerAdapter` (удалён в 5.x) |
| **Декоратор** (Decorator) | Динамически добавлять поведение объекту, помещая его в обёртку, реализующую тот же интерфейс, без взрыва подклассов. Обёртки имеют общего предка с оборачиваемым объектом. | `BufferedInputStream(new FileInputStream(f))` ([[java-io-streams]]), `Collections.unmodifiableList()`, `Collections.synchronizedMap()`; `BeanPostProcessor`, оборачивающий бины, `TransactionAwareCacheDecorator` |
| **Заместитель** (Proxy) | Подставить объект-заменитель, который перехватывает вызовы к настоящему и что-то делает до или после передачи (ленивая инициализация, контроль доступа, логирование, удалённый вызов). | `java.lang.reflect.Proxy`; прокси Spring AOP за `@Transactional`, `@Cacheable`, `@Async` ([[spring-aop]], [[spring-transactional]]); прокси ленивой загрузки Hibernate ([[jpa-persistence-context-and-entity-lifecycle]]) |
| **Мост** (Bridge) | Разделить один или несколько классов на две отдельные иерархии — абстракцию и реализацию, чтобы обе могли меняться независимо. | JDBC: API `DriverManager` / `Connection` и драйверы вендоров; API SLF4J и привязка к Logback / Log4j2 |
| **Фасад** (Facade) | Дать простой интерфейс к сложной системе, библиотеке или фреймворку. Он может открывать урезанный интерфейс — не 100% подсистемы, а ровно то, что нужно клиенту. | `javax.faces.context.FacesContext`, `Executors`; `JdbcTemplate`, `RestClient`, `KafkaTemplate` ([[kafka-producer]]), `SpringApplication.run()` |
| **Компоновщик** (Composite) | Группировать объекты в дерево и работать с деревом как с одним объектом. Имеет смысл, только когда модель по природе — дерево. | `Container` и `Component` в AWT/Swing; `CompositeCacheManager`, `CompositeMessageConverter` в Spring |
| **Легковес** (Flyweight) | Экономить память, разделяя общее (внутреннее) состояние между множеством объектов вместо хранения одинаковых данных в каждом. | Кэш `Integer.valueOf()` (-128..127) ([[primitives-and-wrappers]]), пул строк ([[strings-and-string-pool]]), `Boolean.TRUE` |

### Как различать обёртки
- **Адаптер** меняет интерфейс; **Декоратор** сохраняет интерфейс и добавляет поведение; **Заместитель** сохраняет интерфейс и контролирует доступ. Декоратор собирает клиент («набери стопку того, что нужно»); Заместитель обычно управляет жизненным циклом своей цели, и клиент не знает о его существовании.
- **Адаптер** нужен, когда у стороннего класса правильное поведение, но неправильный интерфейс, или когда нескольким существующим подклассам не хватает общей функциональности, а расширить суперкласс нельзя.
- **Фасад** нужен, когда используется сложная библиотека с множеством движущихся частей, а нужна лишь часть её возможностей.
- Прокси Spring объясняют классический баг: `@Transactional` на методе, вызванном из того же класса, ничего не делает, потому что вызов обходит прокси ([[spring-transactional]]).

## Пример
```java
// Декоратор: добавить retry любому PaymentGateway, не трогая реализации.
public interface PaymentGateway {
    PaymentResult charge(Money amount, CardToken token);
}

public final class RetryingGateway implements PaymentGateway {
    private final PaymentGateway delegate;
    private final int maxAttempts;

    public RetryingGateway(PaymentGateway delegate, int maxAttempts) {
        this.delegate = delegate; this.maxAttempts = maxAttempts;
    }

    @Override
    public PaymentResult charge(Money amount, CardToken token) {
        TransientGatewayException last = null;
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            try {
                return delegate.charge(amount, token);        // тот же интерфейс, дополнительное поведение
            } catch (TransientGatewayException e) {
                last = e;
            }
        }
        throw last;
    }
}

@Configuration
class GatewayConfig {
    @Bean PaymentGateway paymentGateway(HttpGateway http, MeterRegistry metrics) {
        return new MeteredGateway(new RetryingGateway(http, 3), metrics);   // декораторы складываются стопкой
    }
}
```

## Подводные камни
- Retry не-[[idempotency|идемпотентного]] вызова (как списание выше) требует ключа идемпотентности, иначе декоратор спишет дважды.
- Глубокие стопки декораторов делают стек-трейсы и отладку мучительными; обёртки нужно называть понятно, а порядок задавать явно в одном классе конфигурации.
- Прокси ломают вызовы через `this`, `final`-классы/методы (CGLIB не может от них наследоваться) и доступ к полям; прокси Hibernate ломают `equals` при сравнении по классу ([[equals-and-hashcode]]).
- Фасады склонны разрастаться в god-классы; их стоит держать тонкими и делегировать.
- Связанные заметки: [[creational-patterns]], [[behavioral-patterns]], [[solid]].
