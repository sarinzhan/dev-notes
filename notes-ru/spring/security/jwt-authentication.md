---
title: JWT-аутентификация в API на Spring Boot
tags: [jwt, spring-security, authentication, tokens]
learned: 2026-10-22
---
## Кратко
**JSON Web Token** (RFC 7519) — открытый стандарт подписанных токенов доступа: `header.payload.signature`, каждая часть закодирована в Base64URL, части соединены точками. Сервер выдаёт токен после входа, клиент отправляет его как `Authorization: Bearer <token>`, а сервер при каждом запросе проверяет подпись и claims, не храня сессию: аутентификация без состояния (stateless). В Spring Security либо подключают небольшой собственный фильтр, который проверяет токен и заполняет `SecurityContext`, либо используют встроенный OAuth2 Resource Server (`spring-security-oauth2-resource-server`), который уже делает это для JWT от Keycloak или любого другого издателя.

## Основное
### Структура
- **Заголовок (header)**: тип токена (`"typ":"JWT"`) и алгоритм подписи (`"alg":"HS256"` или `RS256`/`ES256`), опционально `kid` (id ключа).
- **Полезная нагрузка (payload)**: **claims**, то есть данные пользователя: зарегистрированные claims `sub` (subject, обычно id пользователя), `iss` (издатель), `aud`, `exp` (срок действия), `iat` (время выдачи), `nbf`, `jti` (уникальный id); плюс собственные, например `roles`, `msisdn`, `tenant`.
- **Подпись (signature)**: заголовок и payload по отдельности кодируются в Base64URL, соединяются точкой и подписываются алгоритмом из заголовка с помощью секретного ключа (HMAC) или закрытого ключа (RSA/ECDSA). Три части соединяются точками.
- Payload **не зашифрован**, а только закодирован в Base64: прочитать его может кто угодно. Никогда не клади туда пароли или персональные данные. Зашифрованные токены — отдельная спецификация (JWE).
- Симметричный алгоритм (`HS256`, общий секрет, минимум 256 бит) подходит, когда выдаёт и проверяет один и тот же сервис; асимметричный (`RS256`, закрытый ключ подписывает, открытый проверяет, публикуется через JWKS по адресу `/.well-known/jwks.json`) обязателен, когда многие сервисы проверяют токены, выданные одним сервером аутентификации.

### Последовательность
1. `POST /auth/login` с учётными данными → `AuthenticationManager.authenticate()` → при успехе генерируется **access-токен** (короткоживущий, 5–15 мин) и **refresh-токен** (долгоживущий, непрозрачный или JWT, хранится на сервере или ротируется).
2. Клиент хранит токены (в памяти или в `HttpOnly`-cookie для браузеров; в защищённом хранилище на мобильных) и отправляет `Authorization: Bearer <access>` с каждым запросом.
3. Фильтр (`OncePerRequestFilter`) извлекает токен, проверяет подпись + `exp` + `iss`, строит `UsernamePasswordAuthenticationToken` (или `JwtAuthenticationToken`) с полномочиями из claims, помещает его в `SecurityContextHolder` и продолжает цепочку. Недействительный токен → очистить контекст и дать `ExceptionTranslationFilter`/точке входа вернуть 401.
4. `POST /auth/refresh` обменивает refresh-токен на новую пару; logout отзывает refresh-токен.

### Библиотеки
- `jjwt` (io.jsonwebtoken 0.12.x): `Jwts.builder()...signWith(key).compact()`, `Jwts.parser().verifyWith(key).build().parseSignedClaims(token)`.
- `java-jwt` (auth0) и Nimbus JOSE+JWT (её Spring Security использует внутри).
- Resource server в Spring Security: `oauth2ResourceServer(o -> o.jwt(Customizer.withDefaults()))` с `spring.security.oauth2.resourceserver.jwt.issuer-uri` или `jwk-set-uri` (или бин `JwtDecoder` с твоим секретом). Даёт `JwtAuthenticationToken`, `@AuthenticationPrincipal Jwt jwt` и `JwtAuthenticationConverter` для отображения claim `roles` в полномочия. По возможности предпочитай его самописному фильтру.

### Stateless против сессии
- JWT: нет обращения к серверному хранилищу на каждый запрос, горизонтально масштабируется, работает между сервисами; но его нельзя отозвать до `exp` без чёрного списка (denylist), а утёкшие токены действительны до истечения срока. Отсюда короткие access-токены плюс ротация refresh-токенов.
- Сессия: тривиальный отзыв, не нужно разбирать токен, но нужны sticky-сессии или общее хранилище сессий (Spring Session + Redis, [[redis-data-structures]]).

## Пример
Минимальный собственный фильтр на jjwt (при варианте с resource server фильтр не нужен):
```java
@Component
public class JwtAuthFilter extends OncePerRequestFilter {

    private final SecretKey key;
    private final UserDetailsService users;

    JwtAuthFilter(@Value("${security.jwt.secret}") String secret, UserDetailsService users) {
        this.key = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8)); // >= 32 байт
        this.users = users;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        String header = req.getHeader(HttpHeaders.AUTHORIZATION);
        if (header != null && header.startsWith("Bearer ")
                && SecurityContextHolder.getContext().getAuthentication() == null) {
            try {
                Claims claims = Jwts.parser().verifyWith(key).requireIssuer("esim-api").build()
                        .parseSignedClaims(header.substring(7)).getPayload();
                var user = users.loadUserByUsername(claims.getSubject());
                var auth = new UsernamePasswordAuthenticationToken(user, null, user.getAuthorities());
                auth.setDetails(new WebAuthenticationDetailsSource().buildDetails(req));
                SecurityContextHolder.getContext().setAuthentication(auth);
            } catch (JwtException e) {
                SecurityContextHolder.clearContext(); // точка входа ответит 401
            }
        }
        chain.doFilter(req, res);
    }
}
```
Создание токена при входе:
```java
String token = Jwts.builder()
        .subject(user.getUsername()).issuer("esim-api")
        .claim("roles", roles)
        .issuedAt(new Date()).expiration(Date.from(Instant.now().plus(15, ChronoUnit.MINUTES)))
        .signWith(key).compact();
```

## Подводные камни
- Принятие `alg: none` или выбор алгоритма самим токеном (классические CVE в jjwt и других библиотеках); всегда фиксируй алгоритм/ключ на проверяющей стороне.
- Короткие секреты для HS256 подбираются перебором; используй 256+ случайных бит из хранилища секретов, а не `application.yml` в Git ([[spring-boot-properties-and-profiles]]).
- Долгоживущие access-токены без refresh: утечка означает полный захват аккаунта на несколько дней.
- Хранение JWT в `localStorage` делает его доступным для XSS; для браузеров безопаснее cookie `HttpOnly; Secure; SameSite` плюс защита от CSRF ([[spring-security-basics]]).
- Обращение к БД на каждый запрос (`loadUserByUsername` в фильтре, как выше) сводит на нет преимущество stateless; клади полномочия в claims или кэшируй поиск.
- Рассинхронизация часов между сервисами вызывает ложные ошибки `exp`/`nbf`; допускай погрешность в несколько секунд и синхронизируй часы.
- Связанные заметки: [[spring-security-basics]], [[rest-and-http]] (401 против 403, POST для эндпоинтов аутентификации), [[idempotency]] — для эндпоинтов обновления токена, вызванных дважды.
