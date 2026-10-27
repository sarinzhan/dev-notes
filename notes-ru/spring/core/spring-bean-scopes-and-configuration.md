---
title: Области видимости бинов и способы их конфигурации
tags: [spring-core, beans, scopes, configuration]
learned: 2026-10-22
---
## Кратко
**Область видимости (scope)** бина определяет, сколько экземпляров создаёт контейнер и сколько они живут: `singleton` (по умолчанию, один на контекст), `prototype` (новый объект на каждый запрос к контейнеру) и веб-области `request`, `session`, `application`, `websocket`. Бины объявляются одним из трёх способов: `<bean>` в XML, сканирование classpath в поисках аннотаций-стереотипов (`@Component` и родственные) или Java-конфигурация (методы `@Bean` внутри `@Configuration`). Современный код использует два последних.

## Основное
### Области видимости
| Scope | Экземпляры | Примечания |
|---|---|---|
| `singleton` | один на `ApplicationContext` | по умолчанию; создаётся сразу при запуске, если нет `@Lazy`; должен быть потокобезопасным (без состояния) |
| `prototype` | новый при каждом `getBean()` / внедрении | контейнер его не отслеживает, поэтому `@PreDestroy` никогда не вызывается |
| `request` | один на HTTP-запрос | только в веб-контексте |
| `session` | один на HTTP-сессию | только в веб-контексте |
| `application` | один на `ServletContext` | отличается от singleton, когда несколько контекстов делят один servlet context |
| `websocket` | один на WebSocket-сессию | нужен `spring-websocket` |

- Объявляется через `@Scope("prototype")` или сокращения `@RequestScope`, `@SessionScope`, `@ApplicationScope`.
- **Prototype внутри singleton**: singleton получает один экземпляр prototype в момент внедрения и хранит его. Чтобы получать новый экземпляр при каждом вызове, внедряют `ObjectProvider<T>` / `ObjectFactory<T>`, используют `@Lookup` на абстрактном методе или объявляют бин с `proxyMode = ScopedProxyMode.TARGET_CLASS`, чтобы прокси находил реальный экземпляр при каждом вызове метода. Тот же приём с прокси позволяет внедрить бин со scope `request` в singleton-контроллер.
- Можно создавать собственные scope, реализовав `Scope` и зарегистрировав его через `ConfigurableBeanFactory.registerScope`.

### Стили конфигурации
- **XML**: `<bean id="..." class="...">` с `<constructor-arg>` / `<property>`. Устаревший способ, всё ещё встречается в старом корпоративном коде; загружается через `ClassPathXmlApplicationContext` или `@ImportResource`.
- **Аннотации + сканирование компонентов**: `@Component` — общий стереотип; `@Service`, `@Repository`, `@Controller`, `@RestController` — его специализации. `@Repository` дополнительно преобразует исключения слоя хранения в иерархию `DataAccessException` из Spring; `@Controller` — то, что ищет `DispatcherServlet` ([[spring-mvc-request-lifecycle]]). `@ComponentScan` (входит в `@SpringBootApplication`) сканирует пакет аннотированного класса и все подпакеты.
- **Java-конфигурация**: класс `@Configuration` с методами `@Bean`. Используется для сторонних классов, которые нельзя пометить аннотациями (`ObjectMapper`, `RestClient`, `DataSource`), и для бинов, которым нужна логика при создании. Имя метода `@Bean` становится именем бина; параметры метода внедряются. `@Import` подключает другие конфигурационные классы.
- Смешивать стили нормально: сканирование для своего кода, `@Bean` для библиотек.

### Имена бинов и поиск
- Имя по умолчанию — простое имя класса с маленькой буквы (`orderService`) или имя метода `@Bean`; переопределяется через `@Component("name")` / `@Bean(name = ...)`.
- `@Qualifier`, `@Primary`, `@Order` и `@Profile` решают проблему «больше одного бина этого типа» ([[spring-ioc-and-di]], [[spring-boot-properties-and-profiles]]).
- `@Lazy` на singleton'е откладывает создание до первого использования; на точке внедрения — внедряет прокси с ленивым разрешением.

## Пример
```java
@Configuration(proxyBeanMethods = false)
public class HttpClientsConfig {

    @Bean
    RestClient esimVendorClient(RestClient.Builder builder, EsimVendorProps props) {
        return builder.baseUrl(props.baseUrl())
                      .defaultHeader("X-Api-Key", props.apiKey())
                      .build();
    }

    @Bean
    @Scope(value = "prototype")
    ProvisioningJob provisioningJob() {      // с состоянием, по одному на запуск workflow
        return new ProvisioningJob();
    }
}

@Service
public class ProvisioningService {
    private final ObjectProvider<ProvisioningJob> jobs;
    ProvisioningService(ObjectProvider<ProvisioningJob> jobs) { this.jobs = jobs; }

    void start(String iccid) { jobs.getObject().run(iccid); } // каждый раз новый job
}
```

## Подводные камни
- Изменяемые поля singleton'а общие для всех потоков: классический источник состояний гонки (race conditions) ([[concurrency-hazards]]). Делай singleton'ы без состояния или используй потокобезопасное состояние.
- `@Scope("prototype")` на `@Service`, который напрямую внедряется в singleton, ничего полезного не даёт: экземпляр всё равно будет один.
- Обращение к бину со scope `request` из потока вне запроса (`@Async`, Kafka listener, планировщик) бросает «No thread-bound request found».
- Классы `@Configuration` наследуются через CGLIB, поэтому не могут быть `final`, а методы `@Bean` не могут быть `private` или `final`. `proxyBeanMethods = false` отключает это и немного ускоряет запуск, но тогда вызовы одних методов `@Bean` из других создают новые объекты.
- Два бина с одинаковым именем в чистом Spring молча переопределяют друг друга; Boot 2.1+ падает с `BeanDefinitionOverrideException`, если не задано `spring.main.allow-bean-definition-overriding=true`.
- Связанные заметки: [[spring-bean-lifecycle]], [[spring-boot-starters-and-autoconfiguration]].
