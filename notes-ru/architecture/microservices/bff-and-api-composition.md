---
title: Backend for frontend и композиция API
tags: [microservices, api-design, gateway, bff]
learned: 2026-10-27
---
## Кратко
Когда экрану нужны данные из пяти сервисов, кто-то должен сделать пять вызовов и объединить результаты. **Композиция API** (API composition) делает это в сервисе-компоновщике или в gateway; **backend for frontend (BFF)** — это компоновщик, которым владеет команда одного клиента (web, iOS, партнёрский API), и он возвращает ровно ту форму, которая нужна этому клиенту. Оба подхода держат агрегацию вне клиентов и вне доменных сервисов. Риски одинаковые: компоновщик превращается в болтливый медленный fan-out с N+1 вызовами, в свалку бизнес-логики или в узкое место, которым никто не владеет. Композиция работает для чтения; записи через несколько сервисов — дело [[saga]] или workflow, а не BFF.

## Основное

### Какую проблему решает
- Мобильное приложение показывает страницу абонента: статус SIM (provisioning), баланс (биллинг), тариф (каталог), последние обращения (поддержка). Варианты: приложение вызывает четыре API (медленно на мобильном, четыре потока аутентификации, четыре варианта ошибок, каждый клиент заново реализует объединение); один сервис вызывает остальные (какой? теперь он зависит от всех); или компоновщик.
- Разным клиентам нужны разные формы: веб-дашборду — 40 полей и пагинация, мобильному приложению — 8 полей и маленький payload, партнёрскому API — стабильный версионированный контракт. Один «общий» API не обслуживает хорошо никого из них.

### API gateway, сервис композиции и BFF
| | API gateway | Сервис композиции | BFF |
|---|---|---|---|
| Делает | Маршрутизация, аутентификация, rate limit, TLS, возможно, простая агрегация | Fan-out + объединение для сценария | Fan-out + объединение + форма под конкретного клиента |
| Владелец | Платформенная команда | Backend-команда | Команда клиента (web, mobile) |
| Логика | Нет (конфигурация) | Оркестрация чтения | Оркестрация чтения + представление |
| Количество | Один (или на зону) | На домен/сценарий | Один на тип клиента |
- Gateway остаётся простым ([[api-gateway-and-service-discovery]]); GraphQL-gateway (Apollo Federation, Spring for GraphQL) — это слой композиции, где запрос на объединение пишет клиент.

### Паттерны композиции
- **Параллельный fan-out**: независимые сервисы вызываются одновременно (`CompletableFuture.allOf`, `WebClient` + `Mono.zip` или [[virtual-threads]] со структурированной конкурентностью), затем результаты объединяются. Задержка = максимум, а не сумма ([[completable-future]], [[spring-reactive-webclient-sse]]).
- **Частичные результаты**: у каждой части свой таймаут и fallback; страница отображается с «баланс недоступен», а не с 502 ([[circuit-breaker]]).
- **Кэширование** медленных или стабильных частей (каталог тарифов) в компоновщике ([[caching]]).
- **Пакетные endpoint-ы**: `GET /subscribers?ids=1,2,3` в доменных сервисах, чтобы композиция списка была одним вызовом на сервис, а не N ([[jpa-n-plus-one]] на сетевом уровне).
- **Предвычисленные представления**: когда fan-out слишком медленный или слишком частый, вместо него строится модель чтения через события ([[cqrs]]); BFF тогда читает одну таблицу. Композиция — синхронный ответ, CQRS — асинхронный.

### Эмпирические правила для BFF
- Один BFF на *тип* клиента с реальными различиями (mobile, web, партнёр), а не на экран и не по моде команды. Два клиента с одинаковыми нуждами делят один.
- BFF содержит только логику представления (выбор полей, форматирование, подсказки i18n, форма пагинации) и оркестрацию чтения. Бизнес-правила в BFF уже через месяц дублируются между BFF; их нужно спускать в доменные сервисы.
- Аутентификация: BFF завершает клиентскую сессию (cookie или токен) и вызывает сервисы с сервисными учётными данными или переданным JWT ([[authentication-vs-authorization]], [[mtls-and-service-to-service-auth]]). Здесь же живут ограничения размера ответа и маскирование полей (никаких персональных данных в мобильное приложение).
- Записи: BFF может переслать одну команду в один сервис; оркестрировать записи в несколько сервисов он не должен. Это задача саги/workflow с хранением состояния и компенсациями.

### Как подать на собеседовании
- «Как мобильное приложение получает страницу абонента из шести микросервисов?» → BFF с параллельным fan-out, таймаутами и fallback-ами на каждую часть, пакетными endpoint-ами, кэшированием статичных частей; если страница горячая — проекция.
- «Где живёт логика агрегации и кто ей владеет?» → команда клиента, которой она нужна; доменные сервисы предоставляют чистые API, не зависящие от клиента.

## Пример
Endpoint мобильного BFF, параллельно компонующий три сервиса с fallback-ами на каждую часть, на виртуальных потоках.

```java
@RestController
@RequestMapping("/mobile/v1")
@RequiredArgsConstructor
public class SubscriberPageController {
    private final ProvisioningClient provisioning;   // обёртки над RestClient с таймаутами + breaker-ами
    private final BillingClient billing;
    private final CatalogueClient catalogue;
    private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();

    public record SubscriberPage(String msisdn, String simStatus, String balance, String tariffName, List<String> warnings) {}

    @GetMapping("/subscribers/{msisdn}")
    public SubscriberPage page(@PathVariable String msisdn) throws InterruptedException, ExecutionException {
        var sim     = executor.submit(() -> provisioning.simStatus(msisdn));
        var balance = executor.submit(() -> billing.balance(msisdn));
        var tariff  = executor.submit(() -> catalogue.tariffFor(msisdn));   // кэшируется на 10 мин в клиенте

        List<String> warnings = new ArrayList<>();
        String simStatus = sim.get(500, MILLISECONDS).status();                  // обязательная часть: ошибка пробрасывается
        String balanceText = part(balance, 300, b -> b.formatted(), warnings, "balance");
        String tariffName  = part(tariff, 300, Tariff::name, warnings, "tariff");
        return new SubscriberPage(msisdn, simStatus, balanceText, tariffName, warnings);
    }

    private <T> String part(Future<T> f, long ms, Function<T, String> map, List<String> warnings, String name) {
        try {
            return map.apply(f.get(ms, MILLISECONDS));
        } catch (Exception e) {                       // необязательная часть: деградировать, записать, продолжить
            f.cancel(true);
            warnings.add(name + " unavailable");
            return null;
        }
    }
}
```

## Подводные камни
- Последовательный fan-out: `sim = ...; balance = ...; tariff = ...;` превращает три вызова по 100 мс в 300 мс; независимые вызовы всегда нужно распараллеливать.
- BFF, у которого появилась база данных: теперь это сервис с проблемами владения данными; BFF должен быть без состояния, если не считать кэшей.
- Композиция поверх пагинированных списков: корректно объединить «страницу 2» из двух сервисов с разным порядком сортировки невозможно; вместо этого нужно предвычислить модель чтения.
- Наслоение таймаутов: бюджет BFF 500 мс плюс 5 с у gateway плюс retry внутри клиентов; нужно согласовать один сквозной бюджет ([[service-communication-sync-vs-async]]).
- Связанные заметки: [[api-gateway-and-service-discovery]], [[cqrs]], [[api-design-for-system-design]], [[design-news-feed]].
