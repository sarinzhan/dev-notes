---
title: Жизненный цикл бина и запуск контекста
tags: [spring-core, beans, lifecycle, bean-post-processor]
learned: 2026-10-22
---
## Кратко
Запуск контекста проходит в две фазы. Сначала контейнер читает конфигурацию в объекты `BeanDefinition` и даёт `BeanFactoryPostProcessor`'ам их изменить. Затем для каждого singleton'а он создаёт объект, внедряет зависимости, вызывает `Aware`-колбэки, применяет `BeanPostProcessor`'ы вокруг колбэков инициализации (`@PostConstruct`, `InitializingBean`, `initMethod`) и отдаёт результат, возможно обёрнутый в прокси. При завершении работы он вызывает `@PreDestroy` / `DisposableBean` / `destroyMethod`.

## Основное
### Фаза 1: определения бинов
1. Создаётся `ApplicationContext`; `@ComponentScan`, классы `@Configuration` и XML разбираются в объекты `BeanDefinition` (имя класса, scope, аргументы конструктора, значения свойств, методы инициализации и уничтожения). Пользовательских объектов ещё нет.
2. Выполняется **BeanFactoryPostProcessor (BFPP)**: он получает `ConfigurableListableBeanFactory` и может менять или добавлять определения. Встроенные примеры: `ConfigurationClassPostProcessor` (обрабатывает `@Configuration`/`@Bean`/`@Import`), `PropertySourcesPlaceholderConfigurer` (подставляет `${...}` в определениях). `BeanDefinitionRegistryPostProcessor` — подынтерфейс, который может регистрировать новые определения.
3. `BeanPostProcessor`'ы создаются и регистрируются раньше любого обычного бина.

### Фаза 2: каждый бин
1. **Создание экземпляра (instantiation)**: вызов конструктора (или статического/экземплярного фабричного метода, то есть `@Bean`). Здесь же разрешаются и внедряются аргументы конструктора.
2. **Заполнение (populate)**: внедрение через сеттеры и поля (`@Autowired`, `@Value`, `@Inject`, `@Resource`), его выполняют `AutowiredAnnotationBeanPostProcessor` и `CommonAnnotationBeanPostProcessor`.
3. **Aware-колбэки**: `BeanNameAware`, `BeanFactoryAware`, `ApplicationContextAware`, `EnvironmentAware` и т. д.
4. **`postProcessBeforeInitialization`** каждого **BeanPostProcessor (BPP)**. Именно здесь вызывается `@PostConstruct` (`CommonAnnotationBeanPostProcessor`).
5. **Инициализация**: `InitializingBean.afterPropertiesSet()`, затем `@Bean(initMethod = "...")`.
6. **`postProcessAfterInitialization`** каждого BPP. Здесь создаются AOP-прокси (`AnnotationAwareAspectJAutoProxyCreator` оборачивает бин для [[spring-aop]], `@Transactional`, `@Async`, `@Cacheable`). В контекст попадает прокси, а не оригинал.
7. **Использование**: singleton'ы кэшируются в реестре singleton'ов; prototype-бины отдаются без отслеживания.
8. **Уничтожение** при `context.close()` или через shutdown hook JVM: `@PreDestroy`, `DisposableBean.destroy()`, `@Bean(destroyMethod)`. Prototype-бины контейнер никогда не уничтожает.

### BPP и BFPP (частый вопрос на собеседовании)
| | BeanFactoryPostProcessor | BeanPostProcessor |
|---|---|---|
| Когда выполняется | один раз, до создания любого бина | дважды на каждый бин, вокруг инициализации |
| С чем работает | метаданные `BeanDefinition` | живой экземпляр бина |
| Типичное применение | подстановка плейсхолдеров, регистрация определений, смена scope | обработка аннотаций, валидация, обёртка в прокси |

### Порядок колбэков
`@PostConstruct` → `afterPropertiesSet()` → `initMethod`; при уничтожении `@PreDestroy` → `destroy()` → `destroyMethod`. Предпочтительны `@PostConstruct` / `@PreDestroy` (аннотации Jakarta, без импорта из Spring). Ленивые singleton'ы (`@Lazy`) и prototype-бины проходят те же шаги, просто позже.

### Где подключается Boot
`SpringApplication.run()` готовит `Environment`, печатает баннер, создаёт контекст, запускает `ApplicationContextInitializer`'ы, регистрирует главный класс, вызывает `refresh()` (всё описанное выше), а затем выполняет бины `ApplicationRunner` / `CommandLineRunner`. Классы автоконфигурации — это просто дополнительные источники `@Configuration`, найденные в фазе 1 ([[spring-boot-starters-and-autoconfiguration]]).

## Пример
```java
@Component
public class KafkaTopicChecker implements BeanPostProcessor {
    @Override
    public Object postProcessAfterInitialization(Object bean, String beanName) {
        if (bean instanceof KafkaListenerEndpointRegistry registry) {
            // вызывается для каждого бина; действуем только для нужного
            log.info("Kafka listeners registered: {}", registry.getListenerContainerIds());
        }
        return bean; // всегда возвращай бин (или обёртку), никогда не null
    }
}
```

## Подводные камни
- Тяжёлая работа (обращения к БД, HTTP) в `@PostConstruct` замедляет запуск и выполняется до того, как веб-сервер начинает принимать трафик; перенеси её в `ApplicationRunner` или `@EventListener(ApplicationReadyEvent.class)`.
- `@PostConstruct` выполняется на «сыром» объекте, до появления AOP-прокси, поэтому `@Transactional` того же бина внутри него не действует.
- `BeanPostProcessor` и всё, от чего он зависит, создаётся рано, без обычной пост-обработки; Boot предупреждает «not eligible for getting processed by all BeanPostProcessors». Делай BPP без зависимостей.
- Методы `@Bean` в классе `@Configuration` перехватываются CGLIB, чтобы вызов другого метода `@Bean` возвращал singleton, но только если класс — настоящий `@Configuration` (не `proxyBeanMethods = false` и не обычный `@Component`).
- Связанные заметки: [[spring-ioc-and-di]], [[spring-bean-scopes-and-configuration]], [[class-initialization-order]] — порядок на уровне JVM, который выполняется до того, как Spring увидит класс.
