---
title: Spring Security 6: filter chain, authentication and authorization
tags: [spring-security, authentication, authorization, filters]
learned: 2026-10-22
---
## TL;DR
Spring Security is a chain of servlet **filters** placed in front of `DispatcherServlet`. **Authentication** (who are you) produces an `Authentication` object stored in the `SecurityContextHolder`; **authorization** (what may you do) checks it against URL rules and method annotations. In Spring Security 6 / Boot 3 you configure everything with a **`SecurityFilterChain` bean** and the lambda DSL; `WebSecurityConfigurerAdapter`, `antMatchers()` and `authorizeRequests()` are gone.

## Key points
### Architecture
- `DelegatingFilterProxy` (a servlet filter) hands every request to `FilterChainProxy`, which picks the first matching `SecurityFilterChain` (you can have several, e.g. one for `/api/**`, one for the UI) and runs its filters in order.
- Important filters in order: `CorsFilter`, `CsrfFilter`, `LogoutFilter`, authentication filters (`UsernamePasswordAuthenticationFilter`, `BasicAuthenticationFilter`, `BearerTokenAuthenticationFilter`, your JWT filter), `ExceptionTranslationFilter` (turns `AuthenticationException` into 401 via `AuthenticationEntryPoint` and `AccessDeniedException` into 403 via `AccessDeniedHandler`), and finally `AuthorizationFilter` which evaluates the URL rules.
- Because this happens **before** the `DispatcherServlet`, exceptions here do not reach `@ControllerAdvice` ([[spring-mvc-request-lifecycle]]).

### Authentication
- `Authentication`: principal (usually `UserDetails`), credentials, `authorities` (`GrantedAuthority`, roles are authorities prefixed `ROLE_`), `authenticated` flag.
- `AuthenticationManager` (`ProviderManager`) delegates to `AuthenticationProvider`s: `DaoAuthenticationProvider` loads the user via `UserDetailsService` and compares passwords with a `PasswordEncoder` (`BCryptPasswordEncoder`; `DelegatingPasswordEncoder` with `{bcrypt}` prefixes is the default). Others: LDAP, OAuth2/OIDC login, JWT resource server, your own.
- The result is stored in `SecurityContextHolder` (a `ThreadLocal` by default); `@AuthenticationPrincipal` or `Principal` in a controller reads it. For `@Async` or virtual-thread propagation use `DelegatingSecurityContextExecutor` or `SecurityContextHolder.setStrategyName(MODE_INHERITABLETHREADLOCAL)`.
- Stateless APIs set `sessionManagement(s -> s.sessionCreationPolicy(STATELESS))` so no `JSESSIONID` is created; the token on each request re-authenticates ([[jwt-authentication]]).
- Authentication vs authorization: 401 means not (or badly) authenticated; 403 means authenticated but not permitted ([[rest-and-http]]).

### Authorization
- URL level: `authorizeHttpRequests(a -> a.requestMatchers("/admin/**").hasRole("ADMIN").anyRequest().authenticated())`. Spring Security 6 uses `requestMatchers` (path patterns via `PathPatternRequestMatcher` in 6.5+); `antMatchers`/`mvcMatchers` were removed.
- Method level: `@EnableMethodSecurity` (replaces `@EnableGlobalMethodSecurity`) and `@PreAuthorize("hasRole('ADMIN') or #msisdn == authentication.name")`, `@PostAuthorize`, `@PreFilter`/`@PostFilter`, or the simpler `@Secured`. Implemented with an AOP proxy, so self-invocation bypasses it ([[spring-aop]]).
- `hasRole("ADMIN")` checks authority `ROLE_ADMIN`; `hasAuthority("esim:write")` checks the literal string. Use fine-grained authorities for API scopes, roles for coarse groups.

### CSRF, CORS, headers
- **CSRF** protection is on by default and matters for browser sessions with cookies. For a stateless token API where the browser never sends an auth cookie, disable it: `csrf(AbstractHttpConfigurer::disable)`. If you use cookies, keep it and expose the token (`CookieCsrfTokenRepository.withHttpOnlyFalse()`); Spring Security 6 also requires the `XorCsrfTokenRequestAttributeHandler` dance for SPAs.
- **CORS**: `cors(Customizer.withDefaults())` with a `CorsConfigurationSource` bean, so preflight `OPTIONS` passes before authentication.
- Security headers (`X-Content-Type-Options`, `Cache-Control`, HSTS, frame options) are added by default.
- Boot's default: if no `SecurityFilterChain` bean exists, every endpoint needs HTTP Basic or form login with a generated password printed at startup. Defining your own chain replaces that.

## Example
```java
@Configuration
@EnableWebSecurity
@EnableMethodSecurity
public class SecurityConfig {

    @Bean
    SecurityFilterChain api(HttpSecurity http, JwtAuthFilter jwtFilter) throws Exception {
        http.securityMatcher("/api/**")
            .csrf(AbstractHttpConfigurer::disable)
            .cors(Customizer.withDefaults())
            .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .authorizeHttpRequests(a -> a
                .requestMatchers(HttpMethod.POST, "/api/v1/auth/**").permitAll()
                .requestMatchers("/api/v1/admin/**").hasRole("ADMIN")
                .requestMatchers(HttpMethod.GET, "/api/v1/esims/**").hasAuthority("esim:read")
                .anyRequest().authenticated())
            .exceptionHandling(e -> e
                .authenticationEntryPoint((req, res, ex) -> res.sendError(401))
                .accessDeniedHandler((req, res, ex) -> res.sendError(403)))
            .addFilterBefore(jwtFilter, UsernamePasswordAuthenticationFilter.class);
        return http.build();
    }

    @Bean
    SecurityFilterChain docs(HttpSecurity http) throws Exception {
        http.securityMatcher("/v3/api-docs/**", "/swagger-ui/**", "/actuator/health")
            .authorizeHttpRequests(a -> a.anyRequest().permitAll());
        return http.build();
    }

    @Bean PasswordEncoder passwordEncoder() { return new BCryptPasswordEncoder(); }

    @Bean
    AuthenticationManager authenticationManager(AuthenticationConfiguration cfg) throws Exception {
        return cfg.getAuthenticationManager();
    }
}
```

## Pitfalls
- Migrating from Security 5: `WebSecurityConfigurerAdapter`, `antMatchers`, `authorizeRequests`, `and()` chaining, `@EnableGlobalMethodSecurity` are all removed in 6.x; use beans and lambdas.
- Rules are evaluated in order; `anyRequest().authenticated()` before a `permitAll()` makes the latter dead. `anyRequest()` must be last.
- `permitAll()` still runs the authentication filters; a broken token on a public endpoint can give 401 unless the filter ignores missing/invalid tokens on those paths.
- Several `SecurityFilterChain` beans without `securityMatcher` or `@Order`: the first one matches everything.
- `SecurityContextHolder` is thread-local: the context is empty in `@Async` methods, Kafka listeners ([[spring-kafka-consumer]]) and scheduled jobs unless propagated explicitly.
- Storing passwords with anything but a slow adaptive hash (bcrypt, argon2, scrypt) is a bug, not a trade-off.
- Related: [[jwt-authentication]], [[spring-mvc-request-lifecycle]], [[openapi-swagger]] (whitelisting the docs).
