---
title: Чистая архитектура — кольца, правило зависимостей, сценарии использования
tags: [architecture, clean-architecture, design-principles, java]
learned: 2026-10-27
---
## Кратко
Чистая архитектура (clean architecture, Роберт Мартин, 2012) раскладывает код по концентрическим кольцам — сущности, сценарии использования, адаптеры интерфейсов, фреймворки и драйверы — с одним **правилом зависимостей** (dependency rule): зависимости в исходном коде направлены только внутрь; ничто во внутреннем кольце не знает ни одного имени из внешнего. Каждый сценарий использования (use case) — это явный класс-**интерактор** (interactor) с входным портом (что он принимает) и выходным портом (как он сообщает результат). По сути это [[hexagonal-architecture]] с более богатой терминологией и явным слоем сценариев использования; дополнительная церемония окупается в большом, долгоживущем домене и становится ловушкой в сервисе на 2 000 строк.

## Основное
### Кольца
| Кольцо | Что содержит | Аналог в Spring |
|---|---|---|
| Сущности (entities) | Бизнес-правила предприятия: агрегаты, объекты-значения (value objects), доменные сервисы ([[ddd-basics]]) | Чистая Java, без аннотаций |
| Сценарии использования (use cases) | Бизнес-правила приложения: один интерактор на одно намерение пользователя; оркестрирует сущности и шлюзы | Обычные классы, выставленные как бины |
| Адаптеры интерфейсов (interface adapters) | Контроллеры, презентеры, шлюзы: преобразуют данные между форматом сценария и внешним форматом | `@RestController`, реализации `@Repository`, Kafka-листенеры, мапперы |
| Фреймворки и драйверы (frameworks and drivers) | Spring, Hibernate, Kafka-клиент, Temporal SDK, база данных | Стартеры, автоконфигурация ([[spring-boot-starters-and-autoconfiguration]]) |


### Правило зависимостей
- Внутреннее кольцо не может импортировать внешнее или подстраиваться под него: никакого `@Entity` на доменном классе, никаких сценариев, возвращающих `ResponseEntity` или перехватывающих `DataIntegrityViolationException`.
- Данные через границу передаются простыми структурами (records, DTO), которые определяет внутреннее кольцо. Передача JPA-сущности в сценарий использования нарушает правило, даже если код компилируется.
- Переход внутрь против направления потока управления делается через интерфейсы, которыми владеет внутреннее кольцо, а реализует внешнее, — та же инверсия зависимостей, что и у портов в гексагональной архитектуре ([[solid]]).

### Интеракторы сценариев и границы
- **Входной порт** (input port, input boundary): интерфейс с одним методом, например `ActivateProfileUseCase.activate(ActivateProfileCommand)`. Контроллер зависит от него, а не от класса интерактора.
- **Интерактор**: реализация; валидирует команду, загружает сущности через шлюзы, применяет правила, сохраняет, сообщает результат.
- **Выходной порт** (output port, output boundary): либо возвращаемое значение (record с результатом — прагматичный выбор в Java), либо интерфейс презентера, который вызывает интерактор, — исходная форма у Мартина, полезная, когда один сценарий должен выдавать несколько представлений, ничего о них не зная.
- **Шлюзы** (gateways): исходящие интерфейсы, которыми владеет кольцо сценариев (`ProfileRepository`, `SmDpPlusGateway`), а реализуют адаптеры.
- Один интерактор на сценарий делает имена честными: `CancelOrder`, а не `OrderService.cancel` среди 40 других методов ([[layered-architecture]]).

### Чистая vs гексагональная vs луковая
- Гексагональная: две стороны (ведущая и ведомая — driving и driven), порты и адаптеры, внутренняя структура не предписана. Чистая: та же инверсия плюс явное разделение сущностей и сценариев использования и именование входных/выходных портов и презентеров.
- Луковая (onion, Палермо, 2008): то же правило с метками доменная модель → доменные сервисы → сервисы приложения → инфраструктура.
- На практике команды их смешивают: раскладка модулей из гексагональной, именование сценариев из чистой, DDD-агрегаты в центре. На собеседовании важно объяснить правило зависимостей, а не название.

### Ловушка переусложнения
- Маленький CRUD-сервис обрастает `CreateUserCommand`, `CreateUserUseCase`, `CreateUserInteractor`, `CreateUserPresenter`, `UserGateway`, `UserEntity`, `UserJpaEntity`, `UserMapper` — восемь файлов ради одного `INSERT`.
- Это слишком, когда каждый сценарий — «загрузить, поменять поле, сохранить», а в кольце сущностей нет методов. Это оправдано, когда правила меняются независимо от транспорта и хранилища, когда несколько каналов (REST + Kafka + Temporal) запускают один и тот же сценарий или когда домен переживает версию фреймворка.
- Средний путь: сохранить правило зависимостей и домен на чистой Java, отказаться от презентеров, возвращать records из интеракторов, объединить два внешних кольца в один пакет `adapter`.

## Пример
Один сценарий использования с явными границами; контроллер зависит только от входного порта.

```java
// кольцо сценариев использования
public record ActivateProfileCommand(String iccid, String msisdn) {}
public record ActivateProfileResult(String iccid, ProfileState state, Instant activatedAt) {}

public interface ActivateProfileUseCase {                     // входной порт
    ActivateProfileResult activate(ActivateProfileCommand cmd);
}
public interface ProfileRepository {                          // шлюз, которым владеет кольцо сценариев
    Optional<Profile> byIccid(Iccid iccid);
    void save(Profile profile);
}
public interface HlrGateway { void activate(Msisdn msisdn, Iccid iccid); }

public class ActivateProfileInteractor implements ActivateProfileUseCase {
    private final ProfileRepository profiles;
    private final HlrGateway hlr;
    private final Clock clock;

    public ActivateProfileInteractor(ProfileRepository profiles, HlrGateway hlr, Clock clock) {
        this.profiles = profiles; this.hlr = hlr; this.clock = clock;
    }

    @Override
    public ActivateProfileResult activate(ActivateProfileCommand cmd) {
        Iccid iccid = Iccid.of(cmd.iccid());                   // объект-значение проверяет формат
        Profile profile = profiles.byIccid(iccid).orElseThrow(() -> new ProfileNotFound(iccid));
        profile.activate(Msisdn.of(cmd.msisdn()), clock.instant());   // сущность следит, чтобы не активировать дважды
        hlr.activate(profile.msisdn(), iccid);
        profiles.save(profile);
        return new ActivateProfileResult(iccid.value(), profile.state(), profile.activatedAt());
    }
}
```

```java
// кольцо адаптеров интерфейсов
@RestController
@RequestMapping("/profiles")
@RequiredArgsConstructor
class ProfileController {
    private final ActivateProfileUseCase activate;             // интерфейс, никогда не интерактор

    @PostMapping("/{iccid}/activate")
    ActivateProfileResponse activate(@PathVariable String iccid, @RequestBody ActivateRequest body) {
        var result = activate.activate(new ActivateProfileCommand(iccid, body.msisdn()));
        return ActivateProfileResponse.from(result);
    }
}
```

Интерактор тестируется с `ProfileRepository` в памяти, записывающим вызовы `HlrGateway` и фиксированным `Clock`, без Spring-контекста ([[junit-and-mockito]]).

## Подводные камни
- Пометка сущностей `@Entity` «чтобы сэкономить маппер» переносит Hibernate в самое внутреннее кольцо; ленивые прокси и контекст персистентности затем протекают в бизнес-правила ([[jpa-persistence-context-and-entity-lifecycle]]).
- Сценарии, бросающие исключения фреймворка или возвращающие `ResponseEntity`, нарушают правило изнутри; заведи доменные исключения и маппь их в `@ControllerAdvice`.
- `@Transactional` на интеракторе помещает Spring-аннотацию в кольцо сценариев; либо смирись с этим, либо оберни интерактор в транзакционный декоратор в кольце адаптеров ([[spring-transactional]]).
- Избыточная абстракция в маленьких сервисах (см. выше); если единственная реализация интерфейса — однострочная прокидка, удали интерфейс.
- Связанные заметки: [[hexagonal-architecture]] (то же правило, меньше слов), [[layered-architecture]] (то, что она заменяет), [[modular-monolith]] (границы между фичами, а не кольца), [[ddd-basics]].
