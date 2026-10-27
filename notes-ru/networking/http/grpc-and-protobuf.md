---
title: gRPC и Protocol Buffers
tags: [networking, grpc, protobuf, http2, rpc]
learned: 2026-10-27
---
## Кратко
gRPC — бинарный RPC-фреймворк: сервисы и сообщения описываются в файле `.proto`, компилятор генерирует клиентские заглушки (stubs) и серверные каркасы для каждого языка, а вызовы передаются как байты Protobuf по потокам HTTP/2. По сравнению с JSON поверх REST он даёт меньший размер полезной нагрузки, типизированные контракты, четыре формы вызова (unary, server-streaming, client-streaming, bidirectional) и встроенные дедлайны — ценой неудобства для браузеров, более сложной отладки и L7-инфраструктуры, которая обязана понимать HTTP/2. Используй его для внутреннего взаимодействия сервисов и потоковых API; для публичных и браузерных оставь REST.

## Основное
### Protocol Buffers
- IDL с подходом «сначала схема» (`proto3`): сообщения с пронумерованными полями; в сеть уходит **номер поля**, а не имя, поэтому переименование бесплатно, а номер — навсегда.
- Кодирование: тег (номер поля + тип в сети), затем значение, целые числа сжаты как varint, нет имён полей, нет пробелов. Типичное сообщение в 3–10 раз меньше JSON и разбирается без рефлексии.
- У скаляров есть значения по умолчанию (`0`, `""`, `false`), и при значении по умолчанию они не сериализуются; «не задано» и «по умолчанию» неразличимы, если не использовать `optional` (proto3 начиная с 3.15) или типы-обёртки.
- Правила эволюции схемы: добавляй поля с новыми номерами (старые читатели их игнорируют, новые видят значения по умолчанию); никогда не переиспользуй и не перенумеровывай; помечай удалённые номера как `reserved`; не меняй тип поля. Та же история совместимости, что и у Avro в Kafka ([[message-schema-evolution]]).
- Protobuf можно использовать и без gRPC: полезная нагрузка Kafka со schema registry, файлы, значения в Redis.

### gRPC в сети
- Каждый вызов — это поток HTTP/2: `POST /package.Service/Method`, `content-type: application/grpc`, тело — сообщения Protobuf с префиксом длины; статус приходит в трейлерах (`grpc-status`, `grpc-message`), поэтому прокси на HTTP/1.1 не могут его передавать ([[http-1-1-vs-2-vs-3]]).
- Типы вызовов: unary (запрос → ответ), server streaming (один запрос, много ответов), client streaming, bidirectional. Потоковая передача напрямую ложится на управление потоком HTTP/2, поэтому обратное давление (backpressure) встроено ([[backpressure-and-flow-control]]).
- **Дедлайны** распространяются: клиент задаёт `deadline = now + 2 s`, каждый хоп видит оставшийся бюджет и сдаётся раньше, вместо того чтобы делать бесполезную работу; это самое большое эксплуатационное преимущество над разрозненными таймаутами REST ([[failure-modes-and-timeouts]]).
- Метаданные = заголовки (токены аутентификации, контекст трассировки) ([[distributed-tracing]]). Коды статуса у gRPC собственные (`OK`, `NOT_FOUND`, `UNAVAILABLE`, `DEADLINE_EXCEEDED`, `RESOURCE_EXHAUSTED`...) и на транспортном уровне отображаются в HTTP 200, поэтому дашборд на уровне HTTP показывает всё как успех.
- Канал (channel) = одно или несколько соединений HTTP/2 с балансировкой на стороне клиента (`round_robin` по разрешённым адресам) и переподключением с backoff. Интерцепторы с обеих сторон — для аутентификации, retry, метрик.

### gRPC и REST/JSON
| | gRPC | REST + JSON |
|---|---|---|
| Контракт | `.proto`, обеспечивается кодогенерацией | OpenAPI, необязателен ([[openapi-swagger]]) |
| Полезная нагрузка | бинарная, компактная | текстовая, читаемая |
| Транспорт | только HTTP/2 | любой HTTP |
| Потоковая передача | нативная, в обе стороны | SSE/WebSocket, прикрученные сбоку ([[websockets-sse-and-long-polling]]) |
| Браузер | нужен прокси grpc-web | нативно |
| Отладка | `grpcurl`, reflection, диссектор Wireshark | `curl`, любой лог |
| Кеширование | нет (POST, нет семантики HTTP) | HTTP-кеши ([[http-caching]]) |
| Балансировка нагрузки | по потокам нужен L7/mesh; L4 прибивает все вызовы к одному поду | по запросам где угодно ([[load-balancers-l4-vs-l7]]) |
| Подходит для | внутренние API, многоязычные команды, высокий QPS, потоковая передача, мобильные клиенты со строгим ограничением трафика | публичные API, браузеры, CRUD, всё, с чем партнёр интегрируется вручную |

### В экосистеме Java/Spring
- `grpc-java` с `protobuf-maven-plugin`; серверы работают на Netty. Интеграция со Spring через Spring gRPC (официальный с 2025 года, `spring-grpc-spring-boot-starter`) или более старый community-проект `grpc-spring-boot-starter`. Классы сервисов наследуют сгенерированный `*ImplBase`; `StreamObserver` — callback для ответов.
- Безопасность: TLS на канале, mTLS для идентичности сервиса ([[mtls-and-service-to-service-auth]]), bearer-токены в метаданных.
- Тестирование: in-process транспорт (`InProcessServerBuilder`) запускает клиент и сервер в одной JVM без сокетов.

## Пример
API статуса провижининга в том виде, как он выглядел бы в gRPC, с серверной стороной на Java. Номера полей — это контракт; `reserved` защищает удалённое поле.
```protobuf
syntax = "proto3";
package esim.v1;
option java_package = "kg.beeline.esim.v1";
option java_multiple_files = true;

service Provisioning {
  rpc GetProfile (GetProfileRequest) returns (Profile);
  rpc WatchProfile (GetProfileRequest) returns (stream ProfileEvent);   // server streaming
}

message GetProfileRequest { string iccid = 1; }

message Profile {
  string iccid = 1;
  ProfileState state = 2;
  reserved 3;                       // было 'legacy_eid', удалено в v1.4
  optional string eid = 4;          // optional: отличает «не задано» от ""
}

enum ProfileState { PROFILE_STATE_UNSPECIFIED = 0; RELEASED = 1; DOWNLOADED = 2; INSTALLED = 3; }
message ProfileEvent { Profile profile = 1; int64 occurred_at_ms = 2; }
```
```java
@GrpcService   // Spring gRPC / grpc-spring-boot-starter
public class ProvisioningGrpcService extends ProvisioningGrpc.ProvisioningImplBase {
    private final ProfileRepository profiles;

    public ProvisioningGrpcService(ProfileRepository profiles) { this.profiles = profiles; }

    @Override
    public void getProfile(GetProfileRequest req, StreamObserver<Profile> out) {
        profiles.findByIccid(req.getIccid()).ifPresentOrElse(
            p -> { out.onNext(toProto(p)); out.onCompleted(); },
            () -> out.onError(Status.NOT_FOUND.withDescription("iccid " + req.getIccid()).asRuntimeException()));
    }
}
```
```bash
# вызвать без написания клиента; на сервере должен быть включён reflection
$ grpcurl -plaintext -d '{"iccid":"8999600000000000001"}' esim.internal:9090 esim.v1.Provisioning/GetProfile
{ "iccid": "8999600000000000001", "state": "INSTALLED", "eid": "8903..." }
$ grpcurl -plaintext -d '{"iccid":"x"}' esim.internal:9090 esim.v1.Provisioning/GetProfile
ERROR: Code: NotFound  Message: iccid x
```

## Подводные камни
- Переиспользование номера поля после удаления поля: старые данные декодируются в чужое поле без всякой ошибки. Всегда используй `reserved`.
- Трактовка `0`/`""` как «присутствует»: скаляры proto3 не допускают null; используй `optional`, обёртки или явный enum `UNSPECIFIED = 0`.
- gRPC за L4-балансировщиком: одно долгоживущее соединение HTTP/2 отправляет все вызовы в один и тот же под; остальные простаивают. Используй L7-прокси (Envoy, nginx `grpc_pass`), сервис-меш или клиентский `round_robin` через DNS headless-сервиса ([[dns-resolution]]).
- Нет дедлайна на клиенте: по умолчанию он бесконечный, и зависший upstream держит поток или stream вечно.
- Большие сообщения: по умолчанию максимум 4 МБ; решение — потоковая передача или нарезка на части, а не повышение лимита до 1 ГБ.
- Связанные заметки: [[service-communication-sync-vs-async]] — когда RPC вообще не тот инструмент, [[rest-and-http]] — сторона REST в этом сравнении.
