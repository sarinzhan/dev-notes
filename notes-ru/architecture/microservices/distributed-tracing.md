---
title: Распределённая трассировка с OpenTelemetry в Spring Boot 3
tags: [observability, opentelemetry, tracing, spring-boot, kafka]
learned: 2026-10-27
---
## Кратко
**Трасса** (trace) — полная история одного запроса через сервисы; это дерево **span-ов**, каждый из которых — единица работы с измеренным временем и атрибутами. Trace id передаётся между сервисами в заголовке W3C `traceparent` (HTTP) или в заголовке записи Kafka, поэтому каждый сервис может прикрепить свои span-ы к тому же дереву. В Spring Boot 3 трассировку дают либо **OpenTelemetry Java agent** (ноль кода, подключается при старте), либо **Micrometer Tracing** внутри приложения; оба экспортируют в OTel Collector, который пересылает в Tempo или Jaeger. Если положить `traceId` в MDC логов, можно перейти от строки лога ко всей трассе.

## Какую проблему решает
- **Без этого:** заказ eSIM проходит через gateway, `esim-provisioning`, топик Kafka, вызов SM-DP+ и consumer уведомлений. Когда он тормозит или падает, у каждого сервиса свои логи без общего id, и чтобы найти медленное звено, приходится искать по нескольким сервисам по времени и гадать.
- **Что меняет:** один trace id идёт вместе с запросом в заголовках HTTP и Kafka, поэтому все span-ы складываются в одно дерево с таймингами, а тот же `traceId` в логах связывает любую строку лога со всем запросом.
- **Когда не нужно:** монолит или два сервиса, где correlation id в логах уже отвечает на вопрос «где». К тому же трассировка стоит коллектора, хранилища и настройки сэмплирования, которые небольшая система может не окупить.

## Основное

### Словарь
- **Span**: имя, время начала/конца, `traceId`, `spanId`, `parentSpanId`, статус, атрибуты (`http.route`, `db.statement`), события, ссылки (links). Span без родителя — корневой.
- **Распространение контекста** (context propagation): текущий span живёт в thread-local / `Context`; при переходе в другой поток, HTTP-вызове или записи в Kafka его нужно переносить явно (инструментация делает это для `RestClient`, `WebClient`, `KafkaTemplate`, `@KafkaListener`, executor-ов, обёрнутых в `ContextExecutorService`).
- **W3C Trace Context**: `traceparent: 00-<32 hex traceId>-<16 hex spanId>-<flags>`; флаги `01` = попал в выборку (sampled). `tracestate` несёт данные вендора. **Baggage** (`baggage: tenant=beeline,orderId=42`) — произвольные пары ключ/значение, которые распространяются вместе с трассой и могут копироваться в атрибуты span-ов и MDC; полезно для `subscriberId` по всему процессу eSIM. Baggage отправляется в каждый нижестоящий сервис, поэтому никогда не класть туда секреты или большие значения.
- Kafka: интерсептор producer-а пишет `traceparent` в заголовки записи; consumer создаёт дочерний span (или span, *связанный ссылкой* со span-ом producer-а, потому что потребление асинхронно и может произойти намного позже). В Spring Kafka 3.x есть поддержка observation (`spring.kafka.template.observation-enabled`, `...listener.observation-enabled`) — см. [[spring-kafka-consumer]].

### Агент и Micrometer Tracing
| | OpenTelemetry Java agent | Micrometer Tracing (Observation API Spring Boot 3) |
|---|---|---|
| Настройка | `-javaagent:opentelemetry-javaagent.jar`, переменные окружения `OTEL_*` | Зависимости `micrometer-tracing-bridge-otel` + `opentelemetry-exporter-otlp`, конфигурация в YAML |
| Покрытие | ~200 библиотек, инструментированных через байткод, включая JDBC, клиенты Kafka, gRPC, HTTP-клиенты, executor-ы | То, что Spring инструментирует через `Observation` (MVC, WebFlux, RestClient, Kafka, JDBC через `datasource-micrometer`) |
| Свои span-ы | `@WithSpan` из `opentelemetry-instrumentation-annotations` или OTel `Tracer` API | `@Observed`, бины `Tracer`/`ObservationRegistry`, `@NewSpan` |
| С точки зрения эксплуатации | Ops подключает его в Dockerfile/Helm, одинаково для всех JVM-приложений | Владеет приложение; метрики и трассы разделяют один `Observation`, поэтому один `@Observed` даёт таймер + span |
| Накладные расходы | Чуть дольше старт; в рантайме несколько процентов | Низкие |
- Не запускать оба: получатся дублирующиеся span-ы. Агент — обычный выбор в ландшафте на Kubernetes; Micrometer — когда команда приложения хочет контроля и единых метрик ([[otel-metric-types]]).
- Путь экспорта: приложение → **OTLP** (gRPC 4317 / HTTP 4318) → **OTel Collector** (пакетирование, tail-sampling, добавление атрибутов `k8s.*`, fan-out) → **Tempo** или **Jaeger** (хранение + UI через Grafana). Collector — то место, где можно сменить бэкенд, не трогая приложения.

### Сэмплирование
- **Head sampling**: решение принимается в корневом span-е, до того как что-либо известно. `parentbased_traceidratio` с долей 0.1 сохраняет 10 % трасс, а дочерние следуют флагу родителя, поэтому трассы никогда не сэмплируются наполовину. Дёшево, но отбрасывает нужные трассы с ошибками.
- **Tail sampling**: collector буферизует всю трассу и решает в конце: сохранять каждую трассу с ошибкой или задержкой > 2 с, 5 % остальных. Нужно, чтобы все span-ы трассы попадали на один экземпляр collector-а (балансирующий экспортёр по trace id), и нужна память.
- Разработка: `always_on`. Продакшен: head 10–20 % плюс tail-правила для ошибок.

### Логи, метрики, трассы
- **Метрики** говорят, *что* что-то не так (вырос p99 задержки); **трассы** — *где* (какой span); **логи** — *почему* (текст исключения). Корреляция: и Micrometer Tracing, и агент добавляют `traceId` и `spanId` в MDC SLF4J, поэтому JSON-строка лога их несёт, и Loki/Grafana могут связать лог ↔ трассу ([[logging-stacks]]). Паттерн консоли по умолчанию в Spring Boot 3.x уже печатает `[app,traceId,spanId]`.
- Exemplars: гистограммы Prometheus могут нести пример trace id на каждый bucket, поэтому всплеск задержки на дашборде ведёт прямо к медленной трассе.
- Sentry подхватывает тот же `traceId`, когда включена его интеграция с OpenTelemetry; так исключение в Sentry связывается с трассой в Tempo ([[sebep-incident-analysis]]).

## Пример
Micrometer Tracing с экспортом по OTLP в Spring Boot 3.x, baggage, передаваемый в MDC, и ручной span вокруг вызова SM-DP+.

```yaml
management:
  tracing:
    enabled: true
    sampling:
      probability: 0.2
    baggage:
      remote-fields: [orderId]
      correlation:
        fields: [orderId]      # также попадает в MDC
  otlp:
    tracing:
      endpoint: http://otel-collector:4318/v1/traces
  observations:
    key-values:
      service.version: ${APP_VERSION:dev}
spring:
  application:
    name: esim-provisioning
  kafka:
    template.observation-enabled: true
    listener.observation-enabled: true
logging:
  pattern:
    level: "%5p [${spring.application.name},%X{traceId:-},%X{spanId:-},%X{orderId:-}]"
```

```java
@Service
@RequiredArgsConstructor
public class ProfileDownloadService {
    private final Tracer tracer;            // io.micrometer.tracing.Tracer
    private final SmdpGateway smdp;

    public DownloadOrder order(String iccid, String orderId) {
        Span span = tracer.nextSpan().name("smdp.downloadOrder")
                .tag("esim.iccid", iccid)
                .tag("order.id", orderId)
                .start();
        try (Tracer.SpanInScope ws = tracer.withSpan(span)) {
            return smdp.downloadOrder(iccid, orderId).join();  // дочерние span-ы от RestClient прикрепляются здесь
        } catch (RuntimeException e) {
            span.error(e);
            throw e;
        } finally {
            span.end();
        }
    }
}
```

## Подводные камни
- Span, который начат и никогда не завершён (пропущен `finally`), утекает и никогда не появляется в Tempo; по возможности лучше `@Observed`/`@WithSpan` на методе.
- Контекст теряется в `CompletableFuture.supplyAsync`, `@Async`, `parallelStream` и executor-ах на виртуальных потоках, если executor не передаёт контекст (`ContextExecutorService.wrap`, `micrometer-context-propagation`).
- Теги с высокой кардинальностью (`subscriberId` на каждом span-е) нормальны для трасс, но смертельны, если тот же тег утекает в метрики через `@Observed` (взрыв кардинальности метрик).
- Родитель span-а Kafka consumer-а может быть многочасовой давности; дашборды, считающие «длительность трассы» от корня до последнего span-а, показывают абсурдные числа для асинхронных процессов. Нужно использовать ссылки (links) или отдельные трассы на consumer с общим ключом baggage.
- Spring Boot 3 убрал Spring Cloud Sleuth; конфигурации Sleuth (`spring.sleuth.*`) молча игнорируются.
- Связанные заметки: [[logging-stacks]], [[otel-metric-types]], [[spring-kafka-consumer]], [[kubernetes-basics]] (collector как DaemonSet/sidecar), [[system-design-basics]].
