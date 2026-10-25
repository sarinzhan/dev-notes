---
title: JWT authentication in a Spring Boot API
tags: [jwt, spring-security, authentication, tokens]
learned: 2026-10-22
---
## TL;DR
A **JSON Web Token** (RFC 7519) is an open standard for signed access tokens: `header.payload.signature`, each part Base64URL-encoded and joined with dots. The server issues it after login, the client sends it as `Authorization: Bearer <token>`, and the server verifies the signature and claims on every request without storing a session: stateless authentication. In Spring Security you either plug a small custom filter that validates the token and fills the `SecurityContext`, or use the built-in OAuth2 Resource Server (`spring-security-oauth2-resource-server`), which already does that for JWTs from Keycloak or any issuer.

## Key points
### Structure
- **Header**: token type (`"typ":"JWT"`) and signing algorithm (`"alg":"HS256"` or `RS256`/`ES256`), optionally `kid` (key id).
- **Payload**: the **claims**, i.e. user data: registered claims `sub` (subject, usually user id), `iss` (issuer), `aud`, `exp` (expiry), `iat` (issued at), `nbf`, `jti` (unique id); plus custom ones like `roles`, `msisdn`, `tenant`.
- **Signature**: header and payload are Base64URL-encoded separately, concatenated with a dot, and signed with the algorithm from the header using the secret key (HMAC) or a private key (RSA/ECDSA). The three parts are joined with dots.
- The payload is **not encrypted**, only Base64-encoded: anyone can read it. Never put passwords or personal data in it. Encrypted tokens are a separate spec (JWE).
- Symmetric (`HS256`, shared secret, at least 256 bits) is fine when the same service issues and verifies; asymmetric (`RS256`, private key signs, public key verifies, published via JWKS at `/.well-known/jwks.json`) is required when many services verify tokens issued by one auth server.

### Flow
1. `POST /auth/login` with credentials → `AuthenticationManager.authenticate()` → on success generate an **access token** (short-lived, 5-15 min) and a **refresh token** (long-lived, opaque or JWT, stored server-side or rotated).
2. Client stores the tokens (memory or `HttpOnly` cookie for browsers; secure storage on mobile) and sends `Authorization: Bearer <access>` on each request.
3. A filter (`OncePerRequestFilter`) extracts the token, verifies signature + `exp` + `iss`, builds a `UsernamePasswordAuthenticationToken` (or `JwtAuthenticationToken`) with authorities from claims, sets it into `SecurityContextHolder`, and continues the chain. Invalid token → clear context and let `ExceptionTranslationFilter`/entry point return 401.
4. `POST /auth/refresh` exchanges the refresh token for a new pair; logout revokes the refresh token.

### Libraries
- `jjwt` (io.jsonwebtoken 0.12.x): `Jwts.builder()...signWith(key).compact()`, `Jwts.parser().verifyWith(key).build().parseSignedClaims(token)`.
- `java-jwt` (auth0) and Nimbus JOSE+JWT (what Spring Security uses internally).
- Spring Security resource server: `oauth2ResourceServer(o -> o.jwt(Customizer.withDefaults()))` with `spring.security.oauth2.resourceserver.jwt.issuer-uri` or `jwk-set-uri` (or a `JwtDecoder` bean with your secret). Gives `JwtAuthenticationToken`, `@AuthenticationPrincipal Jwt jwt`, and a `JwtAuthenticationConverter` to map a `roles` claim to authorities. Prefer it over a hand-written filter when possible.

### Stateless vs session
- JWT: no server-side lookup per request, horizontally scalable, works across services; but cannot be revoked before `exp` without a denylist, and leaked tokens are valid until expiry. Hence short access tokens plus refresh rotation.
- Session: trivial revocation, no token parsing, but needs sticky sessions or a shared session store (Spring Session + Redis, [[redis-data-structures]]).

## Example
A minimal custom filter with jjwt (for the resource-server route no filter is needed):
```java
@Component
public class JwtAuthFilter extends OncePerRequestFilter {

    private final SecretKey key;
    private final UserDetailsService users;

    JwtAuthFilter(@Value("${security.jwt.secret}") String secret, UserDetailsService users) {
        this.key = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8)); // >= 32 bytes
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
                SecurityContextHolder.clearContext(); // entry point will answer 401
            }
        }
        chain.doFilter(req, res);
    }
}
```
Token creation on login:
```java
String token = Jwts.builder()
        .subject(user.getUsername()).issuer("esim-api")
        .claim("roles", roles)
        .issuedAt(new Date()).expiration(Date.from(Instant.now().plus(15, ChronoUnit.MINUTES)))
        .signWith(key).compact();
```

## Pitfalls
- Accepting `alg: none` or letting the token choose the algorithm (classic jjwt/other library CVEs); always pin the algorithm/key on the verifying side.
- Short secrets for HS256 are brute-forceable; use 256+ random bits from a secret store, not `application.yml` in Git ([[spring-boot-properties-and-profiles]]).
- Long-lived access tokens with no refresh: a leak is a full account takeover for days.
- Storing the JWT in `localStorage` exposes it to XSS; an `HttpOnly; Secure; SameSite` cookie plus CSRF protection is safer for browsers ([[spring-security-basics]]).
- Checking the DB on every request (`loadUserByUsername` in the filter, as above) throws away the stateless benefit; put the authorities in the claims, or cache the lookup.
- Clock skew between services causes spurious `exp`/`nbf` failures; allow a few seconds of leeway and sync clocks.
- Related: [[spring-security-basics]], [[rest-and-http]] (401 vs 403, POST for auth endpoints), [[idempotency]] for token refresh endpoints hit twice.
