---
title: Порождающие паттерны проектирования
tags: [design-patterns, creational, singleton, builder, factory]
learned: 2026-10-22
---
## Кратко
Паттерны проектирования — повторяющиеся решения типичных задач проектирования: готовые, стандартизированные, общий словарь программистов. Порождающие паттерны (creational patterns) отвечают за **создание объектов без лишних зависимостей** от конкретных классов: Singleton, Prototype, Builder, Factory Method и Abstract Factory. «Порождающие паттерны рождают объекты, структурные соединяют их, поведенческие учат их общаться».

## Основное
| Паттерн | Назначение | Пример в JDK / Spring |
|---|---|---|
| **Одиночка** (Singleton) | Гарантировать, что у класса один экземпляр, и дать глобальную точку доступа. Скрыть конструктор, открыть статический метод доступа (или enum). | `Runtime.getRuntime()`; каждый бин Spring по умолчанию — singleton *в рамках контейнера* ([[spring-bean-scopes-and-configuration]]) |
| **Прототип** (Prototype) | Копировать объекты, не завися от их конкретного класса; логика копирования живёт в самом объекте, за интерфейсом или абстрактным классом. | `Object.clone()` / `Cloneable`; scope бина `prototype` в Spring создаёт новый экземпляр на каждый `getBean()` |
| **Строитель** (Builder) | Конструировать сложные объекты пошагово; один и тот же код построения даёт разные представления. Выносит конструирование из конструктора с множеством параметров. | `StringBuilder`, `HttpRequest.newBuilder()`, `Stream.builder()`; `UriComponentsBuilder`, `RestClient.builder()`, Lombok `@Builder` |
| **Фабричный метод** (Factory Method) | Определить в суперклассе общий интерфейс создания объектов и дать подклассам решать, какой класс создавать. | `Calendar.getInstance()`, `Collection.iterator()` ([[iterators-and-fail-fast]]), `Executors.newFixedThreadPool()` ([[executors-and-thread-pools]]); `FactoryBean<T>` в Spring |
| **Абстрактная фабрика** (Abstract Factory) | Создавать семейства связанных объектов, не называя их конкретных классов. | `DocumentBuilderFactory`, `XMLInputFactory`; `BeanFactory` / `ApplicationContext` ([[spring-ioc-and-di]]) |

### Когда что использовать
- **Одиночка**: единый общий ресурс (конфигурация, пул соединений вроде [[hikaricp-pool-sizing|HikariCP]]). Важна потокобезопасность: жадное статическое поле, идиома holder-класса или `enum`. Double-checked locking требует `volatile` ([[java-memory-model]]).
- **Прототип**: создание объекта дорого (разобранный шаблон, преднастроенный клиент), а нужно много похожих; копировать дешевле, чем строить заново.
- **Строитель**: код должен создавать разные представления объекта, или у объекта много необязательных параметров (телескопические конструкторы). Распространённый вариант в Java — fluent builder, методы которого возвращают `this`; классический вариант GoF разделяет *Director* и *Builder*.
- **Фабрика**: точные типы и зависимости объектов заранее неизвестны, или нужно дать пользователям библиотеки/фреймворка расширять её часть. Статический фабричный метод (`List.of`, `Optional.of`) — облегчённая версия: он может кэшировать, возвращать подтипы и имеет понятное имя.
- Контейнер Spring — один большой набор порождающих паттернов: методы `@Bean` — фабричные методы, классы `@Configuration` — абстрактные фабрики, а scope-ы бинов выбирают между singleton и prototype ([[spring-bean-lifecycle]]).

## Пример
```java
// Builder с валидацией в build(): неизменяемый результат, читаемое место вызова.
public final class KafkaTopicSpec {
    private final String name;
    private final int partitions;
    private final short replicationFactor;
    private final Duration retention;

    private KafkaTopicSpec(Builder b) {
        this.name = b.name; this.partitions = b.partitions;
        this.replicationFactor = b.replicationFactor; this.retention = b.retention;
    }

    public static Builder named(String name) { return new Builder(name); }

    public static final class Builder {
        private final String name;
        private int partitions = 3;
        private short replicationFactor = 3;
        private Duration retention = Duration.ofDays(7);

        private Builder(String name) { this.name = Objects.requireNonNull(name); }
        public Builder partitions(int n) { this.partitions = n; return this; }
        public Builder replicationFactor(short rf) { this.replicationFactor = rf; return this; }
        public Builder retention(Duration d) { this.retention = d; return this; }

        public KafkaTopicSpec build() {
            if (partitions < 1) throw new IllegalArgumentException("partitions >= 1");
            if (replicationFactor < 1) throw new IllegalArgumentException("rf >= 1");
            return new KafkaTopicSpec(this);
        }
    }
}

// использование
var spec = KafkaTopicSpec.named("esim.activations").partitions(12).retention(Duration.ofDays(30)).build();
```

## Подводные камни
- Написанный вручную Singleton — это глобальная переменная: трудно тестировать, скрытая связанность, и он один на classloader, а не на JVM. Лучше singleton-бин, управляемый контейнером.
- `Cloneable` — сломанный API (нет `clone()` в интерфейсе, по умолчанию поверхностная копия, обходит конструкторы). Вместо Prototype через `clone` лучше копирующий конструктор или статический `copyOf`.
- Builder-ы, разрешающие `build()` на неполном объекте, просто переносят ошибку во время выполнения; валидировать нужно в `build()`. Lombok `@Builder` ничего не валидирует и делает каждое поле необязательным.
- Взрыв фабрик: фабрика на каждый класс с одной реализацией — пустая церемония. Фабрику стоит добавлять, когда есть реальная вариативность.
- Связанные заметки: [[structural-patterns]], [[behavioral-patterns]], [[solid]], [[immutable-objects]].
