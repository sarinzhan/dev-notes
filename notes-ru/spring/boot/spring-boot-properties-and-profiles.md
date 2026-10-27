---
title: Свойства, профили и настройка встроенного сервера
tags: [spring-boot, properties, profiles, configuration, performance]
learned: 2026-10-22
---
## Кратко
Spring Boot читает конфигурацию из множества **источников свойств (property sources)** (`application.yml`, переменные окружения, командная строка и т. д.) в один `Environment` с фиксированным приоритетом. **Профили (profiles)** — это именованные наборы бинов и свойств (`dev`, `prod`), которые активируются через `spring.profiles.active`; `@Profile` создаёт бин только для подходящего профиля. Значения связывают через `@Value` или, что лучше, через типизированный record с `@ConfigurationProperties`. Несколько свойств сервера (gzip, HTTP/2, keep-alive) дают дешёвый прирост производительности.

## Основное
### Файлы свойств
- **Файл свойств (properties file)** хранит конфигурацию в виде пар «ключ — значение». Boot принимает `application.properties` и `application.yml`; они отличаются только синтаксисом (YAML вкладывает ключи и поддерживает списки). Оба загружаются из `classpath:/`, `classpath:/config/`, `./`, `./config/`.
- Файлы конкретного профиля `application-{profile}.yml` переопределяют базовый файл. В одном YAML-файле документы разделяются `---` плюс `spring.config.activate.on-profile: prod`.
- Приоритет (от высокого к низкому, сокращённо): аргументы командной строки → `SPRING_APPLICATION_JSON` → переменные окружения ОС → `application-{profile}.yml` → `application.yml` → `@PropertySource` → значения по умолчанию. Гибкое связывание (relaxed binding) сопоставляет переменную окружения `SERVER_PORT` со свойством `server.port`.
- `@PropertySource("classpath:x.properties")` на классе `@Configuration` добавляет в `Environment` дополнительный файл (только properties, для YAML нужна своя фабрика).
- `spring.config.import` (Boot 2.4+) подключает дополнительные файлы или серверы конфигурации; префикс `optional:` допускает их отсутствие.

### Чтение значений
- `@Value("${payments.timeout:5s}")` внедряет одно значение со значением по умолчанию; поддерживает SpEL `#{...}`.
- `Environment` — это бин с методами `getProperty()` и `getActiveProfiles()`; его внедряют, когда нужен динамический поиск.
- `@ConfigurationProperties(prefix = "payments")` связывает целое поддерево с классом или record'ом, с валидацией (`@Validated`), метаданными для автодополнения в IDE (`spring-boot-configuration-processor`) и без разбросанных по коду строковых ключей. Включается через `@EnableConfigurationProperties` или `@ConfigurationPropertiesScan`. Предпочтительный способ для всего, что больше одного-двух значений.

### Профили
- У каждого запущенного приложения есть список **активных профилей**; если ничего не задано, профиль — `default`.
- Задаётся через `spring.profiles.active=dev,kafka-local` (в файле, переменной окружения `SPRING_PROFILES_ACTIVE` или `-Dspring.profiles.active`). `spring.profiles.default` меняет профиль по умолчанию. `spring.profiles.group.prod=prod-db,prod-kafka` (Boot 2.4+) объединяет профили.
- `@Profile("prod")` на бине или конфигурационном классе регистрирует его, только когда этот профиль активен; допустимы выражения `!dev`, `prod & eu`. Это ещё один ответ на проблему «два бина одного типа» ([[spring-ioc-and-di]]).
- В тестах — `@ActiveProfiles("test")` или `application-test.yml` в `src/test/resources`.
- Секретам не место в файлах в Git: их передают через переменные окружения, секреты Docker/Kubernetes ([[docker-fundamentals]]) или сервер конфигурации/Vault.

### Дешёвые настройки производительности встроенного сервера
- **gzip**: `server.compression.enabled=true`, `server.compression.min-response-size=2KB`, `server.compression.mime-types=application/json,application/xml`. Клиент должен отправлять `Accept-Encoding: gzip`. Большие JSON-ответы сжимаются в 5–10 раз.
- **HTTP/2**: `server.http2.enabled=true` (на Tomcat требует TLS или h2c). HTTP/1.1 (1997) использует одно соединение на каждый выполняющийся запрос; HTTP/2 (2015) **мультиплексирует** много запросов в одном соединении, он бинарный и сжимает заголовки (HPACK). Установка соединения (TCP + TLS handshake) дорогая, поэтому повторное использование соединений важно.
- **Keep-alive** переиспользует TCP-соединение для нескольких запросов HTTP/1.1; настраивается через `server.tomcat.keep-alive-timeout` и `server.tomcat.max-keep-alive-requests`. Клиентская сторона этого механизма — пул соединений в [[rest-clients]].
- Пул потоков (thread pool): `server.tomcat.threads.max` (по умолчанию 200); с `spring.threads.virtual.enabled=true` (Boot 3.2+, Java 21) каждый запрос получает [[virtual-threads|виртуальный поток]], и блокирующий ввод-вывод перестаёт быть ограничением.
- Плавное завершение (graceful shutdown): `server.shutdown=graceful` и `spring.lifecycle.timeout-per-shutdown-phase=30s`, чтобы выполняющиеся запросы завершились при повторном deploy.
- Размер пула источника данных важнее большинства перечисленного: см. [[hikaricp-pool-sizing]].

## Пример
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

## Подводные камни
- `@Value` на поле `static`-класса или до обновления (refresh) контекста остаётся `null`; константы нельзя брать из свойств.
- Опечатка в ключе свойства молча проходит с `@Value` (если только нет значения по умолчанию) и громко падает с `@ConfigurationProperties` + `@Validated` + `ignoreUnknownFields=false`.
- Несколько активных профилей с конфликтующими ключами: побеждает **последний** в списке профиль, что удивляет, когда `spring.profiles.active=prod,local`.
- Ловушки YAML: `on`/`yes` превращаются в boolean, `010` — восьмеричное число, значения с `:` или `#` нужно брать в кавычки.
- Включение gzip для уже сжатых или крошечных ответов тратит CPU впустую; задавай `min-response-size` и ограничивай MIME-типы.
- Связанные заметки: [[spring-boot-starters-and-autoconfiguration]] (`@ConditionalOnProperty` связывает автоконфигурацию со свойствами), [[logging-stacks]] — для отправки логов, которые порождают эти настройки.
