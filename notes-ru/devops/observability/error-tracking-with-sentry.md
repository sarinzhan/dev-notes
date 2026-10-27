---
title: Отслеживание ошибок с Sentry — события, issues, сэмплирование, self-hosting
tags: [observability, sentry, spring-boot, incidents]
learned: 2026-10-27
---
## Кратко
Sentry перехватывает **события (events)** — исключение со стеком вызовов, breadcrumbs, контекстом запроса, пользователем, тегами — и группирует их в **issues** по отпечатку (fingerprint), так что 10 000 одинаковых `NullPointerException` — это одна issue со счётчиком, временем первого и последнего появления и состоянием регрессии. Он закрывает пробел между логами (слишком шумные, чтобы за ними следить) и метриками (нет деталей): новая issue или регрессия — это push-уведомление с точным фреймом. В Spring Boot это `sentry-spring-boot-starter-jakarta` плюс DSN; Sentry также встраивается в трейс через общий `traceId` OpenTelemetry. Self-hosting — это серьёзная нагрузка (Kafka, ClickHouse, Redis, Postgres, Symbolicator); правила эксплуатации — это хранение (retention), сэмплирование и очистка персональных данных (PII). Мост Sentry → Telegram с черновиками анализа первопричины от LLM описан в [[sebep-incident-analysis]].

## Основное

### Понятия
| Термин | Значение |
|---|---|
| Event | Одна перехваченная ошибка или сообщение с контекстом (стек, breadcrumbs, теги, пользователь, релиз, окружение) |
| Issue | Группа событий с одинаковым **отпечатком (fingerprint)** (по умолчанию: фреймы стека + тип исключения); имеет состояние new / unresolved / resolved / ignored / regressed |
| Breadcrumbs | Последние N строк лога, HTTP-вызовов, запросов к БД перед ошибкой, прикреплённые к событию |
| Release | Версия (`APP_VERSION`, git SHA) в каждом событии: «впервые замечено в 1.42.0», регрессии после resolve |
| Environment | Разделение `prod` / `staging` для правил алертинга |
| Alert rule | Алерты по issue (новая issue, регрессия, > N событий за M минут) и алерты по метрикам (доля ошибок) → email, Slack, Telegram, webhook |
- Fingerprinting определяет уровень шума: сообщение с идентификатором внутри (`"order 42 not found"`) создаёт по issue на каждый заказ, если сообщение не параметризовано или не задан собственный отпечаток (`event.setFingerprint(List.of("order-not-found"))`).
- Необработанные исключения, доходящие до `ExceptionHandler` Spring MVC, перехватываются автоматически (`sentry.exception-resolver-order`); обработанные бизнес-исключения, которые ты отображаешь в 4xx, отправлять не нужно (`ignored-exceptions-for-type`).

### Интеграция со Spring Boot
- Зависимости: `sentry-spring-boot-starter-jakarta` (+ `sentry-logback`, чтобы превращать логи `ERROR` в события, а `WARN`/`INFO` — в breadcrumbs). Конфигурация в `application.yaml`: `sentry.dsn`, `environment`, `release`, `traces-sample-rate`, `send-default-pii`.
- Контекст: `Sentry.configureScope(s -> s.setTag("msisdn_hash", h))`, `setUser` только с внутренним идентификатором, `Sentry.addBreadcrumb(...)`. Значения MDC прикрепляются автоматически интеграцией с Logback.
- Трассировка: `sentry-opentelemetry-agent` или интеграция с OTel позволяет span производительности Sentry использовать общий `traceId` с Tempo; при низком `traces-sample-rate` (0.05) собственный продукт производительности Sentry остаётся дешёвым, а связь всё равно есть ([[distributed-tracing]]).
- Kafka listeners и задачи `@Scheduled`: исключения не проходят через MVC, поэтому перехватывай их явно в обработчике ошибок или полагайся на то, что `sentry-logback` поймает лог `ERROR` от `DefaultErrorHandler` ([[spring-kafka-consumer]]).

### Сэмплирование и квоты
- `sample-rate` (для ошибок) по умолчанию 1.0; оставь так, если только шторм ошибок не съедает квоту. Лучше: callback `before-send`, который отбрасывает известные шумные исключения, и ограничение частоты по отпечатку в приложении.
- На стороне сервера: лимиты частоты на проект и защита от всплесков (spike protection) не дают шторму съесть диск для хранения; в self-hosted нет биллинга, но есть диск.
- Шторм ошибок (баг в consumer, порождающий тысячи событий в минуту) — нормальная ситуация, а не исключение; проектируй алертинг под неё: алерт на *новую* issue и *регрессию*, а не на каждое событие (история с debounce в [[sebep-incident-analysis]]).

### PII и очистка данных
- `send-default-pii: false` (по умолчанию) отбрасывает cookies, тела запросов и IP пользователей. **Data scrubber** на стороне сервера маскирует поля с именами `password`, `secret`, `token` и значения, похожие на номера карт; добавь собственные шаблоны `[Mm]sisdn|iccid|eid` в настройках проекта.
- `before-send` в приложении может вырезать заголовки запроса (`Authorization`) и данные breadcrumbs ещё до того, как что-либо покинет сервис. С точки зрения комплаенса относись к Sentry как к приёмнику логов ([[owasp-top-10-for-backend]], [[secrets-management]]).
- Контекст исходного кода: Sentry показывает ±5 строк кода вокруг фрейма, только если загружены source bundles (`sentry-maven-plugin` / `sentry-cli`); сам стек вызовов JVM содержит файл и строку, но не текст.

### Эксплуатация self-hosted
- Docker Compose из `getsentry/self-hosted`: web, workers, cron, relay, Snuba, **Kafka** (конвейер событий), **ClickHouse** (хранение и поиск событий), **Redis**, **Postgres** (issues, пользователи, метаданные), Symbolicator, nginx. На практике минимум 16 ГБ RAM, больше при росте трафика.
- Хранение: `SENTRY_EVENT_RETENTION_DAYS` (по умолчанию 90) управляет cron-задачей очистки; первым заполняется диск ClickHouse. Следи за квотами в `sentry.yml`/`config.yml` и свободным местом ClickHouse.
- Обновления запускают `install.sh` с миграциями; сначала сделай бэкап volumes Postgres и ClickHouse. Лаг Kafka consumer внутри Sentry (consumers Snuba) после перезапуска проявляется как «события приходят с опозданием на 20 минут»; мониторь стек Sentry той же Grafana, что и всё остальное ([[grafana-dashboards-and-red-use]], [[logging-stacks]]).
- Исходящие интеграции (Telegram, GitLab) идут через корпоративный прокси; прокси с инспекцией TLS требует, чтобы его CA был и в контейнерах Sentry.

### Sentry, логи и метрики
- Логи: всё, с поиском, без группировки. Метрики: счётчики, без деталей. Sentry: дедуплицированные исключения с контекстом и рабочим процессом (назначить, решить, регрессия). Используй все три; алерты на новые/регрессировавшие issues — из Sentry, на частоты — из метрик, разбирайся в Loki ([[observability-three-pillars]]).

## Пример
Конфигурация Spring Boot с очисткой данных и контролем шума, плюс callback `before-send`, который отбрасывает ожидаемые исключения и маскирует данные абонентов.

```yaml
sentry:
  dsn: ${SENTRY_DSN}
  environment: ${ENV:dev}
  release: esim-provisioning@${APP_VERSION:dev}
  send-default-pii: false
  traces-sample-rate: 0.05                 # span производительности; ошибки по-прежнему 100 %
  max-breadcrumbs: 50
  in-app-includes: [kg.beeline.esim]       # фреймы нашего кода считаются «in app» для группировки и отображения
  ignored-exceptions-for-type:
    - kg.beeline.esim.domain.ProfileNotFoundException   # отображается в 404, это не инцидент
  logging:
    minimum-event-level: error
    minimum-breadcrumb-level: info
```

```java
@Configuration
class SentryConfig {
    private static final Pattern MSISDN = Pattern.compile("996\\d{9}");

    @Bean
    SentryOptions.BeforeSendCallback beforeSend() {
        return (event, hint) -> {
            Throwable t = event.getThrowable();
            if (t instanceof CallNotPermittedException) return null;            // breaker открыт: это покрывает алерт по метрике
            if (event.getRequest() != null) event.getRequest().setHeaders(Map.of());   // никаких утечек Authorization
            if (event.getMessage() != null && event.getMessage().getFormatted() != null) {
                event.getMessage().setFormatted(MSISDN.matcher(event.getMessage().getFormatted()).replaceAll("996*********"));
            }
            event.setTag("component", "provisioning");
            return event;
        };
    }
}
```

## Подводные камни
- Логирование `log.error("failed for {}", msisdn, e)` в цикле retry Kafka: каждая попытка — это событие, и MSISDN попадает в Sentry; логируй один раз в recoverer и очищай данные.
- Каждая обработанная 4xx перехватывается как ошибка: тысячи issues «validation failed» хоронят настоящую. Перехватывай только неожиданные исключения.
- Не задан release: «regressed» никогда не срабатывает, и невозможно понять, какой деплой всё сломал. Задавай `release` из сборки и загружай source bundles.
- Self-hosted Sentry без алертов на хранение и диск: ClickHouse заполняет volume, и весь стек молча перестаёт принимать события.
- Связанные заметки: [[sebep-incident-analysis]], [[observability-three-pillars]], [[slo-sli-and-alerting]], [[exceptions]] (что бросать), [[docker-fundamentals]].
