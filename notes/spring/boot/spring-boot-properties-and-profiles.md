---
title: Properties, profiles and tuning the embedded server
tags: [spring-boot, properties, profiles, configuration, performance]
learned: 2026-10-22
---
## TL;DR
Spring Boot reads configuration from many **property sources** (`application.yml`, environment variables, command line, etc.) into one `Environment`, with a fixed precedence. **Profiles** are named sets of beans and properties (`dev`, `prod`) activated with `spring.profiles.active`; `@Profile` creates a bean only for the matching profile. Bind values with `@Value` or, better, a typed `@ConfigurationProperties` record. A few server properties (gzip, HTTP/2, keep-alive) give cheap performance wins.

## Key points
### Property files
- A **properties file** stores key-value configuration. Boot accepts `application.properties` and `application.yml`; they differ only in syntax (YAML nests keys and supports lists). Both are loaded from `classpath:/`, `classpath:/config/`, `./`, `./config/`.
- Profile-specific files `application-{profile}.yml` override the base file. In one YAML file `---` plus `spring.config.activate.on-profile: prod` separates documents.
- Precedence (high to low, abbreviated): command-line args → `SPRING_APPLICATION_JSON` → OS environment variables → `application-{profile}.yml` → `application.yml` → `@PropertySource` → defaults. Relaxed binding maps `SERVER_PORT` env var to `server.port`.
- `@PropertySource("classpath:x.properties")` on a `@Configuration` adds an extra file to the `Environment` (properties only, YAML needs a custom factory).
- `spring.config.import` (Boot 2.4+) pulls in extra files or config servers; `optional:` prefix tolerates absence.

### Reading values
- `@Value("${payments.timeout:5s}")` injects one value with a default; supports SpEL `#{...}`.
- `Environment` is a bean giving `getProperty()` and `getActiveProfiles()`; inject it when you need dynamic lookups.
- `@ConfigurationProperties(prefix = "payments")` binds a whole subtree to a class or record, with validation (`@Validated`), metadata for IDE completion (`spring-boot-configuration-processor`) and no string keys scattered around. Enable with `@EnableConfigurationProperties` or `@ConfigurationPropertiesScan`. Preferred for anything beyond one or two values.

### Profiles
- Every running app has a list of **active profiles**; with none set the profile is `default`.
- Set with `spring.profiles.active=dev,kafka-local` (file, env var `SPRING_PROFILES_ACTIVE`, or `-Dspring.profiles.active`). `spring.profiles.default` changes the fallback. `spring.profiles.group.prod=prod-db,prod-kafka` (Boot 2.4+) composes profiles.
- `@Profile("prod")` on a bean or configuration class registers it only when that profile is active; expressions `!dev`, `prod & eu` are allowed. This is one more answer to "two beans of the same type" ([[spring-ioc-and-di]]).
- `@ActiveProfiles("test")` in tests, or `application-test.yml` under `src/test/resources`.
- Secrets do not belong in files in Git: inject them via environment variables, Docker/Kubernetes secrets ([[docker-fundamentals]]) or a config server/Vault.

### Cheap performance settings for the embedded server
- **gzip**: `server.compression.enabled=true`, `server.compression.min-response-size=2KB`, `server.compression.mime-types=application/json,application/xml`. The client must send `Accept-Encoding: gzip`. Big JSON responses shrink 5-10x.
- **HTTP/2**: `server.http2.enabled=true` (needs TLS on Tomcat, or h2c). HTTP/1.1 (1997) uses one connection per in-flight request; HTTP/2 (2015) **multiplexes** many requests over one connection, is binary and compresses headers (HPACK). Connection setup (TCP + TLS handshake) is expensive, so reusing connections matters.
- **Keep-alive** reuses a TCP connection for several HTTP/1.1 requests; tune `server.tomcat.keep-alive-timeout` and `server.tomcat.max-keep-alive-requests`. The client side of this is the connection pool in [[rest-clients]].
- Thread pool: `server.tomcat.threads.max` (default 200); with `spring.threads.virtual.enabled=true` (Boot 3.2+, Java 21) each request gets a [[virtual-threads|virtual thread]] and blocking I/O stops being the limit.
- Graceful shutdown: `server.shutdown=graceful` and `spring.lifecycle.timeout-per-shutdown-phase=30s` so in-flight requests finish on redeploy.
- Datasource pool size matters more than most of the above: see [[hikaricp-pool-sizing]].

## Example
```yaml
# application.yml
spring:
  profiles:
    active: ${APP_PROFILE:dev}
server:
  compression:
    enabled: true
    min-response-size: 2KB
    mime-types: application/json
  shutdown: graceful
esim:
  vendor:
    base-url: https://sandbox.vendor.example
    api-key: ${ESIM_VENDOR_KEY}
    timeout: 5s
---
spring:
  config:
    activate:
      on-profile: prod
esim:
  vendor:
    base-url: https://api.vendor.example
```
```java
@Validated
@ConfigurationProperties(prefix = "esim.vendor")
public record EsimVendorProps(@NotBlank String baseUrl,
                              @NotBlank String apiKey,
                              @DurationUnit(ChronoUnit.SECONDS) Duration timeout) {}
```

## Pitfalls
- `@Value` on a field of a `static` class or before the context is refreshed stays `null`; constants cannot come from properties.
- A typo in a property key is silent with `@Value` (unless no default) and loud with `@ConfigurationProperties` + `@Validated` + `ignoreUnknownFields=false`.
- Several active profiles with conflicting keys: the **last** listed profile wins, which surprises people when `spring.profiles.active=prod,local`.
- YAML gotchas: `on`/`yes` become booleans, `010` is octal, values with `:` or `#` need quotes.
- Enabling gzip for already-compressed or tiny responses wastes CPU; keep `min-response-size` and limit MIME types.
- Related: [[spring-boot-starters-and-autoconfiguration]] (`@ConditionalOnProperty` ties autoconfig to properties), [[logging-stacks]] for shipping the logs these settings produce.
