---
title: Cookies, sessions, same-origin policy and CORS
tags: [networking, http, cookies, cors, csrf]
learned: 2026-10-27
---
## TL;DR
HTTP is stateless; cookies are the browser-side mechanism that attaches a small piece of state (usually a session id or a token) to every request for a domain. The same-origin policy stops JavaScript on one origin from reading responses from another, and CORS is the server-controlled exception to it, enforced by the browser only. `SameSite`, `HttpOnly` and `Secure` are the three cookie flags that decide whether a session survives XSS and CSRF. Spring's CORS configuration and the REST-side view are in [[rest-and-http]]; this note is the protocol and the security model behind it.

## Key points
### Cookies
- Server sets: `Set-Cookie: SESSION=abc; Path=/; Domain=beeline.kg; Max-Age=3600; Secure; HttpOnly; SameSite=Lax`. Browser sends back `Cookie: SESSION=abc` on every matching request, automatically, including requests triggered by other sites. That automatic sending is both the feature and the CSRF problem.
- Scope: `Domain` (host and subdomains when set; exact host only when omitted), `Path`. Cookies ignore port and scheme unless `Secure` is set, which is why localhost dev on different ports shares cookies.
- `HttpOnly`: not readable from `document.cookie`, so XSS cannot steal it. `Secure`: sent only over HTTPS. `Max-Age`/`Expires`: persistent; without them the cookie dies with the browser session.
- `SameSite`: `Strict` (never sent on cross-site requests, breaks links from email), `Lax` (sent on top-level GET navigations, not on cross-site POST/fetch; browser default since 2020), `None` (sent everywhere, requires `Secure`; needed for third-party embedding and some OAuth redirect flows).
- Size limit ~4 KB per cookie and all cookies travel with every request, including static assets: do not put a 3 KB JWT plus profile data in cookies ([[http-1-1-vs-2-vs-3]] HPACK softens this but does not fix it).

### Sessions: server-side state vs tokens
| | Server session (cookie = id) | Token (JWT in cookie or header) |
|---|---|---|
| Where the state lives | server memory / Redis (Spring Session) | inside the token, signed |
| Revocation | delete the session, immediate | hard: short expiry + refresh token or a denylist |
| Horizontal scaling | needs shared store or sticky sessions ([[load-balancers-l4-vs-l7]]) | stateless, any instance validates |
| Payload | arbitrary | small claims only |
| CSRF exposure | yes if cookie-based | yes if cookie-based; no if sent as `Authorization` header by JS |
| XSS exposure | `HttpOnly` protects the id | `HttpOnly` cookie protects; `localStorage` does not |

- Details of JWT structure and Spring Security setup: [[jwt-authentication]], [[spring-security-basics]]; the general concepts in [[authentication-vs-authorization]].

### Same-origin policy
- Origin = scheme + host + port. `https://app.beeline.kg` and `https://api.beeline.kg` are different origins; so are `:443` and `:8443`.
- JavaScript may *send* requests anywhere (forms, images, scripts always could); it may not *read* the response from another origin unless CORS allows it. The request often reaches your server; the browser only hides the answer. Side effects of a POST still happen, which is why CORS is not a CSRF defence.

### CORS protocol
- **Simple requests** (GET/HEAD/POST with only safe headers and `Content-Type` of form/text types): sent directly; the browser checks `Access-Control-Allow-Origin` on the response.
- **Preflight**: anything else (JSON `Content-Type`, `Authorization` header, PUT/DELETE) triggers `OPTIONS` first with `Origin`, `Access-Control-Request-Method`, `Access-Control-Request-Headers`. The server must answer `204` with `Access-Control-Allow-Origin`, `-Methods`, `-Headers`, optionally `-Max-Age` to cache the preflight (browsers cap it at 2 h in Chromium).
- `Access-Control-Allow-Credentials: true` is required for cookies or `Authorization` to be sent, and then `Allow-Origin` must be the exact origin, not `*`.
- `Access-Control-Expose-Headers` lists non-default response headers JS may read (`Location`, `X-Request-ID`); otherwise only a few simple ones are visible.
- Responses that vary per origin need `Vary: Origin` so caches keep them apart ([[http-caching]]).
- CORS is enforced by browsers only: curl, mobile apps and other servers ignore it. It protects users from malicious sites, not your API from clients.

### CSRF
- Attack: a page on `evil.example` submits a form to `https://api.beeline.kg/transfer`; the browser attaches the session cookie; the server cannot tell it from a legitimate request.
- Defences, layered: `SameSite=Lax` (or `Strict`) on the session cookie; a CSRF token the attacker cannot read (synchronizer token in a form field or `X-XSRF-TOKEN` header, Spring Security's `CookieCsrfTokenRepository`); checking `Origin`/`Referer`. Pure header-token APIs (`Authorization: Bearer`) are immune because the browser never adds that header on its own, which is why Spring Security lets you disable CSRF for stateless APIs.

## Example
The most common production CORS setup: a SPA on one origin, API on another, session cookie with sane flags, preflight cached. Spring Boot 3 with Spring Security present (so the CORS filter runs before security).
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
        cfg.setAllowedOrigins(List.of("https://my.beeline.kg"));          // exact origin, never "*" with credentials
        cfg.setAllowedMethods(List.of("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"));
        cfg.setAllowedHeaders(List.of("Content-Type", "X-XSRF-TOKEN", "X-Request-ID"));
        cfg.setExposedHeaders(List.of("Location", "X-Request-ID"));
        cfg.setAllowCredentials(true);                                      // send the session cookie
        cfg.setMaxAge(Duration.ofHours(1));                                 // cache the preflight
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
Preflight as the browser sends it:
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

## Pitfalls
- `Access-Control-Allow-Origin: *` together with credentials: browsers reject it, and reflecting any `Origin` back with credentials is equivalent to no protection.
- Fixing CORS in nginx and in Spring at the same time produces duplicate headers, which browsers treat as invalid.
- Storing a JWT in `localStorage` to "avoid CSRF": trades CSRF for XSS theft; an `HttpOnly` cookie plus `SameSite` and a CSRF token is the safer default for browser clients.
- `SameSite=Lax` breaks OAuth/OIDC callbacks that POST back to your site; those flows need `SameSite=None; Secure` on the state cookie.
- Disabling CSRF globally because "we use tokens" while the same app also serves a cookie-authenticated admin UI.
- Related: [[rest-and-http]] for CORS from the API-design angle, [[api-security-checklist]] for the rest of the browser-facing hardening list.
