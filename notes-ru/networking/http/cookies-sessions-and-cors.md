---
title: Cookies, сессии, политика одного источника и CORS
tags: [networking, http, cookies, cors, csrf]
learned: 2026-10-27
---
## Кратко
HTTP не хранит состояние (stateless); cookies — механизм на стороне браузера, который прикрепляет небольшой кусочек состояния (обычно id сессии или токен) к каждому запросу к домену. Политика одного источника (same-origin policy) не даёт JavaScript с одного источника читать ответы с другого, а CORS — управляемое сервером исключение из неё, которое соблюдает только браузер. `SameSite`, `HttpOnly` и `Secure` — три флага cookie, от которых зависит, переживёт ли сессия XSS и CSRF. Настройка CORS в Spring и взгляд со стороны REST — в [[rest-and-http]]; эта заметка — о протоколе и модели безопасности за ним.

## Основное
### Cookies
- Сервер устанавливает: `Set-Cookie: SESSION=abc; Path=/; Domain=beeline.kg; Max-Age=3600; Secure; HttpOnly; SameSite=Lax`. Браузер отправляет обратно `Cookie: SESSION=abc` в каждом подходящем запросе автоматически, включая запросы, инициированные другими сайтами. Эта автоматическая отправка — одновременно и возможность, и проблема CSRF.
- Область действия: `Domain` (хост и поддомены, если задан; только точный хост, если опущен), `Path`. Cookies игнорируют порт и схему, если не задан `Secure`, поэтому при локальной разработке на разных портах localhost cookies общие.
- `HttpOnly`: не читается через `document.cookie`, поэтому XSS не может его украсть. `Secure`: отправляется только по HTTPS. `Max-Age`/`Expires`: постоянный cookie; без них cookie умирает вместе с сессией браузера.
- `SameSite`: `Strict` (никогда не отправляется в межсайтовых запросах, ломает ссылки из писем), `Lax` (отправляется при навигации верхнего уровня через GET, но не в межсайтовых POST/fetch; поведение браузеров по умолчанию с 2020 года), `None` (отправляется везде, требует `Secure`; нужен для встраивания на сторонние сайты и некоторых OAuth-редиректов).
- Лимит размера ~4 КБ на cookie, и все cookies едут с каждым запросом, включая статические ресурсы: не клади в cookies JWT на 3 КБ плюс данные профиля ([[http-1-1-vs-2-vs-3]] — HPACK смягчает это, но не решает).

### Сессии: состояние на сервере против токенов
| | Серверная сессия (cookie = id) | Токен (JWT в cookie или заголовке) |
|---|---|---|
| Где живёт состояние | память сервера / Redis (Spring Session) | внутри токена, подписанное |
| Отзыв | удалить сессию, мгновенно | сложно: короткий срок жизни + refresh token или denylist |
| Горизонтальное масштабирование | нужно общее хранилище или sticky sessions ([[load-balancers-l4-vs-l7]]) | без состояния, проверить может любой экземпляр |
| Полезная нагрузка | произвольная | только небольшие claims |
| Уязвимость к CSRF | да, если на cookie | да, если на cookie; нет, если JS отправляет его в заголовке `Authorization` |
| Уязвимость к XSS | `HttpOnly` защищает id | `HttpOnly`-cookie защищает; `localStorage` — нет |

- Подробности о структуре JWT и настройке Spring Security: [[jwt-authentication]], [[spring-security-basics]]; общие понятия — в [[authentication-vs-authorization]].

### Политика одного источника
- Источник (origin) = схема + хост + порт. `https://app.beeline.kg` и `https://api.beeline.kg` — разные источники; так же как `:443` и `:8443`.
- JavaScript может *отправлять* запросы куда угодно (формы, картинки, скрипты всегда это умели); он не может *читать* ответ с другого источника, если CORS этого не разрешает. Запрос часто доходит до твоего сервера; браузер лишь скрывает ответ. Побочные эффекты POST всё равно происходят, поэтому CORS — не защита от CSRF.

### Протокол CORS
- **Простые запросы** (GET/HEAD/POST только с безопасными заголовками и `Content-Type` типов form/text): отправляются напрямую; браузер проверяет `Access-Control-Allow-Origin` в ответе.
- **Preflight** (предварительный запрос): всё остальное (JSON в `Content-Type`, заголовок `Authorization`, PUT/DELETE) сначала вызывает `OPTIONS` с `Origin`, `Access-Control-Request-Method`, `Access-Control-Request-Headers`. Сервер должен ответить `204` с `Access-Control-Allow-Origin`, `-Methods`, `-Headers` и опционально `-Max-Age`, чтобы закешировать preflight (в Chromium браузер ограничивает это 2 ч).
- `Access-Control-Allow-Credentials: true` нужен, чтобы отправлялись cookies или `Authorization`, и тогда `Allow-Origin` должен быть точным источником, а не `*`.
- `Access-Control-Expose-Headers` перечисляет нестандартные заголовки ответа, которые может читать JS (`Location`, `X-Request-ID`); иначе видны только несколько простых.
- Ответам, которые зависят от источника, нужен `Vary: Origin`, чтобы кеши хранили их раздельно ([[http-caching]]).
- CORS соблюдают только браузеры: curl, мобильные приложения и другие серверы его игнорируют. Он защищает пользователей от вредоносных сайтов, а не твой API от клиентов.

### CSRF
- Атака: страница на `evil.example` отправляет форму на `https://api.beeline.kg/transfer`; браузер прикрепляет cookie сессии; сервер не может отличить это от легитимного запроса.
- Защита, слоями: `SameSite=Lax` (или `Strict`) на cookie сессии; CSRF-токен, который атакующий не может прочитать (synchronizer token в поле формы или заголовке `X-XSRF-TOKEN`, `CookieCsrfTokenRepository` в Spring Security); проверка `Origin`/`Referer`. API только с токеном в заголовке (`Authorization: Bearer`) неуязвимы, потому что браузер никогда не добавляет этот заголовок сам, — поэтому Spring Security позволяет отключить CSRF для stateless API.

## Пример
Самая распространённая боевая настройка CORS: SPA на одном источнике, API на другом, cookie сессии с разумными флагами, закешированный preflight. Spring Boot 3 с подключённым Spring Security (поэтому фильтр CORS выполняется до security).
```java
@Configuration
public class WebSecurityConfig {

    @Bean
    SecurityFilterChain api(HttpSecurity http) throws Exception {
        return http
            .cors(c -> c.configurationSource(corsSource()))
            .csrf(csrf -> csrf.csrfTokenRepository(CookieCsrfTokenRepository.withHttpOnlyFalse()))
            .authorizeHttpRequests(a -> a.anyRequest().authenticated())
            .build();
    }

    private CorsConfigurationSource corsSource() {
        var cfg = new CorsConfiguration();
        cfg.setAllowedOrigins(List.of("https://my.beeline.kg"));          // точный источник, никогда не "*" с credentials
        cfg.setAllowedMethods(List.of("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"));
        cfg.setAllowedHeaders(List.of("Content-Type", "X-XSRF-TOKEN", "X-Request-ID"));
        cfg.setExposedHeaders(List.of("Location", "X-Request-ID"));
        cfg.setAllowCredentials(true);                                      // отправлять cookie сессии
        cfg.setMaxAge(Duration.ofHours(1));                                 // кешировать preflight
        var src = new UrlBasedCorsConfigurationSource();
        src.registerCorsConfiguration("/api/**", cfg);
        return src;
    }
}
```
```yaml
server:
  servlet:
    session:
      cookie:
        name: SESSION
        http-only: true
        secure: true
        same-site: lax
        max-age: 1h
```
Preflight в том виде, в каком его отправляет браузер:
```bash
$ curl -si -X OPTIONS https://api.beeline.kg/api/v1/esims -H 'Origin: https://my.beeline.kg' \
   -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: content-type,x-xsrf-token'
HTTP/2 204
access-control-allow-origin: https://my.beeline.kg
access-control-allow-methods: GET,POST,PUT,PATCH,DELETE,OPTIONS
access-control-allow-headers: content-type, x-xsrf-token, x-request-id
access-control-allow-credentials: true
access-control-max-age: 3600
vary: Origin
```

## Подводные камни
- `Access-Control-Allow-Origin: *` вместе с credentials: браузеры это отвергают, а отражение любого `Origin` обратно с credentials равносильно отсутствию защиты.
- Исправление CORS одновременно в nginx и в Spring даёт дублирующиеся заголовки, которые браузеры считают некорректными.
- Хранение JWT в `localStorage`, чтобы «избежать CSRF»: обмен CSRF на кражу через XSS; `HttpOnly`-cookie плюс `SameSite` и CSRF-токен — более безопасный вариант по умолчанию для браузерных клиентов.
- `SameSite=Lax` ломает callback-и OAuth/OIDC, которые отправляют POST обратно на твой сайт; таким сценариям нужен `SameSite=None; Secure` на cookie состояния (state).
- Глобальное отключение CSRF, потому что «мы используем токены», в то время как то же приложение обслуживает и админку с аутентификацией через cookie.
- Связанные заметки: [[rest-and-http]] — CORS с точки зрения проектирования API, [[api-security-checklist]] — остальной список мер защиты для браузерных клиентов.
