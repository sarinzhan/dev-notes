---
title: Аутентификация и авторизация, токены и потоки OAuth2/OIDC
tags: [networking, security, oauth2, oidc, tokens]
learned: 2026-10-27
---
## Кратко
Аутентификация (authentication) отвечает на вопрос «кто вызывает» (401 при неудаче); авторизация (authorization) — «можно ли ему это делать» (403). Сессии хранят ответ на сервере за непрозрачной cookie; токены несут его в самом запросе — либо как непрозрачную ссылку, которую сервер ищет у себя, либо как самодостаточный подписанный JWT, который может проверить любой сервис. OAuth2 — протокол делегирования доступа к API без передачи паролей; OIDC добавляет слой идентичности (ID token), чтобы тот же поток ещё и выполнял вход пользователя. В Spring-бэкенде ты чаще всего оказываешься в роли OAuth2 **resource server**, проверяющего JWT от Keycloak или облачного IdP; механика JWT и фильтр Spring — в [[jwt-authentication]], а эта заметка — карта вокруг них.

## Основное
### Два вопроса
| | Аутентификация (AuthN) | Авторизация (AuthZ) |
|---|---|---|
| Вопрос | кто ты | что тебе можно |
| Доказательство | пароль, сертификат ([[mtls-and-service-to-service-auth]]), токен, OTP | роли, scopes, владение, политика |
| Ошибка HTTP | `401 Unauthorized` + `WWW-Authenticate` | `403 Forbidden` (или `404`, чтобы скрыть существование) |
| Компонент Spring Security | `AuthenticationManager`, фильтры, `SecurityContext` | `authorizeHttpRequests`, `@PreAuthorize`, method security ([[spring-security-basics]]) |
| Типичный баг | принять истёкший токен | проверить роль, но не владение (IDOR) |

- Модели авторизации: **RBAC** (роли → права; просто, грубо), **ABAC** (атрибуты субъекта, ресурса, контекста; `msisdn == token.sub`), **ReBAC** (отношения, в стиле Zanzibar, для графов совместного доступа). Большинство сервисов используют RBAC для грубой маршрутизации плюс проверки владения в коде.

### Сессии, непрозрачные токены и JWT
| | Серверная сессия | Непрозрачный токен (opaque) | JWT |
|---|---|---|---|
| Что держит клиент | случайный id в cookie ([[cookies-sessions-and-cors]]) | случайная строка, обычно `Bearer` | подписанные claims, `Bearer` |
| Проверка | поиск в хранилище сессий | вызов интроспекции у издателя (`/introspect`) или общее хранилище | локальная проверка подписи публичным ключом издателя (JWKS) |
| Отзыв | мгновенно | мгновенно | только по истечении; короткий TTL + ротация refresh или denylist, который возвращает поиск |
| Между сервисами | нужно общее хранилище | каждый сервис вызывает издателя | каждый сервис проверяет сам |
| Размер в сети | крошечный | крошечный | 500 Б – 2 КБ в каждом запросе |
| Когда выбирать | браузерное приложение, один бэкенд, нужен мгновенный выход | мало сервисов, строгий отзыв (банкинг) | много сервисов, проверка на шлюзе, короткоживущие access-токены |

### Роли и потоки OAuth2
- **Роли**: владелец ресурса (resource owner, пользователь), клиент (client, приложение, которому нужен доступ), сервер авторизации (authorization server: Keycloak, Auth0, Entra ID), сервер ресурсов (resource server, твой API). Access-токены предназначены для API; ID token (OIDC) — для клиента, чтобы знать, кто вошёл; refresh-токены получают новые access-токены.
- **Authorization Code + PKCE**: браузерные/мобильные приложения. Пользователя перенаправляют на IdP, он входит, возвращается с одноразовым `code`, клиент обменивает его на токены; PKCE (`code_verifier`/`code_challenge`) защищает code от перехвата, поэтому публичным клиентам не нужен секрет. Сегодня единственный поток для входа пользователя; потоки Implicit и Resource Owner Password объявлены устаревшими в OAuth 2.1.
- **Client Credentials**: сервис-сервис, без пользователя. Client id + секрет (или JWT с закрытым ключом, или mTLS) в обмен на access-токен со scopes. Spring: `spring-security-oauth2-client` с `ClientRegistration` и `OAuth2AuthorizedClientManager` добавляет токен в `RestClient`/`WebClient`.
- **Device Code**: телевизоры, CLI: показывается код, пользователь подтверждает на телефоне.
- **Token exchange (RFC 8693)**: сервис обменивает входящий токен пользователя на токен для нижестоящего сервиса с более узким scope; правильное решение вместо «пробрасывать токен пользователя везде».
- **OIDC** поверх: scope `openid`, ID token (JWT с `sub`, `iss`, `aud`, `nonce`), документ обнаружения `/.well-known/openid-configuration` (издатель, URL JWKS, эндпоинты), `/userinfo`.

### Scopes и роли
- **Scopes** описывают, что было выдано клиенту (`esim:read`, `esim:activate`); **роли/права** — что может делать пользователь. API проверяет оба: токен должен нести scope, а у пользователя должно быть право. Keycloak кладёт роли realm в `realm_access.roles`; Spring по умолчанию отображает scopes в authorities `SCOPE_x`, а для ролей нужен конвертер ([[jwt-authentication]]).

### Чек-лист проверки для resource server
1. Подпись ключом, указанным в `kid`, из JWKS издателя (кешировать, обновлять при неизвестном `kid`).
2. `iss` равен ожидаемому издателю; `aud` содержит этот API; `exp`/`nbf` с небольшим допуском на расхождение часов; `alg` из белого списка (никогда `none`, никогда не доверять заголовку выбор типа ключа).
3. Затем авторизация: scopes, роли, владение ресурсом из пути.
4. Логировать `sub` и `jti`, никогда не сам токен ([[api-security-checklist]]).

## Пример
Resource server на Spring Boot 3, проверяющий токены Keycloak, отображающий роли realm и выполняющий проверку владения, которую один лишь RBAC пропускает. Плюс клиентская сторона для вызова сервис-сервис через client credentials.
```yaml
spring:
  security:
    oauth2:
      resourceserver:
        jwt:
          issuer-uri: https://sso.beeline.kg/realms/esim     # discovery -> JWKS, проверка iss
          audiences: esim-api                                 # проверка aud (Spring Boot 3.1+)
      client:
        registration:
          billing:
            provider: keycloak
            client-id: esim-api
            client-secret: ${BILLING_CLIENT_SECRET}
            authorization-grant-type: client_credentials
            scope: billing:charge
        provider:
          keycloak:
            issuer-uri: https://sso.beeline.kg/realms/esim
```
```java
@Configuration
@EnableMethodSecurity
public class ResourceServerConfig {

    @Bean
    SecurityFilterChain api(HttpSecurity http) throws Exception {
        var roles = new JwtGrantedAuthoritiesConverter();          // scopes -> SCOPE_*
        var converter = new JwtAuthenticationConverter();
        converter.setJwtGrantedAuthoritiesConverter(jwt -> {
            var out = new ArrayList<GrantedAuthority>(roles.convert(jwt));
            Map<String, Object> realm = jwt.getClaimAsMap("realm_access");
            if (realm != null) ((List<?>) realm.get("roles"))
                    .forEach(r -> out.add(new SimpleGrantedAuthority("ROLE_" + r)));
            return out;
        });
        return http
            .authorizeHttpRequests(a -> a
                .requestMatchers("/actuator/health").permitAll()
                .requestMatchers(HttpMethod.POST, "/api/v1/esims/*/activations").hasAuthority("SCOPE_esim:activate")
                .anyRequest().authenticated())
            .oauth2ResourceServer(o -> o.jwt(j -> j.jwtAuthenticationConverter(converter)))
            .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .build();
    }
}

@RestController
class EsimController {
    @GetMapping("/api/v1/subscribers/{msisdn}/esims")
    @PreAuthorize("hasRole('SUPPORT') or #msisdn == authentication.token.claims['msisdn']")   // владение, а не только роль
    List<EsimDto> list(@PathVariable String msisdn) { ... }
}
```
Как выглядит получение токена для вызова сервис-сервис:
```bash
$ curl -s -X POST https://sso.beeline.kg/realms/esim/protocol/openid-connect/token \
    -d grant_type=client_credentials -d client_id=esim-api -d client_secret=$BILLING_CLIENT_SECRET -d scope=billing:charge
{"access_token":"eyJhbGciOiJSUzI1NiIsImtpZCI6...","expires_in":300,"token_type":"Bearer","scope":"billing:charge"}
```

## Подводные камни
- Возврат 401 при ошибке авторизации или 403 при отсутствии учётных данных; клиенты строят логику refresh и входа именно на этих кодах.
- Проверить роль и забыть про владение: `GET /subscribers/{msisdn}` с любым валидным токеном возвращает чужие данные (IDOR, верх списка OWASP, [[owasp-top-10-for-backend]]).
- Проброс access-токена пользователя во все нижестоящие сервисы: в лучшем случае несовпадение audience, в худшем — расползание привилегий. Используй token exchange или client credentials со scopes.
- Использование ID token как учётных данных для API; он для клиента, а API нужен access-токен с правильным `aud`.
- Долгоживущие access-токены «для удобства»: утёкший токен валиден до `exp`. Держи access-токены на 5–15 минутах, ротируй refresh-токены и привязывай их к клиенту.
- Связанные заметки: [[jwt-authentication]] — устройство токена и фильтр, [[api-security-checklist]] — остальной список мер защиты вокруг аутентификации.
