---
title: Spring Boot: стартеры, автоконфигурация и @SpringBootApplication
tags: [spring-boot, autoconfiguration, starters, conditional]
learned: 2026-10-22
---
## Кратко
Spring Boot — это слой поверх Spring Framework, который делает приложение готовым к запуску почти без настройки: **соглашения вместо конфигурации (convention over configuration)**, **встроенный сервер (embedded server)** (по умолчанию Tomcat) и **автоконфигурация (autoconfiguration)**, которая создаёт бины на основе того, что есть в classpath и в свойствах. **Стартеры (starters)** — это подобранные наборы зависимостей, которые приносят библиотеки и соответствующую им автоконфигурацию. Boot 3.x требует Java 17+, Spring Framework 6 и пространство имён Jakarta EE 9+ (`jakarta.*`).

## Основное
### Ключевые идеи
- **Упрощённая конфигурация**: разумные значения по умолчанию; переопределяется только то, что отличается ([[spring-boot-properties-and-profiles]]).
- **Встроенный сервер**: приложение — это обычный `main()`; Tomcat/Jetty/Undertow работает внутри JVM и упаковывается в fat jar плагином Boot для Maven/Gradle ([[maven]]). Нет внешнего контейнера сервлетов, в который нужно делать deploy.
- **Автоконфигурация**: Boot анализирует classpath и окружение и регистрирует бины, которые ты не объявлял: `DataSource`, если есть JDBC-драйвер и `spring.datasource.url`, `KafkaTemplate`, если присутствует `spring-kafka`, `ObjectMapper`, `DispatcherServlet` и т. д.
- **Classpath**: набор каталогов и jar-файлов, в которых JVM (или инструмент сборки) ищет `.class`-файлы и ресурсы во время выполнения. Автоконфигурация отталкивается от него: «можно ли загрузить `org.postgresql.Driver`?».

### @SpringBootApplication
Составная аннотация из трёх аннотаций на главном классе:
- `@SpringBootConfiguration` (это `@Configuration`), чтобы сам класс мог объявлять `@Bean`.
- `@ComponentScan`: сканирует пакет главного класса и его подпакеты в поисках стереотипов. Главный класс кладут в корневой пакет; класс вне этого пакета не будет найден.
- `@EnableAutoConfiguration`: импортирует классы автоконфигурации, перечисленные в файле `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports` каждого jar (Boot 2.7+; до этого — `spring.factories`). Исключить одну можно через `exclude = DataSourceAutoConfiguration.class` или `spring.autoconfigure.exclude`.

### Стартеры
Стартер — это pom без собственного кода: он подтягивает согласованный набор библиотек совместимых версий (ими управляет `spring-boot-dependencies`). Примеры: `spring-boot-starter-web` (Spring MVC + Jackson + встроенный Tomcat), `-data-jpa` (Hibernate + Spring Data + HikariCP), `-security`, `-validation`, `-actuator`, `-test`. Сторонние библиотеки следуют соглашению `<name>-spring-boot-starter`. Стартер вместе со своей автоконфигурацией и даёт ощущение «просто добавь зависимость, и всё работает».

### Как автоконфигурация принимает решения: @Conditional
`@Conditional(SomeCondition.class)` принимает `Condition` с единственным методом `boolean matches(ConditionContext, AnnotatedTypeMetadata)`:
- `ConditionContext` даёт доступ к `BeanDefinitionRegistry`, `BeanFactory`, `Environment`, `ResourceLoader` и `ClassLoader`.
- `AnnotatedTypeMetadata` предоставляет аннотации целевого класса или метода.

Производные аннотации Boot:
| Аннотация | Истинна, когда |
|---|---|
| `@ConditionalOnClass` / `OnMissingClass` | класс есть (нет) в classpath |
| `@ConditionalOnBean` / `OnMissingBean` | бин этого типа/имени уже определён (не определён); именно так твой собственный `@Bean` переопределяет бин по умолчанию |
| `@ConditionalOnProperty` | свойство имеет заданное значение (`matchIfMissing` для значений по умолчанию) |
| `@ConditionalOnResource` | ресурс существует (`classpath:...`) |
| `@ConditionalOnJava` | версия JVM подходит |
| `@ConditionalOnWebApplication` | веб-приложение на сервлетах или реактивное |
| `@ConditionalOnExpression` | SpEL-выражение истинно |

Классы автоконфигурации обрабатываются **после** пользовательской конфигурации, поэтому `@ConditionalOnMissingBean` позволяет твоему бину победить. Аннотация `@AutoConfiguration(after = ..., before = ...)` (Boot 2.7+) задаёт их порядок. Запуск с `--debug` или эндпоинт actuator `conditions` показывают «отчёт об оценке условий» (condition evaluation report): какие автоконфигурации сработали и почему.

### Последовательность запуска одной строкой
`SpringApplication.run()` → построить `Environment` → создать `ApplicationContext` → зарегистрировать главный класс → `refresh()` (сканирование, автоконфигурация, создание бинов, см. [[spring-bean-lifecycle]]) → запустить встроенный сервер → выполнить `ApplicationRunner`/`CommandLineRunner`.

## Пример
Небольшая автоконфигурация для внутренней библиотеки (паттерн, который использует каждый стартер):
```java
@AutoConfiguration
@ConditionalOnClass(TelegramClient.class)
@ConditionalOnProperty(prefix = "alerts.telegram", name = "enabled", havingValue = "true")
@EnableConfigurationProperties(TelegramProps.class)
public class TelegramAlertsAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    TelegramClient telegramClient(TelegramProps props, RestClient.Builder builder) {
        return new TelegramClient(builder.baseUrl(props.baseUrl()).build(), props.botToken());
    }
}
```
Регистрируется в `src/main/resources/META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`:
```
kg.beeline.alerts.TelegramAlertsAutoConfiguration
```

## Подводные камни
- Если положить главный класс в глубокий пакет (`com.x.app.boot`), `@ComponentScan` пропустит `com.x.app.service`; перенеси класс или задай `scanBasePackages`.
- Автоконфигурация — не магия: если бина не хватает, читай отчёт об условиях, а не добавляй случайные аннотации.
- Ручное смешивание версий стартеров ломает согласованность версий, которую даёт BOM; пусть ими управляет `spring-boot-dependencies`.
- Boot 3 перенёс всё из `javax.*` в `jakarta.*`; старые библиотеки, которые всё ещё используют `javax.servlet` или `javax.persistence`, не работают ([[java-ee-and-jakarta]]).
- Регистрация автоконфигурации через `spring.factories` удалена в Boot 3.0; используй файл `.imports`.
- Связанные заметки: [[spring-boot-properties-and-profiles]], [[spring-bean-scopes-and-configuration]], [[spring-ioc-and-di]].
