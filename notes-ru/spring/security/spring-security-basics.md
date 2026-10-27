---
title: Spring Security 6: цепочка фильтров, аутентификация и авторизация
tags: [spring-security, authentication, authorization, filters]
learned: 2026-10-22
---
## Кратко
Spring Security — это цепочка сервлетных **фильтров**, стоящих перед `DispatcherServlet`. **Аутентификация** (кто ты) создаёт объект `Authentication`, который хранится в `SecurityContextHolder`; **авторизация** (что тебе можно) сверяет его с правилами для URL и аннотациями на методах. В Spring Security 6 / Boot 3 всё настраивается через **бин `SecurityFilterChain`** и лямбда-DSL; `WebSecurityConfigurerAdapter`, `antMatchers()` и `authorizeRequests()` удалены.

## Основное
### Архитектура
- `DelegatingFilterProxy` (сервлетный фильтр) передаёт каждый запрос в `FilterChainProxy`, который выбирает первую подходящую `SecurityFilterChain` (их может быть несколько, например одна для `/api/**`, другая для UI) и выполняет её фильтры по порядку.
- Важные фильтры по порядку: `CorsFilter`, `CsrfFilter`, `LogoutFilter`, фильтры аутентификации (`UsernamePasswordAuthenticationFilter`, `BasicAuthenticationFilter`, `BearerTokenAuthenticationFilter`, твой JWT-фильтр), `ExceptionTranslationFilter` (превращает `AuthenticationException` в 401 через `AuthenticationEntryPoint`, а `AccessDeniedException` — в 403 через `AccessDeniedHandler`) и, наконец, `AuthorizationFilter`, который проверяет правила для URL.
- Поскольку это происходит **до** `DispatcherServlet`, исключения здесь не доходят до `@ControllerAdvice` ([[spring-mvc-request-lifecycle]]).

### Аутентификация
- `Authentication`: principal (обычно `UserDetails`), учётные данные (credentials), `authorities` (`GrantedAuthority`; роли — это полномочия с префиксом `ROLE_`), флаг `authenticated`.
- `AuthenticationManager` (`ProviderManager`) делегирует `AuthenticationProvider`'ам: `DaoAuthenticationProvider` загружает пользователя через `UserDetailsService` и сравнивает пароли с помощью `PasswordEncoder` (`BCryptPasswordEncoder`; по умолчанию используется `DelegatingPasswordEncoder` с префиксами `{bcrypt}`). Другие: LDAP, вход через OAuth2/OIDC, JWT resource server, свой собственный.
- Результат хранится в `SecurityContextHolder` (по умолчанию `ThreadLocal`); `@AuthenticationPrincipal` или `Principal` в контроллере читают его. Для передачи в `@Async` или виртуальные потоки используй `DelegatingSecurityContextExecutor` или `SecurityContextHolder.setStrategyName(MODE_INHERITABLETHREADLOCAL)`.
- API без состояния задают `sessionManagement(s -> s.sessionCreationPolicy(STATELESS))`, чтобы не создавался `JSESSIONID`; токен в каждом запросе заново аутентифицирует пользователя ([[jwt-authentication]]).
- Аутентификация и авторизация: 401 означает «не аутентифицирован (или аутентифицирован неверно)»; 403 — «аутентифицирован, но доступ запрещён» ([[rest-and-http]]).

### Авторизация
- Уровень URL: `authorizeHttpRequests(a -> a.requestMatchers("/admin/**").hasRole("ADMIN").anyRequest().authenticated())`. Spring Security 6 использует `requestMatchers` (шаблоны путей через `PathPatternRequestMatcher` в 6.5+); `antMatchers`/`mvcMatchers` удалены.
- Уровень методов: `@EnableMethodSecurity` (заменяет `@EnableGlobalMethodSecurity`) и `@PreAuthorize("hasRole('ADMIN') or #msisdn == authentication.name")`, `@PostAuthorize`, `@PreFilter`/`@PostFilter` или более простой `@Secured`. Реализовано через AOP-прокси, поэтому вызов самого себя обходит проверку ([[spring-aop]]).
- `hasRole("ADMIN")` проверяет полномочие `ROLE_ADMIN`; `hasAuthority("esim:write")` проверяет буквальную строку. Используй детальные полномочия для областей (scopes) API, роли — для крупных групп.

### CSRF, CORS, заголовки
- Защита от **CSRF** включена по умолчанию и важна для браузерных сессий с cookie. Для API на токенах без состояния, где браузер никогда не отправляет cookie аутентификации, её отключают: `csrf(AbstractHttpConfigurer::disable)`. Если используешь cookie, оставь защиту и отдавай токен (`CookieCsrfTokenRepository.withHttpOnlyFalse()`); для SPA Spring Security 6 к тому же требует танцев с `XorCsrfTokenRequestAttributeHandler`.
- **CORS**: `cors(Customizer.withDefaults())` с бином `CorsConfigurationSource`, чтобы предварительный запрос (preflight) `OPTIONS` проходил до аутентификации.
- Заголовки безопасности (`X-Content-Type-Options`, `Cache-Control`, HSTS, frame options) добавляются по умолчанию.
- Поведение Boot по умолчанию: если бина `SecurityFilterChain` нет, каждый эндпоинт требует HTTP Basic или вход через форму со сгенерированным паролем, который печатается при запуске. Определение своей цепочки заменяет это поведение.

## Пример
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

## Подводные камни
- Миграция с Security 5: `WebSecurityConfigurerAdapter`, `antMatchers`, `authorizeRequests`, цепочки через `and()`, `@EnableGlobalMethodSecurity` — всё удалено в 6.x; используй бины и лямбды.
- Правила проверяются по порядку; `anyRequest().authenticated()` перед `permitAll()` делает последний мёртвым. `anyRequest()` должен быть последним.
- `permitAll()` всё равно запускает фильтры аутентификации; сломанный токен на публичном эндпоинте может дать 401, если фильтр не игнорирует отсутствующие/недействительные токены на этих путях.
- Несколько бинов `SecurityFilterChain` без `securityMatcher` или `@Order`: первый перехватывает всё.
- `SecurityContextHolder` привязан к потоку: контекст пуст в методах `@Async`, Kafka listener'ах ([[spring-kafka-consumer]]) и задачах по расписанию, если его явно не передать.
- Хранить пароли чем-либо, кроме медленного адаптивного хэша (bcrypt, argon2, scrypt), — это баг, а не компромисс.
- Связанные заметки: [[jwt-authentication]], [[spring-mvc-request-lifecycle]], [[openapi-swagger]] (открытие доступа к документации).
