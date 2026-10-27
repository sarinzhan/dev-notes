---
title: Эволюция схем сообщений — JSON, Avro, Protobuf и schema registry
tags: [messaging, kafka, schema, api-design]
learned: 2026-10-27
---
## Кратко
Событие в Kafka читают consumer-ы, которых ты не деплоишь, с offset-ов недельной давности, поэтому его схема — это API, которое должно оставаться **обратно совместимым** (backward compatible: новый consumer читает старые данные) и обычно **прямо совместимым** (forward compatible: старый consumer читает новые данные). Безопасные изменения: добавлять необязательные поля со значениями по умолчанию, никогда не переименовывать и не менять тип, никогда не переиспользовать поле, а для ломающих изменений версионировать топик (`...v2`) с периодом миграции. **Реестр схем** (schema registry: Confluent, Apicurio, Karapace) обеспечивает это в момент отправки для Avro, Protobuf и JSON Schema; обычный JSON держится на дисциплине и поле `version`.

## Основное

### Режимы совместимости
| Режим | Кто обновляется первым | Допустимые изменения |
|---|---|---|
| **Backward** | Consumer-ы | Удалять поля, добавлять поля *со значениями по умолчанию*; новый consumer читает старые сообщения (replay работает) |
| **Forward** | Producer-ы | Добавлять поля, удалять поля, *у которых было значение по умолчанию*; старый consumer игнорирует неизвестные новые поля |
| **Full** | Любой | Только добавлять/удалять необязательные поля со значениями по умолчанию |
| **None** | Оба одновременно | Что угодно; нужен согласованный deploy, который микросервисы сделать не могут |
- Kafka требует как минимум **backward**, потому что consumer-ы перечитывают историю; **full** (транзитивный) — безопасный вариант по умолчанию для топика с многими consumer-ами. Настройка реестра: `compatibility.level=FULL_TRANSITIVE` на subject.
- Новое *обязательное* поле в Avro обратно несовместимо (в старых сообщениях у него нет значения), поэтому для развивающихся событий «обязательных» полей фактически не бывает; всё новое — необязательное со значением по умолчанию.

### Форматы
| | JSON | JSON Schema | Avro | Protobuf |
|---|---|---|---|---|
| Размер | Большой (имена полей повторяются) | Как у JSON | Компактный, без имён полей | Компактный, теги |
| Нужна ли схема для чтения | Нет | Нет (только для валидации) | Да (схема писателя), поэтому реестр обязателен | Нет (теги есть в данных) |
| Правила эволюции | Соглашение | Проверяются | Строгие, проверяются реестром | Строгие по замыслу: никогда не переиспользовать номера тегов, `reserved` |
| Генерация кода | Необязательно (Jackson) | Необязательно | `avro-maven-plugin` | `protoc` |
| Типичное применение | Маленькие команды, мало consumer-ов, удобство отладки (`kafka-console-consumer` читаем) | JSON с ограничителями | Платформы данных на Kafka, Debezium, Connect | Команды на gRPC, много языков ([[grpc-and-protobuf]]) |
- Записи Avro несут 5-байтовый заголовок (`0x00` + id схемы), если отправлены через сериализатор реестра; consumer без реестра видит мусор.
- Spring: `KafkaAvroSerializer`/`KafkaProtobufSerializer` от Confluent или `JsonSerializer` со `spring.json.type.mapping`, чтобы не передавать имена Java-классов в заголовках ([[kafka-producer]]).

### Правила эволюции события
- Добавление полей: необязательные, со значением по умолчанию, которое означает «неизвестно» (`null`, пустой список), а не поддельное значение.
- Удаление полей: перестать писать, продолжать читать (consumer-ы могут делать replay), удалить из схемы релизом позже; в Protobuf `reserved 7;`, чтобы тег никогда не переиспользовался.
- Смена смысла или типа (`amount` из центов в десятичную строку): новое поле (`amountDecimal`) и пометка старого как устаревшего или новая мажорная версия.
- Ломающее изменение: новый топик `esim.sim.events.v2` (или новый subject), producer пишет в оба на время миграции, consumer-ы переходят по одному, старый топик выводится из эксплуатации. Заголовки вроде `type: ProfileActivated.v2` работают, когда топик несёт несколько типов событий ([[event-driven-architecture]]).
- Конверт (envelope): `eventId`, `type`, `version`, `occurredAt`, `aggregateId`, `payload`. Конверт стабилен, payload развивается.
- Consumer-ы должны игнорировать неизвестные поля: Jackson `@JsonIgnoreProperties(ignoreUnknown = true)` или `FAIL_ON_UNKNOWN_PROPERTIES=false`; иначе каждое добавление в producer-е их ломает ([[coupling-cohesion-and-general-principles]]).

### Как работает реестр схем
- Сериализатор producer-а регистрирует (или находит) схему под subject `<topic>-value`, получает id, пишет id + байты. Consumer читает id, получает схему писателя и сопоставляет её со своей схемой читателя (разрешение схем Avro: значения по умолчанию заполняют отсутствующие поля, лишние поля отбрасываются).
- Реестр отклоняет несовместимую регистрацию → падает producer при старте или первой отправке, а не consumer-ы в 3 часа ночи. Проверку стоит поставить в CI (`schema-registry-maven-plugin:test-compatibility`).
- Именование subject: `TopicNameStrategy` (одна схема на топик), `RecordNameStrategy` (несколько типов событий в топике), `TopicRecordNameStrategy` (и то, и другое).
- Также используется Debezium и Kafka Connect, а ещё relay в [[outbox-pattern]], когда payload в Avro.

### Контрактное тестирование
- Контракты, управляемые потребителем (consumer-driven contracts), для событий: consumer публикует, какие поля он читает, CI producer-а их проверяет ([[contract-testing-and-wiremock]]). Более дешёвая альтернатива: держать файлы схем в репозитории producer-а и на каждом PR запускать проверки совместимости против реестра.

## Пример
Схема Avro v2 с добавленным необязательным полем со значением по умолчанию, конфигурация producer-а Spring с реестром и consumer, который игнорирует неизвестные поля JSON для JSON-топиков.

```yaml
spring:
  kafka:
    producer:
      key-serializer: org.apache.kafka.common.serialization.StringSerializer
      value-serializer: io.confluent.kafka.serializers.KafkaAvroSerializer
      properties:
        schema.registry.url: http://schema-registry:8081
        auto.register.schemas: false          # регистрирует CI; в рантайме только поиск
        use.latest.version: true
        value.subject.name.strategy: io.confluent.kafka.serializers.subject.TopicRecordNameStrategy
    consumer:
      value-deserializer: io.confluent.kafka.serializers.KafkaAvroDeserializer
      properties:
        schema.registry.url: http://schema-registry:8081
        specific.avro.reader: true
```

```java
// ProfileActivated.avsc, версия 2: eid добавлено как необязательное со значением по умолчанию null (backward + forward совместимо)
// {
//   "type": "record", "name": "ProfileActivated", "namespace": "kg.beeline.esim.events",
//   "fields": [
//     {"name": "eventId",    "type": {"type": "string", "logicalType": "uuid"}},
//     {"name": "simId",      "type": {"type": "string", "logicalType": "uuid"}},
//     {"name": "iccid",      "type": "string"},
//     {"name": "occurredAt", "type": {"type": "long", "logicalType": "timestamp-millis"}},
//     {"name": "eid",        "type": ["null", "string"], "default": null, "doc": "added in v2"}
//   ]
// }

// JSON-топики: терпимость к добавлениям в producer-е
@JsonIgnoreProperties(ignoreUnknown = true)
public record ActivationRequestedV1(UUID id, UUID simId, String iccid, Instant occurredAt,
                                    @JsonProperty(defaultValue = "1") int version) {}
```

## Подводные камни
- Передача имён Java-классов в заголовках Kafka (`__TypeId__`) и десериализация по ним: переименование пакета в producer-е ломает всех consumer-ов. Типы нужно отображать явно.
- Смена типа поля, «потому что он был неправильный»: в старых сообщениях всё ещё старый тип; вместо этого нужно добавить новое поле.
- `auto.register.schemas=true` в продакшене: локальный запуск разработчика регистрирует сломанную схему под продакшен-subject.
- Переиспользование номера тега Protobuf или имени поля Avro с другим смыслом: молча неверные значения при replay.
- Связанные заметки: [[event-driven-architecture]], [[event-sourcing]] (схемы, которые живут вечно), [[kafka-producer]], [[domain-events-and-integration-events]], [[api-design-for-system-design]] (версионирование REST).
