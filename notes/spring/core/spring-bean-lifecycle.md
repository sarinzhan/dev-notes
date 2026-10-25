---
title: Bean lifecycle and how the context starts
tags: [spring-core, beans, lifecycle, bean-post-processor]
learned: 2026-10-22
---
## TL;DR
Context startup has two phases. First the container reads configuration into `BeanDefinition`s and lets `BeanFactoryPostProcessor`s modify them. Then, for each singleton, it instantiates the object, injects dependencies, runs `Aware` callbacks, applies `BeanPostProcessor`s around the init callbacks (`@PostConstruct`, `InitializingBean`, `initMethod`), and hands out the possibly proxied result. On shutdown it runs `@PreDestroy` / `DisposableBean` / `destroyMethod`.

## Key points
### Phase 1: bean definitions
1. `ApplicationContext` is created; `@ComponentScan`, `@Configuration` classes and XML are parsed into `BeanDefinition` objects (class name, scope, constructor args, property values, init/destroy methods). No user objects exist yet.
2. **BeanFactoryPostProcessor (BFPP)** runs: it receives the `ConfigurableListableBeanFactory` and may change or add definitions. Built-in examples: `ConfigurationClassPostProcessor` (processes `@Configuration`/`@Bean`/`@Import`), `PropertySourcesPlaceholderConfigurer` (resolves `${...}` in definitions). `BeanDefinitionRegistryPostProcessor` is a sub-interface that can register new definitions.
3. `BeanPostProcessor`s are instantiated and registered before any ordinary bean.

### Phase 2: each bean
1. **Instantiation**: constructor call (or static/instance factory method, i.e. `@Bean`). Constructor arguments are resolved and injected here.
2. **Populate**: setter and field injection (`@Autowired`, `@Value`, `@Inject`, `@Resource`), done by `AutowiredAnnotationBeanPostProcessor` and `CommonAnnotationBeanPostProcessor`.
3. **Aware callbacks**: `BeanNameAware`, `BeanFactoryAware`, `ApplicationContextAware`, `EnvironmentAware`, etc.
4. **`postProcessBeforeInitialization`** of every **BeanPostProcessor (BPP)**. This is where `@PostConstruct` is invoked (`CommonAnnotationBeanPostProcessor`).
5. **Initialization**: `InitializingBean.afterPropertiesSet()`, then `@Bean(initMethod = "...")`.
6. **`postProcessAfterInitialization`** of every BPP. This is where AOP proxies are created (`AnnotationAwareAspectJAutoProxyCreator` wraps the bean for [[spring-aop]], `@Transactional`, `@Async`, `@Cacheable`). What ends up in the context is the proxy, not the original.
7. **Use**: singletons are cached in the singleton registry; prototypes are returned without being tracked.
8. **Destruction** on `context.close()` or JVM shutdown hook: `@PreDestroy`, `DisposableBean.destroy()`, `@Bean(destroyMethod)`. Prototype beans are never destroyed by the container.

### BPP vs BFPP (common interview question)
| | BeanFactoryPostProcessor | BeanPostProcessor |
|---|---|---|
| Runs | once, before any bean is created | twice per bean, around initialization |
| Works on | `BeanDefinition` metadata | the live bean instance |
| Typical use | placeholder resolution, registering definitions, changing scope | annotation processing, validation, wrapping in a proxy |

### Order of callbacks
`@PostConstruct` → `afterPropertiesSet()` → `initMethod`; on destroy `@PreDestroy` → `destroy()` → `destroyMethod`. Prefer `@PostConstruct` / `@PreDestroy` (Jakarta annotations, no Spring import). Lazy singletons (`@Lazy`) and prototypes go through the same steps, just later.

### Where Boot plugs in
`SpringApplication.run()` prepares the `Environment`, prints the banner, creates the context, runs `ApplicationContextInitializer`s, registers the main class, calls `refresh()` (everything above), then runs `ApplicationRunner` / `CommandLineRunner` beans. Autoconfiguration classes are just more `@Configuration` sources discovered in phase 1 ([[spring-boot-starters-and-autoconfiguration]]).

## Example
```java
@Component
public class KafkaTopicChecker implements BeanPostProcessor {
    @Override
    public Object postProcessAfterInitialization(Object bean, String beanName) {
        if (bean instanceof KafkaListenerEndpointRegistry registry) {
            // runs for every bean; act only on the one you care about
            log.info("Kafka listeners registered: {}", registry.getListenerContainerIds());
        }
        return bean; // always return the bean (or a wrapper), never null
    }
}
```

## Pitfalls
- Doing heavy work (DB calls, HTTP) in `@PostConstruct` slows startup and runs before the web server accepts traffic; move it to an `ApplicationRunner` or an `@EventListener(ApplicationReadyEvent.class)`.
- `@PostConstruct` runs on the raw object, before the AOP proxy exists, so `@Transactional` on the same bean does not apply inside it.
- A `BeanPostProcessor` and everything it depends on is created early, without the usual post-processing; Boot warns "not eligible for getting processed by all BeanPostProcessors". Keep BPPs dependency-free.
- `@Bean` methods in a `@Configuration` class are intercepted by CGLIB so that calling another `@Bean` method returns the singleton, but only if the class is a real `@Configuration` (not `proxyBeanMethods = false` or a plain `@Component`).
- Related: [[spring-ioc-and-di]], [[spring-bean-scopes-and-configuration]], [[class-initialization-order]] for the JVM-level order that happens before Spring sees the class.
