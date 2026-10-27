---
title: Гексагональная архитектура — порты и адаптеры
tags: [architecture, hexagonal, ddd, spring, testing]
learned: 2026-10-27
---
## Кратко
Гексагональная архитектура (hexagonal architecture, Алистер Кокберн, 2005, также «порты и адаптеры» — ports and adapters) помещает доменную и прикладную логику в центр и разрешает ей общаться с внешним миром только через **порты** — интерфейсы, которые определяет ядро. **Адаптеры** реализуют эти порты для конкретной технологии: REST, Kafka, JPA, Temporal, клиент SM-DP+. Поскольку интерфейсами владеет ядро, все зависимости направлены внутрь, и ядро можно скомпилировать и протестировать без Spring, базы данных или брокера. Цена — больше интерфейсов и кода маппинга, и она окупается, только если в домене есть настоящие правила.

## Основное
### Порты и адаптеры
- **Порт**: интерфейс в ядре, который описывает потребность или возможность на языке домена (`ReserveProfile`, `OrderRepository`), но никогда не на языке технологии (`JpaOrderRepository`, `KafkaSender`).
- **Ведущая сторона (driving, primary, inbound)**: что-то снаружи вызывает приложение. Порты — это интерфейсы сценариев использования; адаптеры — REST-контроллеры, Kafka-листенеры, workflow и activity Temporal, CLI, задания по расписанию.
- **Ведомая сторона (driven, secondary, outbound)**: приложение вызывает что-то снаружи. Порты — интерфейсы репозиториев и шлюзов; адаптеры — JPA-репозитории, HTTP-клиенты, Kafka-продюсеры, отправители почты.

### Инверсия зависимостей на практике
- Ядро определяет `OrderRepository`, JPA-адаптер его реализует. Зависимость на этапе компиляции идёт адаптер → ядро, хотя вызовы во время выполнения идут ядро → адаптер: это «D» из [[solid]] в масштабе модуля.
- Ядро — чистая Java: никаких `@Entity`, `@Service`, `@Transactional`. Держи его в отдельном модуле Gradle/Maven, чтобы правило соблюдал компилятор ([[maven]]).
- Связывание происходит на краю: Spring-класс `@Configuration` создаёт сервисы ядра и внедряет в них бины адаптеров ([[spring-ioc-and-di]], [[spring-bean-scopes-and-configuration]]).

### Отображение на сервис Spring Boot
| Понятие гексагональной архитектуры | Реализация в Spring |
|---|---|
| Доменная модель | Обычные классы/records с инвариантами ([[ddd-basics]]) |
| Сервис приложения (сценарий использования) | Обычный класс, выставленный как бин из `@Configuration`; граница транзакции |
| Входящий порт | Java-интерфейс, который реализует сервис приложения |
| Входящий адаптер | `@RestController`, `@KafkaListener` ([[spring-kafka-consumer]]), Temporal `@WorkflowMethod` / `@ActivityMethod` ([[temporal-concepts]]) |
| Исходящий порт | Интерфейс в ядре (`ProfileGateway`, `OrderRepository`) |
| Исходящий адаптер | `@Repository` со Spring Data ([[spring-data-repositories]]), `RestClient` ([[rest-clients]]), `KafkaTemplate` ([[kafka-producer]]) |
| Транзакции | `@Transactional` на бине со стороны адаптера или через `TransactionTemplate`, переданный через порт ([[spring-transactional]]) |

- JPA-сущности живут в адаптере и маппятся в доменные объекты и обратно; две модели на одну таблицу — цена домена, который не знает про Hibernate ([[jpa-entities-and-relationships]]).
- В Temporal workflow — это входящий адаптер, который оркестрирует; каждая activity — тонкий адаптер вокруг сценария использования или исходящего порта. Бизнес-правила остаются в ядре.

### Тестирование ядра без инфраструктуры
- Модульные тесты вызывают сервис приложения с фейками исходящих портов в памяти; без Spring-контекста, миллисекунды на тест ([[junit-and-mockito]]).
- Тесты адаптеров отдельные: `@DataJpaTest` с [[testcontainers]] для JPA, `@WebMvcTest` для контроллера, Testcontainers Kafka для листенера.
- Несколько end-to-end-тестов связывают всё вместе; пирамида тестов вытекает из архитектуры ([[testing-types-and-dev-cycles]]).

### Чем она отличается от соседей
- По сравнению с [[layered-architecture]]: там сервис зависит от реализации репозитория; гексагональная архитектура переворачивает эту зависимость.
- По сравнению с [[clean-architecture]] и луковой (Onion): та же идея, но с большим числом колец и явным слоем сценариев использования; гексагональная намеренно минималистична.
- Хорошо сочетается с DDD: агрегаты в центре, репозитории как порты, доменные события через исходящий порт, за которым стоит [[outbox-pattern]].

## Пример
Ядро провижининга eSIM с одним входящим портом, одним доменным сервисом и JPA-адаптером.

```java
// модуль ядра: здесь нет Spring
public interface ProvisionEsim {                                   // входящий порт
    EsimOrderId provision(Msisdn msisdn, TariffCode tariff);
}
public interface EsimOrderRepository {                             // исходящий порт
    EsimOrder save(EsimOrder order);
    Optional<EsimOrder> findPendingFor(Msisdn msisdn);
}
public interface ProfileGateway { Iccid reserve(EsimOrderId id, TariffCode tariff); }

public class ProvisionEsimService implements ProvisionEsim {
    private final EsimOrderRepository orders;
    private final ProfileGateway profiles;
    public ProvisionEsimService(EsimOrderRepository orders, ProfileGateway profiles) { this.orders = orders; this.profiles = profiles; }

    @Override
    public EsimOrderId provision(Msisdn msisdn, TariffCode tariff) {
        orders.findPendingFor(msisdn).ifPresent(o -> { throw new PendingOrderExists(msisdn); });
        EsimOrder order = EsimOrder.create(msisdn, tariff);         // инварианты внутри агрегата
        Iccid iccid = profiles.reserve(order.id(), tariff);
        order.attachProfile(iccid);
        return orders.save(order).id();
    }
}
```

```java
// модуль адаптеров: Spring + JPA
@Component
@RequiredArgsConstructor
class JpaEsimOrderRepository implements EsimOrderRepository {
    private final SpringDataEsimOrderRepository jpa;               // расширяет JpaRepository<EsimOrderEntity, UUID>
    private final EsimOrderMapper mapper;                          // сущность <-> домен

    @Override public EsimOrder save(EsimOrder order) { return mapper.toDomain(jpa.save(mapper.toEntity(order))); }
    @Override public Optional<EsimOrder> findPendingFor(Msisdn msisdn) {
        return jpa.findByMsisdnAndStatus(msisdn.value(), "PENDING").map(mapper::toDomain);
    }
}

@Configuration
class CoreConfig {
    @Bean ProvisionEsim provisionEsim(EsimOrderRepository orders, ProfileGateway profiles) {
        return new ProvisionEsimService(orders, profiles);
    }
}
```

И REST-контроллер, и activity Temporal вызывают `provisionEsim.provision(...)`; модульный тест передаёт репозиторий на основе `HashMap` и фейковый шлюз.

## Подводные камни
- Порты, названные по технологии (`KafkaPublisher`, `JpaOrderPort`), протаскивают адаптер в ядро и делают замену невозможной; называй их по тому, что нужно домену.
- Один обобщённый порт `Repository<T>` с `findAll`/`save`/`delete` затаскивает семантику ORM в домен; ограничивай каждый порт запросами, которые нужны сценариям использования.
- Усталость от маппинга: DTO → команда → домен → сущность → домен → ответ. Для богатых агрегатов с этим стоит смириться; для справочной таблицы хватит обычных слоёв.
- Транзакции, охватывающие исходящий HTTP-вызов: ядро не знает, что находится внутри `@Transactional`, поэтому слой адаптеров должен решать, где граница, а удалённые вызовы должны идти после commit или через [[outbox-pattern]].
- Считать код workflow Temporal доменом: это адаптер с ограничениями replay, и правила, живущие только там, можно протестировать лишь с тестовым сервером Temporal.
- Связанные заметки: [[ddd-basics]], [[clean-architecture]], [[modular-monolith]], [[esim-provisioning-platform]] (где эту форму опробовали на практике), [[spring-ioc-and-di]], [[temporal-concepts]].
