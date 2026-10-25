---
title: Bean scopes and ways to configure beans
tags: [spring-core, beans, scopes, configuration]
learned: 2026-10-22
---
## TL;DR
A bean's **scope** says how many instances the container creates and how long they live: `singleton` (default, one per context), `prototype` (new object per request to the container), and the web scopes `request`, `session`, `application`, `websocket`. Beans are declared in one of three ways: XML `<bean>`, classpath scanning of stereotype annotations (`@Component` and friends), or Java config (`@Bean` methods inside `@Configuration`). Modern code uses the last two.

## Key points
### Scopes
| Scope | Instances | Notes |
|---|---|---|
| `singleton` | one per `ApplicationContext` | default; created eagerly at startup unless `@Lazy`; must be thread-safe (stateless) |
| `prototype` | new one on every `getBean()` / injection | container does not track it, so `@PreDestroy` never runs |
| `request` | one per HTTP request | web context only |
| `session` | one per HTTP session | web context only |
| `application` | one per `ServletContext` | differs from singleton when several contexts share one servlet context |
| `websocket` | one per WebSocket session | needs `spring-websocket` |

- Declare with `@Scope("prototype")` or the shortcuts `@RequestScope`, `@SessionScope`, `@ApplicationScope`.
- **Prototype inside singleton**: the singleton receives one prototype instance at injection time and keeps it. To get a fresh one per call inject `ObjectProvider<T>` / `ObjectFactory<T>`, use `@Lookup` on an abstract method, or declare the scoped bean with `proxyMode = ScopedProxyMode.TARGET_CLASS` so a proxy resolves the real instance on every method call. The same proxy trick is how a `request`-scoped bean can be injected into a singleton controller.
- Custom scopes are possible by implementing `Scope` and registering it with `ConfigurableBeanFactory.registerScope`.

### Configuration styles
- **XML**: `<bean id="..." class="...">` with `<constructor-arg>` / `<property>`. Legacy, still seen in old enterprise code; loaded with `ClassPathXmlApplicationContext` or `@ImportResource`.
- **Annotations + component scanning**: `@Component` is the generic stereotype; `@Service`, `@Repository`, `@Controller`, `@RestController` are specializations. `@Repository` additionally translates persistence exceptions into Spring's `DataAccessException` hierarchy; `@Controller` is what `DispatcherServlet` looks for ([[spring-mvc-request-lifecycle]]). `@ComponentScan` (included in `@SpringBootApplication`) scans the package of the annotated class and all sub-packages.
- **Java config**: `@Configuration` class with `@Bean` methods. Use it for third-party classes you cannot annotate (`ObjectMapper`, `RestClient`, `DataSource`) and for beans that need constructor logic. `@Bean` method name is the bean name; the method's parameters are injected. `@Import` pulls in other configuration classes.
- Mixed is normal: scan your own code, `@Bean` for libraries.

### Bean naming and lookup
- Default name is the decapitalized simple class name (`orderService`) or the `@Bean` method name; override with `@Component("name")` / `@Bean(name = ...)`.
- `@Qualifier`, `@Primary`, `@Order` and `@Profile` resolve the "more than one bean of this type" problem ([[spring-ioc-and-di]], [[spring-boot-properties-and-profiles]]).
- `@Lazy` on a singleton defers creation to first use; on an injection point it injects a lazy-resolving proxy.

## Example
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
    ProvisioningJob provisioningJob() {      // stateful, one per workflow run
        return new ProvisioningJob();
    }
}

@Service
public class ProvisioningService {
    private final ObjectProvider<ProvisioningJob> jobs;
    ProvisioningService(ObjectProvider<ProvisioningJob> jobs) { this.jobs = jobs; }

    void start(String iccid) { jobs.getObject().run(iccid); } // new job each time
}
```

## Pitfalls
- Mutable fields in a singleton are shared by all threads: a classic source of race conditions ([[concurrency-hazards]]). Keep singletons stateless or use thread-safe state.
- `@Scope("prototype")` on a `@Service` injected directly into a singleton does nothing useful; you still get one instance.
- A request-scoped bean accessed from a non-request thread (`@Async`, Kafka listener, scheduler) throws "No thread-bound request found".
- `@Configuration` classes are CGLIB-subclassed, so they cannot be `final` and `@Bean` methods cannot be `private` or `final`. `proxyBeanMethods = false` turns this off and makes startup slightly faster, but then inter-`@Bean` method calls create new objects.
- Two beans with the same name silently override each other in plain Spring; Boot 2.1+ fails with `BeanDefinitionOverrideException` unless `spring.main.allow-bean-definition-overriding=true`.
- Related: [[spring-bean-lifecycle]], [[spring-boot-starters-and-autoconfiguration]].
