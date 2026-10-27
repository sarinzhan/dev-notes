---
title: Authentication vs authorization, tokens and OAuth2/OIDC flows
tags: [networking, security, oauth2, oidc, tokens]
learned: 2026-10-27
---
## TL;DR
Authentication answers "who is calling" (401 when it fails); authorization answers "may they do this" (403). Sessions keep the answer on the server behind an opaque cookie; tokens carry it in the request, either as an opaque reference the server looks up or as a self-contained signed JWT any service can verify. OAuth2 is the protocol for delegating access to an API without sharing passwords; OIDC adds an identity layer (the ID token) so the same flow also logs the user in. In a Spring backend you mostly end up as an OAuth2 **resource server** validating JWTs from Keycloak or a cloud IdP; the JWT mechanics and Spring filter are in [[jwt-authentication]], this note is the map around it.

## Key points
### The two questions
| | Authentication (AuthN) | Authorization (AuthZ) |
|---|---|---|
| Question | who are you | what may you do |
| Evidence | password, certificate ([[mtls-and-service-to-service-auth]]), token, OTP | roles, scopes, ownership, policy |
| HTTP failure | `401 Unauthorized` + `WWW-Authenticate` | `403 Forbidden` (or `404` to hide existence) |
| Spring Security piece | `AuthenticationManager`, filters, `SecurityContext` | `authorizeHttpRequests`, `@PreAuthorize`, method security ([[spring-security-basics]]) |
| Typical bug | accepting an expired token | checking the role but not the ownership (IDOR) |

- Authorization models: **RBAC** (roles → permissions; simple, coarse), **ABAC** (attributes of subject, resource, context; `msisdn == token.sub`), **ReBAC** (relationships, Zanzibar-style, for sharing graphs). Most services are RBAC for coarse routes plus ownership checks in code.

### Sessions vs opaque tokens vs JWT
| | Server session | Opaque token | JWT |
|---|---|---|---|
| What the client holds | random id in a cookie ([[cookies-sessions-and-cors]]) | random string, usually `Bearer` | signed claims, `Bearer` |
| Verification | lookup in session store | introspection call to the issuer (`/introspect`) or shared store | local signature check with the issuer's public key (JWKS) |
| Revocation | instant | instant | only at expiry; short TTL + refresh rotation, or a denylist that reintroduces the lookup |
| Cross-service | needs shared store | every service calls the issuer | every service verifies alone |
| Size on the wire | tiny | tiny | 500 B – 2 KB on every request |
| Pick when | browser app, one backend, want instant logout | few services, strict revocation (banking) | many services, gateway-validated, short-lived access tokens |

### OAuth2 roles and flows
- **Roles**: resource owner (user), client (the app wanting access), authorization server (Keycloak, Auth0, Entra ID), resource server (your API). Access tokens are for APIs; the ID token (OIDC) is for the client to know who logged in; refresh tokens get new access tokens.
- **Authorization Code + PKCE**: browser/mobile apps. User is redirected to the IdP, logs in, comes back with a one-time `code`, the client exchanges it for tokens; PKCE (`code_verifier`/`code_challenge`) protects the code from interception so public clients need no secret. The only flow for user login today; Implicit and Resource Owner Password flows are deprecated in OAuth 2.1.
- **Client Credentials**: service-to-service, no user. Client id + secret (or a private-key JWT, or mTLS) for an access token with scopes. Spring: `spring-security-oauth2-client` with `ClientRegistration` and an `OAuth2AuthorizedClientManager` adds the token to `RestClient`/`WebClient`.
- **Device Code**: TVs, CLIs: show a code, user approves on a phone.
- **Token exchange (RFC 8693)**: a service swaps an incoming user token for a downstream token with narrower scope; the proper fix for "forward the user's token everywhere".
- **OIDC** on top: `openid` scope, ID token (JWT with `sub`, `iss`, `aud`, `nonce`), `/.well-known/openid-configuration` discovery document (issuer, JWKS URL, endpoints), `/userinfo`.

### Scopes vs roles
- **Scopes** describe what the client was granted (`esim:read`, `esim:activate`); **roles/permissions** describe what the user may do. An API checks both: the token must carry the scope and the user must have the right. Keycloak puts realm roles in `realm_access.roles`; Spring maps scopes to `SCOPE_x` authorities by default and needs a converter for roles ([[jwt-authentication]]).

### Validation checklist for a resource server
1. Signature with the key identified by `kid` from the issuer's JWKS (cache it, refresh on unknown `kid`).
2. `iss` equals the expected issuer; `aud` contains this API; `exp`/`nbf` with small clock skew; `alg` is from an allow-list (never `none`, never trust the header to pick the key type).
3. Then authorization: scopes, roles, ownership of the resource in the path.
4. Log the `sub` and `jti`, never the token ([[api-security-checklist]]).

## Example
A Spring Boot 3 resource server validating Keycloak tokens, mapping realm roles, and doing the ownership check that RBAC alone misses. Plus the client side for a service-to-service call with client credentials.
```yaml
spring:
  security:
    oauth2:
      resourceserver:
        jwt:
          issuer-uri: https://sso.beeline.kg/realms/esim     # discovery -> JWKS, iss check
          audiences: esim-api                                 # aud check (Spring Boot 3.1+)
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
    @PreAuthorize("hasRole('SUPPORT') or #msisdn == authentication.token.claims['msisdn']")   // ownership, not only role
    List<EsimDto> list(@PathVariable String msisdn) { ... }
}
```
What the token exchange looks like for the service-to-service call:
```bash
$ curl -s -X POST https://sso.beeline.kg/realms/esim/protocol/openid-connect/token \
    -d grant_type=client_credentials -d client_id=esim-api -d client_secret=$BILLING_CLIENT_SECRET -d scope=billing:charge
{"access_token":"eyJhbGciOiJSUzI1NiIsImtpZCI6...","expires_in":300,"token_type":"Bearer","scope":"billing:charge"}
```

## Pitfalls
- Returning 401 for authorization failures or 403 for missing credentials; clients implement refresh and login logic based on these codes.
- Checking the role and forgetting ownership: `GET /subscribers/{msisdn}` with any valid token returns anyone's data (IDOR, OWASP top of the list, [[owasp-top-10-for-backend]]).
- Forwarding the user's access token to every downstream service: audience mismatch at best, privilege sprawl at worst. Use token exchange or client credentials with scopes.
- Treating the ID token as an API credential; it is for the client, APIs want the access token with the right `aud`.
- Long-lived access tokens "for convenience": a leaked token is valid until `exp`. Keep access tokens at 5–15 minutes, rotate refresh tokens, and bind them to the client.
- Related: [[jwt-authentication]] for the token internals and filter, [[api-security-checklist]] for the rest of the hardening list around auth.
