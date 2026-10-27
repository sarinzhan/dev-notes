---
title: Message schema evolution — JSON, Avro, Protobuf and the schema registry
tags: [messaging, kafka, schema, api-design]
learned: 2026-10-27
---
## TL;DR
An event on Kafka is read by consumers you do not deploy, from offsets days old, so its schema is an API that must stay **backward compatible** (new consumer reads old data) and usually **forward compatible** (old consumer reads new data). The safe moves are: add optional fields with defaults, never rename or change a type, never reuse a field, and version the topic (`...v2`) for breaking changes with a migration period. A **schema registry** (Confluent, Apicurio, Karapace) enforces this at produce time for Avro, Protobuf and JSON Schema; plain JSON relies on discipline and a `version` field.

## Key points

### Compatibility modes
| Mode | Who upgrades first | Allowed changes |
|---|---|---|
| **Backward** | Consumers | Delete fields, add fields *with defaults*; new consumer can read old messages (replay works) |
| **Forward** | Producers | Add fields, delete fields *that had defaults*; old consumer ignores unknown new fields |
| **Full** | Either | Only add/remove optional fields with defaults |
| **None** | Both at once | Anything; needs a coordinated deploy, which microservices cannot do |
- Kafka's default need is **backward** at minimum because consumers replay history; **full** (transitive) is the safe default for a topic with many consumers. Registry setting: `compatibility.level=FULL_TRANSITIVE` per subject.
- A new *required* field is backward-incompatible under Avro (old messages have no value), so "required" effectively does not exist for evolving events; everything new is optional with a default.

### Formats
| | JSON | JSON Schema | Avro | Protobuf |
|---|---|---|---|---|
| Size | Large (field names repeated) | Same as JSON | Compact, no field names | Compact, tags |
| Schema needed to read | No | No (validation only) | Yes (writer schema), so registry is mandatory | No (tags are in the data) |
| Evolution rules | Convention | Validated | Strict, registry-checked | Strict by design: never reuse tag numbers, `reserved` |
| Code generation | Optional (Jackson) | Optional | `avro-maven-plugin` | `protoc` |
| Typical use | Small teams, few consumers, debuggability (`kafka-console-consumer` readable) | JSON with guard rails | Kafka-heavy data platforms, Debezium, Connect | gRPC shops, polyglot ([[grpc-and-protobuf]]) |
- Avro records carry a 5-byte header (`0x00` + schema id) when produced through a registry serializer; a consumer without the registry sees garbage.
- Spring: `KafkaAvroSerializer`/`KafkaProtobufSerializer` from Confluent, or `JsonSerializer` with `spring.json.type.mapping` to avoid shipping Java class names in headers ([[kafka-producer]]).

### Rules for evolving an event
- Add fields: optional, with a default that means "unknown" (`null`, empty list), not a fake value.
- Remove fields: stop writing, keep reading (consumers may replay), delete from the schema one release later; Protobuf `reserved 7;` so the tag is never reused.
- Change meaning or type (`amount` from cents to decimal string): new field (`amountDecimal`) and deprecate the old, or a new major version.
- Breaking change: new topic `esim.sim.events.v2` (or new subject), producer dual-writes for a migration window, consumers move one by one, old topic retired. Headers like `type: ProfileActivated.v2` work when the topic carries several event types ([[event-driven-architecture]]).
- Envelope: `eventId`, `type`, `version`, `occurredAt`, `aggregateId`, `payload`. Stable envelope, evolving payload.
- Consumers must ignore unknown fields: Jackson `@JsonIgnoreProperties(ignoreUnknown = true)` or `FAIL_ON_UNKNOWN_PROPERTIES=false`; otherwise every producer addition breaks them ([[coupling-cohesion-and-general-principles]]).

### Schema registry mechanics
- Producer serializer registers (or looks up) the schema under subject `<topic>-value`, gets an id, writes id + bytes. Consumer reads the id, fetches the writer schema, resolves it against its own reader schema (Avro schema resolution: defaults fill missing fields, extra fields are dropped).
- Registry rejects an incompatible registration → the producer fails at startup or first send, not the consumers at 3 a.m. Put the check in CI (`schema-registry-maven-plugin:test-compatibility`).
- Subject naming: `TopicNameStrategy` (one schema per topic), `RecordNameStrategy` (several event types per topic), `TopicRecordNameStrategy` (both).
- Also used by Debezium and Kafka Connect, and by the [[outbox-pattern]] relay when payloads are Avro.

### Contract testing
- Consumer-driven contracts for events: the consumer publishes which fields it reads, the producer's CI verifies them ([[contract-testing-and-wiremock]]). Cheaper alternative: keep the schema files in the producer repo and run compatibility checks against the registry on every PR.

## Example
Avro schema v2 adding an optional field with a default, Spring producer config with the registry, and a consumer that ignores unknown JSON fields for the JSON topics.

```yaml
spring:
  kafka:
    producer:
      key-serializer: org.apache.kafka.common.serialization.StringSerializer
      value-serializer: io.confluent.kafka.serializers.KafkaAvroSerializer
      properties:
        schema.registry.url: http://schema-registry:8081
        auto.register.schemas: false          # CI registers; runtime only looks up
        use.latest.version: true
        value.subject.name.strategy: io.confluent.kafka.serializers.subject.TopicRecordNameStrategy
    consumer:
      value-deserializer: io.confluent.kafka.serializers.KafkaAvroDeserializer
      properties:
        schema.registry.url: http://schema-registry:8081
        specific.avro.reader: true
```

```java
// ProfileActivated.avsc, version 2: eid added as optional with a null default (backward + forward compatible)
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

// JSON topics: tolerate producer additions
@JsonIgnoreProperties(ignoreUnknown = true)
public record ActivationRequestedV1(UUID id, UUID simId, String iccid, Instant occurredAt,
                                    @JsonProperty(defaultValue = "1") int version) {}
```

## Pitfalls
- Shipping Java class names in Kafka headers (`__TypeId__`) and deserialising by them: a package rename in the producer breaks every consumer. Map types explicitly.
- Changing a field's type "because it was wrong": old messages still have the old type; add a new field instead.
- `auto.register.schemas=true` in production: a developer's local run registers a broken schema under the production subject.
- Reusing a Protobuf tag number or an Avro field name for a different meaning: silently wrong values on replay.
- Related: [[event-driven-architecture]], [[event-sourcing]] (schemas that live forever), [[kafka-producer]], [[domain-events-and-integration-events]], [[api-design-for-system-design]] (REST versioning).
