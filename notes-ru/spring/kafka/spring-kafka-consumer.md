---
title: Основы consumer'а в Spring Kafka
tags: [kafka, messaging, spring-boot, reliability]
learned: 2026-10-08
---
## Кратко
`@KafkaListener` работает в контейнере с N потоками-consumer'ами (`concurrency`), каждый из которых владеет частью партиций группы. Offset'ы коммитит контейнер после обработки записи (или пакета), поэтому падение посреди обработки означает доставку **at-least-once**, и обработчик должен быть [[idempotency|идемпотентным]]. Обработка ошибок настраивается один раз в `DefaultErrorHandler` (retry с задержкой (backoff), затем dead letter), а не через try/catch внутри слушателя.

## Основное

### Слушатель, параллелизм, партиции
- Один `@KafkaListener` создаёт `ConcurrentMessageListenerContainer` с `concurrency` дочерними контейнерами, каждый из которых — `KafkaConsumer` в своём потоке. Партицией владеет ровно один consumer в группе, поэтому при `concurrency` больше числа партиций часть потоков простаивает; партиции задают потолок параллелизма ([[kafka-architecture]]).
- Порядок гарантируется только внутри партиции. Задавай ключ сообщений по id абонента/агрегата, чтобы связанные события оставались упорядоченными ([[kafka-producer]]).
- Одинаковый `groupId` → работа делится; разный `groupId` → каждая группа получает каждую запись.

```anim
kafka-consumer-group
```

### Режимы подтверждения
| `AckMode` | Когда коммит |
|---|---|
| `BATCH` (по умолчанию) | после обработки всех записей одного `poll()` |
| `RECORD` | после каждой записи |
| `TIME` / `COUNT` / `COUNT_TIME` | периодически |
| `MANUAL` | вызван `ack.acknowledge()`, коммит в конце цикла poll |
| `MANUAL_IMMEDIATE` | вызван `ack.acknowledge()`, коммит сразу |
- `MANUAL_IMMEDIATE` — когда коммитить нужно только после успешной записи в БД. Даже тогда ситуация «БД закоммитилась, commit offset'а упал» приводит к повторной доставке записи: именно поэтому дедупликация живёт в БД ([[idempotency]]), а не в режиме подтверждения.
- Spring выставляет `enable.auto.commit` в `false`; коммитами владеет контейнер.

### Rebalance и цикл poll
- `max.poll.interval.ms` (5 мин) — допустимое время между двумя вызовами `poll()`; `max.poll.records` (500) — размер пакета за один poll. Если обработка 500 записей занимает больше 5 минут, брокер исключает consumer'а, происходит rebalance, и другой consumer заново обрабатывает тот же пакет, что тоже занимает слишком много времени: **шторм rebalance'ов (rebalance storm)** с дубликатами и нулевым прогрессом.
- Решение: уменьшить `max.poll.records`, увеличить `max.poll.interval.ms` или ускорить обработку (пакетные записи в БД, асинхронный ввод-вывод). Следи за `kafka.consumer.fetch.manager.records.lag` и количеством rebalance'ов.
- Кооперативный sticky-назначатель (`CooperativeStickyAssignor`) избегает отзыва партиций с остановкой всего мира (stop-the-world); `group.instance.id` (статическое членство) предотвращает rebalance при быстром перезапуске.

### Обработка ошибок
- `DefaultErrorHandler(recoverer, backOff)` (Spring Kafka 2.8+): при исключении возвращается (seek) к записи и повторяет её согласно `BackOff` (`FixedBackOff`, `ExponentialBackOffWithMaxRetries`), затем вызывает recoverer. Retry блокируют партицию: используй короткие задержки или неблокирующий `@RetryableTopic` для длинных.
- `DeadLetterPublishingRecoverer` публикует упавшую запись в `<topic>.DLT` с заголовками об исключении; настрой алерт на lag DLT и сделай инструмент для повторной отправки (replay).
- `addNotRetryableExceptions(DeserializationException.class, ValidationException.class, ...)` отключает retry для ошибок, которые никогда не пройдут; `DefaultErrorHandler` уже считает фатальными `DeserializationException`, `MessageConversionException`, `MethodArgumentResolutionException`, `ClassCastException`.
- Ошибки десериализации происходят в `poll()`, до слушателя. Без `ErrorHandlingDeserializer` consumer бесконечно крутится на «отравленной» записи (poison record). С ним запись доходит до обработчика ошибок с исключением в заголовке и уходит в DLT.
- Пакетные слушатели (`@KafkaListener(batch = "true")`, `List<ConsumerRecord<K,V>>`): обработчик ошибок повторно доставляет весь пакет, если не бросить `BatchListenerFailedException(msg, index)`, указав, какая запись упала.

### Транзакции
- `@Transactional` на методе слушателя делает запись в БД атомарной, но commit offset'а — отдельная система: по-прежнему at-least-once ([[spring-transactional]]).
- Транзакции Kafka (`transaction-id-prefix`, `KafkaTransactionManager`) дают exactly-once только для цепочки consume → produce **внутри Kafka**; запись в БД внутри не покрывается. `ChainedKafkaTransactionManager` устарел; для БД + Kafka используй [[outbox-pattern]].

### Тестирование
- `@EmbeddedKafka` запускает брокер внутри JVM: быстро, подходит для логики слушателя. `KafkaContainer` (или `ConfluentKafkaContainer`) из [[testcontainers]] запускает настоящий образ брокера и ловит проблемы сериализации и конфигурации; для интеграционных тестов он предпочтительнее. Используй `@DynamicPropertySource`, чтобы подставить `bootstrap-servers`.

## Пример
```java
@Configuration
public class KafkaConsumerConfig {

    @Bean
    public DefaultErrorHandler errorHandler(KafkaTemplate<Object, Object> template) {
        var recoverer = new DeadLetterPublishingRecoverer(template,
            (rec, ex) -> new TopicPartition(rec.topic() + ".DLT", rec.partition()));
        var backOff = new ExponentialBackOffWithMaxRetries(3);
        backOff.setInitialInterval(500);
        backOff.setMultiplier(2.0);
        var handler = new DefaultErrorHandler(recoverer, backOff);
        handler.addNotRetryableExceptions(ActivationValidationException.class);
        return handler;
    }

    @Bean
    public ConcurrentKafkaListenerContainerFactory<String, ActivationRequested> factory(
            ConsumerFactory<String, ActivationRequested> cf, DefaultErrorHandler handler) {
        var f = new ConcurrentKafkaListenerContainerFactory<String, ActivationRequested>();
        f.setConsumerFactory(cf);
        f.setCommonErrorHandler(handler);
        f.getContainerProperties().setAckMode(ContainerProperties.AckMode.MANUAL_IMMEDIATE);
        return f;
    }
}

@Component
@RequiredArgsConstructor
public class ActivationListener {
    private final ProvisioningService provisioning;

    @KafkaListener(topics = "esim.activation", groupId = "provisioning", concurrency = "3")
    public void onActivation(ActivationRequested evt, Acknowledgment ack) {
        provisioning.activate(evt);   // @Transactional: вставка в processed_events + бизнес-запись
        ack.acknowledge();
    }
}
```
```yaml
spring:
  kafka:
    consumer:
      key-deserializer: org.springframework.kafka.support.serializer.ErrorHandlingDeserializer
      value-deserializer: org.springframework.kafka.support.serializer.ErrorHandlingDeserializer
      properties:
        spring.deserializer.value.delegate.class: org.springframework.kafka.support.serializer.JsonDeserializer
        spring.json.trusted.packages: kg.beeline.esim.events
        max.poll.records: 100
```

## Подводные камни
- Слушатель с аннотацией [[spring-transactional|@Transactional]] **не** делает commit в Kafka транзакционным вместе с БД. БД коммитится, затем commit offset'а может упасть: дубликат.
- Перехват всех исключений внутри слушателя скрывает сбои от `DefaultErrorHandler`: ни retry, ни DLT, молчаливая потеря данных.
- Блокирующие retry с длинной задержкой на партиции останавливают все записи за упавшей; для семантики «повторить позже» используй `@RetryableTopic`.
- Не задан `spring.json.trusted.packages`: `JsonDeserializer` отвергает заголовок типа, и каждая запись становится ошибкой десериализации.
- Связанные заметки: [[kafka-consumer]] — про «голый» клиент, [[distributed-tracing]] — про проброс trace id через заголовки, [[esim-provisioning-platform]] — про реальную схему топиков.
