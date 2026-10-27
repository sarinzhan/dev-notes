---
title: CDN — кэширование на edge, статический и динамический контент, инвалидация и подписанные URL
tags: [system-design, cdn, caching, networking]
learned: 2026-10-27
---
## Кратко
CDN — это глобально распределённый набор обратных прокси (точек присутствия, points of presence, PoP), которые отдают ответы из кэша рядом с пользователем: пользователь из Бишкека получает изображение из Алматы за 10 мс, а не из Франкфурта за 90 мс, а origin видит один запрос на объект на каждый PoP, а не на каждого пользователя. Это первый строительный блок для всего, где есть статические ресурсы, загрузки или публичные API, которые в основном читаются. Решения такие: что кэшируется и на сколько (`Cache-Control`), как инвалидировать (версионированные URL лучше очисток (purge)), как защищать приватные объекты (подписанные URL) и что всё равно должно доходить до origin (динамические персонализированные ответы, которые CDN лишь ускоряет, терминируя TLS рядом с клиентом).

## Основное

### Как проходит запрос
1. DNS разрешает `cdn.example.kg` в ближайший PoP (geo-DNS или anycast, [[dns-resolution]], [[load-balancing]]).
2. PoP терминирует TLS и ищет ключ кэша: по умолчанию схема + хост + путь + query; заголовки — только если это настроено (`Vary`).
3. **Попадание (hit)**: ответ отдаётся с edge, задержка = RTT пользователь ↔ PoP. **Промах (miss)**: PoP (или промежуточный PoP-«щит» (shield), который схлопывает промахи от многих edge) забирает объект с origin по «прогретому» keep-alive соединению, сохраняет его согласно заголовкам ответа и отдаёт.
4. Origin — это твой балансировщик нагрузки или бакет блоб-хранилища ([[file-and-blob-storage]]). Бакет в стиле S3 за CDN — стандартный способ отдавать пользовательские загрузки.

### Что кэшировать
| Контент | Кэшируется на edge | Типичный TTL | Примечание |
|---|---|---|---|
| Версионированные статические ресурсы (`app.3f9a1c.js`, изображения) | да | 1 год, `immutable` | хэш содержимого в имени, никогда не меняется |
| Пользовательские загрузки, медиа | да | дни–месяцы | приватные — через подписанные URL |
| Публичные GET API (каталог, тарифы, новости) | да | 10 с–10 мин | `s-maxage` для edge, `max-age` для браузеров |
| Персонализированные ответы (`/me`, балансы) | нет (`private`, `no-store`) | — | CDN всё равно терминирует TLS рядом с пользователем и держит прогретое соединение с origin |
| HTML-оболочка приложения (app shell) | ненадолго | 0–60 с, перепроверка | чтобы deploy быстро становился виден |
| `POST`/`PUT`, WebSockets | нет | — | проксируются насквозь, выигрывают от TLS на edge и фильтрации DDoS |

Заголовки, которые этим управляют (полная семантика — в [[http-caching]]): `Cache-Control: public, max-age=60, s-maxage=600, stale-while-revalidate=30`, `ETag` для условной перепроверки (`304` дешёвый), `Vary: Accept-Encoding` (список `Vary` держи крошечным, каждое значение умножает число записей в кэше).

### Инвалидация
- **Версионированные URL** (хэш содержимого или `?v=` + id сборки) — единственная инвалидация, которая работает всегда: новый deploy ссылается на новые имена, старые объекты истекают сами. Индексный HTML получает короткий TTL, чтобы подхватывать новые имена.
- **Purge API**: по URL, по тегу/surrogate key (`Surrogate-Key: tariff-42`) или всё сразу. Распространение по PoP занимает от секунд до минуты, частота ограничена; оставь его для аварий и объектов, которые нельзя переименовать (аватар пользователя по стабильному URL).
- **Короткий TTL + `stale-while-revalidate`**: edge сразу отдаёт устаревшую копию и обновляет её в фоне, так что пользователи никогда не ждут origin, а устаревание ограничено TTL. `stale-if-error` продолжает отдавать контент во время сбоя origin — бесплатный прирост доступности для контента только для чтения.

### Подписанные URL и приватный контент
- Приватный объект (PDF счёта, QR-код eSIM, запись) не должен лежать по угадываемому публичному URL. Origin или приложение генерирует **подписанный URL (signed URL)**: путь + срок действия + опционально IP клиента, подписанные ключом, известным CDN или бакету (HMAC или RSA). CDN проверяет подпись и срок перед отдачей; приложение никогда не передаёт байты само.
- Срок — минуты для разовых загрузок, часы для воспроизведения медиа; подписанные **cookies** покрывают целый префикс (все сегменты видео). Отзыв — это «дождаться истечения» или ротировать ключ; проектируй под короткое время жизни ([[symmetric-and-asymmetric-crypto]]).
- Загрузки идут тем же путём в обратную сторону: presigned `PUT` в бакет, чтобы загрузка шла в обход приложения ([[file-and-blob-storage]]).

### Что CDN делает помимо кэширования
- **Терминирование TLS на edge** и HTTP/3 до пользователя, HTTP/2 с долгоживущими соединениями до origin: handshake в 3 RTT по 80 мс превращается в 3 × 10 мс ([[tls-handshake-and-certificates]], [[http-1-1-vs-2-vs-3]]).
- **Поглощение DDoS и WAF**: объёмные атаки упираются в мощности anycast, а не в твой LB; [[rate-limiting]] по IP на edge; правила против ботов.
- **Origin shielding и схлопывание запросов**: один промах на объект на щит, а не на PoP; защищает origin от stampede, когда истекает популярный объект ([[caching]]).
- **Вычисления на edge (edge compute)**: небольшие функции на PoP (A/B-маршрутизация, переписывание заголовков, проверка подписанного токена) без обращения к origin.
- **Сжатие и преобразование изображений**: Brotli на edge, генерация вариантов `?w=400` по запросу.

### Интуиция по объёмам
Если 5 M абонентов ежедневно открывают приложение и каждая загрузка тянет 2 MB ресурсов, это 10 TB/день исходящего трафика; при hit ratio 99 % на edge origin остаётся 100 GB/день и ~10 Мбит/с, что один бакет обслужит не заметив. Обычно CDN меняет именно строку расходов на исходящий трафик (egress), а не на CPU ([[back-of-envelope-estimation]]).

## Пример
Конфигурация Spring MVC для публичного эндпоинта тарифов и версионированных ресурсов, плюс генератор подписанных URL для приватных PDF-счетов. CDN читает заголовки; ничего специфичного для CDN в коде нет.

```java
@RestController
@RequestMapping("/v1/tariffs")
class TariffController {
    private final TariffService tariffs;
    TariffController(TariffService tariffs) { this.tariffs = tariffs; }

    @GetMapping
    ResponseEntity<List<TariffDto>> list(WebRequest request) {
        var snapshot = tariffs.currentSnapshot();                       // версия + список, дёшево вычислить
        String etag = "\"" + snapshot.version() + "\"";
        if (request.checkNotModified(etag)) return null;               // Spring вернёт 304 с заголовками ниже
        return ResponseEntity.ok()
                .eTag(etag)
                .cacheControl(CacheControl.maxAge(Duration.ofSeconds(60))      // браузеры
                        .cachePublic()
                        .sMaxAge(Duration.ofMinutes(10))                        // edge CDN
                        .staleWhileRevalidate(Duration.ofSeconds(30))
                        .staleIfError(Duration.ofHours(1)))
                .body(snapshot.items());
    }
}

@Configuration
class StaticAssetsConfig implements WebMvcConfigurer {
    @Override public void addResourceHandlers(ResourceHandlerRegistry registry) {
        registry.addResourceHandler("/assets/**")
                .addResourceLocations("classpath:/static/assets/")
                .setCacheControl(CacheControl.maxAge(Duration.ofDays(365)).cachePublic().immutable())
                .resourceChain(true)
                .addResolver(new VersionResourceResolver().addContentVersionStrategy("/**"));  // app.js -> app-3f9a1c.js
    }
}

@Component
class SignedUrlService {                                               // схема на HMAC, не привязанная к конкретному CDN
    private final byte[] key; private final String base;
    SignedUrlService(@Value("${cdn.signing-key}") String key, @Value("${cdn.base}") String base) {
        this.key = key.getBytes(StandardCharsets.UTF_8); this.base = base;
    }
    String invoiceUrl(long subscriberId, String invoiceId, Duration ttl) {
        long expires = Instant.now().plus(ttl).getEpochSecond();
        String path = "/private/invoices/%d/%s.pdf".formatted(subscriberId, invoiceId);
        String sig = hmacBase64Url(path + ":" + expires);               // функция на edge пересчитывает и сравнивает
        return base + path + "?exp=" + expires + "&sig=" + sig;
    }
    private String hmacBase64Url(String data) {
        try {
            var mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return Base64.getUrlEncoder().withoutPadding().encodeToString(mac.doFinal(data.getBytes(StandardCharsets.UTF_8)));
        } catch (GeneralSecurityException e) { throw new IllegalStateException(e); }
    }
}
```

## Подводные камни
- Персонализированный ответ закэширован как публичный: `/me` одного пользователя отдаётся всем. Любой эндпоинт, читающий `Authorization` или сессионную cookie, должен отправлять `Cache-Control: private, no-store`.
- `Vary: Cookie` или `Vary: User-Agent`: фактически отключает кэширование (одна запись на каждое значение cookie), хотя в конфигурации выглядит как кэшируемое.
- Опора на purge при deploy: purge распространяется минуту, и его частота ограничена; вместо этого версионируй ресурсы.
- Ключи кэша с query string: варианты `?utm_source=` умножают промахи; настрой CDN игнорировать маркетинговые параметры.
- Подписанный URL с долгим сроком, который потом утекает (пересланная ссылка, логи): объект становится публичным на неделю. Для загрузок — минуты; включай id пользователя в подписанный путь, чтобы одну ссылку нельзя было переиспользовать для другого объекта.
- Связанные заметки: [[http-caching]] — семантика заголовков; [[file-and-blob-storage]] — origin, откуда на самом деле идёт большая часть трафика CDN.
