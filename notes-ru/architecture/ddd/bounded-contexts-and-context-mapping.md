---
title: Ограниченные контексты и карта контекстов
tags: [ddd, architecture, microservices, integration]
learned: 2026-10-27
---
## Кратко
**Ограниченный контекст** (bounded context) — это граница, внутри которой действуют одна модель и один единый язык (ubiquitous language): «абонент» в provisioning (ICCID, EID, состояние профиля) — это другая модель, чем «абонент» в биллинге (баланс, тариф, долг). **Карта контекстов** (context map) описывает, как контексты связаны и как они переводят модели друг друга: кто владеет контрактом (upstream/downstream), копирует ли downstream модель (conformist) или защищается от неё (антикоррупционный слой), где есть общее ядро (shared kernel) или опубликованный язык (published language). Границы контекстов — лучшее первое приближение к границам сервисов ([[monolith-vs-microservices]]).

## Основное

### Как найти границы
- Прислушиваться к одному и тому же слову с разным смыслом («активация» SIM на HLR и активация промо) или к разным словам для одного и того же. Каждый смысл принадлежит своему контексту.
- Один контекст = им может владеть одна команда, одна модель помещается в одной голове, правдоподобна одна единица deploy. В коде контекст — это модуль/пакет со своими сущностями, репозиториями и DTO ([[modular-monolith]]) или сервис со своей базой данных ([[data-modeling-for-services]]).
- Поддомен и ограниченный контекст: **поддомен** (subdomain) — часть бизнеса (основной: eSIM provisioning; вспомогательный: уведомления; типовой: auth, биллинг). Ограниченный контекст — граница решения; в идеале один на поддомен, но в legacy-системах часто несколько контекстов покрывают один поддомен или один контекст охватывает два.
- Основные (core) поддомены получают усилия на моделирование и собственный код; типовые (generic) покупают или копируют.

### Отношения на карте контекстов
| Паттерн | Кто адаптируется | Когда применять |
|---|---|---|
| **Партнёрство** (partnership) | Обе команды согласуют релизы | Два основных контекста развиваются вместе |
| **Общее ядро** (shared kernel) | Оба используют небольшую общую библиотеку/схему | Крошечная стабильная общая модель (id, деньги); опасно, если разрастается |
| **Заказчик/поставщик** (customer/supplier) | Upstream планирует с учётом нужд downstream | У downstream есть влияние (внутренние команды) |
| **Конформист** (conformist) | Downstream принимает модель upstream как есть | Upstream ради тебя меняться не будет, модель приемлема (API крупного вендора) |
| **Антикоррупционный слой** (anti-corruption layer, ACL) | Downstream переводит на границе | Модель upstream запутанная или чуждая (SM-DP+ ES2+, legacy-биллинг, банковский хост) |
| **Открытый сервис** (open host service) | Upstream предоставляет стабильный протокол | Много downstream; REST/gRPC API с версионированием |
| **Опубликованный язык** (published language) | Оба говорят на документированном общем формате | События в Kafka со схемой Avro/Protobuf ([[message-schema-evolution]]) |
| **Разные пути** (separate ways) | Никто не интегрируется | Интеграция стоит дороже дублирования |
- Upstream/downstream — это про то, кто на кого влияет, а не про направление данных: HLR — upstream для provisioning, даже когда provisioning отправляет ему команды, потому что provisioning вынужден подстраиваться под протокол HLR.

### Антикоррупционный слой на практике
- ACL — это адаптер (в смысле [[hexagonal-architecture]]), который преобразует внешнюю модель в доменную: `SmdpClient` возвращает доменные объекты `DownloadOrder`, а не JSON ES2+; `BillingAcl` отображает коды `subscriber_status` биллинга в состояние `Subscription`.
- Его стоит держать однонаправленным и простым: перевод, значения по умолчанию, отображение ошибок. Бизнес-правилам в нём не место.
- Тесты: контрактные тесты против внешнего формата ([[contract-testing-and-wiremock]]), модульные тесты для маппинга.

### Взаимодействие между контекстами
- Синхронное (REST/gRPC), когда вызывающему нужен ответ сейчас и он может терпеть зависимость от доступности upstream ([[service-communication-sync-vs-async]]).
- Асинхронные интеграционные события, когда downstream реагирует на факты ([[domain-events-and-integration-events]], [[event-driven-architecture]]). Опубликованный язык + outbox — обычная пара ([[outbox-pattern]]).
- Никогда не делить таблицу базы данных между контекстами: это связанность по общим данным (common coupling), и контрактом становится схема ([[coupling-cohesion-and-general-principles]]).

## Пример
ACL между контекстом provisioning и сервером SM-DP+: внешнее представление ES2+ остаётся внутри адаптера.

```java
// Доменный порт (язык контекста provisioning)
public interface ProfileServer {
    DownloadOrder order(Iccid iccid, Eid eid);
}

// Антикоррупционный слой: адаптер, переводящий в модель вендора ES2+ и обратно
@Component
@RequiredArgsConstructor
class Es2PlusProfileServer implements ProfileServer {
    private final RestClient rest;

    @Override
    public DownloadOrder order(Iccid iccid, Eid eid) {
        var req = Map.of("iccid", iccid.value(), "eid", eid.value(), "releaseFlag", true);
        Es2Response resp = rest.post().uri("/gsma/rsp2/es2plus/downloadOrder")
                .body(req).retrieve().body(Es2Response.class);
        return switch (resp.header().functionExecutionStatus().status()) {
            case "Executed-Success" -> new DownloadOrder(iccid, new MatchingId(resp.matchingId()), DownloadState.RELEASED);
            case "Failed" -> throw mapError(resp.header().functionExecutionStatus().statusCodeData());
            default -> throw new ProfileServerException("unknown status");
        };
    }

    private ProvisioningException mapError(StatusCodeData d) {
        // коды вендора превращаются в доменные исключения; домен никогда не видит "8.2.1"
        return switch (d.subjectCode() + "/" + d.reasonCode()) {
            case "8.2.1/3.9" -> new ProfileNotFoundException();
            case "8.2.5/3.3" -> new ProfileAlreadyReleasedException();
            default -> new ProfileServerException(d.message());
        };
    }
}
```

## Подводные камни
- Одна JPA-сущность `Subscriber`, которую используют provisioning, биллинг и поддержка: поля всех контекстов попадают в одну таблицу, каждое изменение затрагивает каждую команду. Модель стоит разделить, даже если сервисы ещё не разделены.
- Общее ядро, которое разрастается в модуль `common` на 200 классов, незаметно превращается в распределённый монолит.
- Конформист по случайности: DTO вендора скопированы в свой домен, потому что так быстрее; следующая версия API вендора переписывает твой домен.
- Карту контекстов нарисовали один раз и больше не обновляют; это живой документ об отношениях команд в той же мере, что и о коде.
- Связанные заметки: [[ddd-basics]], [[aggregates-and-invariants]], [[strangler-fig-migration]] (выделение контекстов из монолита), [[esim-provisioning-platform]].
