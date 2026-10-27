---
title: Основы ActiveMQ — модель JMS, Classic и Artemis
tags: [activemq, jms, messaging, message-broker]
learned: 2026-10-22
---
## Кратко
Apache ActiveMQ — классический Java-брокер сообщений и та эталонная реализация, о которой обычно думают, когда говорят «JMS». Он существует в двух вариантах: **ActiveMQ Classic** (исходная линейка 5.x) и **ActiveMQ Artemis** (брокер, выросший из HornetQ и считающийся будущим «ActiveMQ 6»). API JMS/Jakarta Messaging даёт **очереди** (queues; точка-точка, одно сообщение — одному потребителю) и **топики** (topics; pub/sub, каждый подписчик получает копию) за вендор-нейтральным Java API. Его по-прежнему выбирают в Java-командах с существующим JMS-кодом, когда стандартный API важнее максимальной пропускной способности или когда нужен лёгкий встроенный брокер.

## Основное

### Модель JMS / Jakarta Messaging
- **JMS** (Java Message Service, теперь **Jakarta Messaging 3.x** в пакете `jakarta.jms` начиная с Jakarta EE 9) — это **API**, а не сетевой протокол. Каждый брокер поставляет свой клиент; ActiveMQ дополнительно поддерживает открытые протоколы (OpenWire, AMQP 1.0, MQTT, STOMP, а в Artemis — core-протокол HornetQ), чтобы могли подключаться и не-Java клиенты.
- Основные объекты: `ConnectionFactory` → `JMSContext` (упрощённый API JMS 2.0) или `Connection`/`Session` → `JMSProducer` / `JMSConsumer` на `Destination`, которым является `Queue` или `Topic`. `JmsTemplate` и `@JmsListener` в Spring оборачивают ровно это, а `DefaultJmsListenerContainerFactory` играет ту же роль, что `ConcurrentKafkaListenerContainerFactory` в [[spring-kafka-consumer]].
- Типы сообщений: `TextMessage`, `BytesMessage`, `MapMessage`, `ObjectMessage` (избегать: Java-сериализация), `StreamMessage`. Заголовки (`JMSMessageID`, `JMSCorrelationID`, `JMSReplyTo`, `JMSExpiration`, `JMSPriority` 0–9, `JMSDeliveryMode` PERSISTENT/NON_PERSISTENT) плюс произвольные свойства.
- **Селекторы сообщений** (message selectors): потребитель подписывается с фильтром в стиле SQL-92 по заголовкам/свойствам (`region = 'KG' AND priority > 5`), который вычисляется на брокере. Это замена JMS для маршрутизации через exchange в RabbitMQ ([[rabbitmq-basics]]).

### Очереди и топики
| | Очередь (точка-точка) | Топик (публикация/подписка) |
|---|---|---|
| Доставка | Каждое сообщение — **одному** потребителю; несколько потребителей конкурируют | Каждое сообщение — **каждому** активному подписчику |
| Потребитель офлайн | Сообщение ждёт в очереди | Теряется, если подписка не **durable** (переживает отключение) или не **shared durable** (JMS 2.0: несколько потребителей делят одну подписку — аналог consumer group в Kafka) |
| Балансировка нагрузки | Естественная: добавь потребителей | Только через shared-подписки |
| Типичное применение | Команды, распределение работы, запрос/ответ (`JMSReplyTo` + временная очередь) | Рассылка событий нескольким сервисам |
| Отображение в Artemis | Адрес с **anycast**-очередью | Адрес с **multicast**-очередями, по одной на подписку |

- **Виртуальные топики** (virtual topics, Classic) / anycast+multicast-адреса (Artemis) дают схему «опубликовал один раз — у каждого сервиса своя очередь с конкурирующими потребителями». В RabbitMQ то же самое собирают из fanout exchange и очередей на каждый сервис.

### Подтверждения и транзакции
- `AUTO_ACKNOWLEDGE` (ack, когда `receive()`/`onMessage` возвращает управление — at-least-once при падении посреди обработки, так как сообщение доставляется повторно), `CLIENT_ACKNOWLEDGE` (явный `message.acknowledge()`, подтверждает все сообщения, полученные к этому моменту в сессии), `DUPS_OK_ACKNOWLEDGE` (ленивые ack, возможны дубликаты, быстрее), `SESSION_TRANSACTED` (ack через `session.commit()`).
- **Локальные транзакции** группируют отправки и ack в одной сессии. **XA / JTA** позволяет JMS-сессии участвовать в распределённой транзакции вместе с базой данных — единственное, что JMS-брокеры умеют, а Kafka нет. На практике XA медленный и хрупкий; [[outbox-pattern]] плюс [[idempotency|идемпотентные]] потребители — современная альтернатива даже с ActiveMQ.
- Политика повторной доставки: `maximumRedeliveries` и backoff на клиенте (Classic) или в настройках адреса (Artemis); исчерпавшие попытки сообщения уходят в **DLQ** (`ActiveMQ.DLQ` по умолчанию в Classic, `DLQ`/`ExpiryQueue` на каждый адрес в Artemis).
- Управление потоком (flow control): **prefetch** (в Classic по умолчанию 1000 на потребителя очереди, что копит сообщения, как неограниченный prefetch в RabbitMQ — для медленных потребителей его стоит уменьшить) и **producer flow control** / paging, когда destination достигает лимита памяти.

### ActiveMQ Classic и Artemis
| | ActiveMQ Classic (5.x / 6.x) | ActiveMQ Artemis (2.x) |
|---|---|---|
| Происхождение | Исходный ActiveMQ, 2004 | HornetQ (JBoss), передан в 2015 |
| Хранение | KahaDB или JDBC-хранилище | Журнал только на дозапись (append-only) с libaio/NIO; быстр на persistent-сообщениях |
| Потоки | Поток на соединение, блокирующий ввод-вывод | Netty, неблокирующий, намного выше пропускная способность |
| HA | Master/slave на общем хранилище или JDBC-аренде; сеть брокеров для горизонтального масштабирования | Пары live/backup с общим хранилищем или репликацией; кластеризация с перераспределением сообщений |
| Протоколы | OpenWire (родной), AMQP 1.0, MQTT, STOMP | Core, AMQP 1.0, OpenWire, MQTT, STOMP, HornetQ |
| Модель адресов | Destinations с wildcards и виртуальными топиками | Адреса + типы маршрутизации (anycast / multicast) + очереди |
| Статус | Поддерживается; 6.x требует Java 17 и Jakarta Messaging 3.1 | Назначенный преемник; встроенный брокер по умолчанию в Spring Boot (`spring-boot-starter-artemis`), WildFly и Red Hat AMQ 7 |

- Новые проекты выбирают **Artemis**; Classic остаётся там, где уже работает связка OpenWire/JDBC-хранилище. Оба сильно отстают от Kafka по пропускной способности и горизонтальному масштабированию, а от RabbitMQ — по широте протоколов и инструментам эксплуатации.

### Когда ActiveMQ всё ещё правильный выбор
- Существующие приложения на Java EE / Spring уже написаны под JMS; смена брокера — это изменение конфигурации, переход на Kafka — перепроектирование.
- Нужны возможности JMS «из коробки»: селекторы сообщений, приоритет и TTL на уровне сообщения, запрос/ответ через временные очереди, XA с базой данных.
- Встроенный брокер для тестов или одноузлового развёртывания (Artemis in-VM в Spring Boot).
- Модель Kafka не подходит: мало сообщений с «тяжёлой» семантикой на каждое, строгая доставка ровно одному обработчику без управления offset на стороне потребителя, много короткоживущих destinations.
- Не подходит для потоковой обработки событий (event streaming), повторного чтения (replay), раздачи одних и тех же данных многим командам или стабильно очень большого объёма — это [[kafka-architecture|Kafka]]; для гибкой маршрутизации с множеством протоколов и сильными инструментами эксплуатации — [[rabbitmq-basics|RabbitMQ]].

## Пример
Spring Boot 3 со встроенным Artemis: потребитель очереди с локальными транзакциями и DLQ — JMS-аналог Kafka-листенера из [[spring-kafka-consumer]].

```yaml
spring:
  artemis:
    mode: embedded            # или mode: native + broker-url: tcp://mq:61616
    embedded:
      queues: esim.activation
  jms:
    listener:
      session:
        transacted: true      # повторная доставка при исключении, DLQ после максимума попыток
      concurrency: 3-10
```

```java
@Component
public class ActivationJmsListener {

    @JmsListener(destination = "esim.activation", selector = "region = 'KG'")
    public void onActivation(ActivationRequested evt,
                             @Header(JmsHeaders.REDELIVERED) boolean redelivered) {
        if (redelivered && processed.exists(evt.id())) return;   // защита идемпотентности
        provisioning.activate(evt);
    }
}

// сторона продюсера
jmsTemplate.convertAndSend("esim.activation", evt, m -> {
    m.setStringProperty("region", evt.region());
    m.setJMSCorrelationID(evt.id());
    return m;
});
```

## Подводные камни
- Обычный (не durable) подписчик топика, находящийся офлайн, молча теряет сообщения. Используй shared durable-подписки или отображай топики на очереди для каждого сервиса.
- `ObjectMessage` полагается на Java-сериализацию: дыра в безопасности и кошмар версионирования; оба брокера требуют белый список разрешённых пакетов для него. Отправляй JSON в `TextMessage`/`BytesMessage`.
- Prefetch 1000 по умолчанию в Classic означает, что один медленный потребитель держит тысячу сообщений, которые могли бы обработать другие; для долгой работы ставь его низким.
- Транзакционная сессия с исключением в листенере доставляет сообщение повторно сразу и бесконечно, если не настроены `maximumRedeliveries`/DLQ — ядовитое сообщение (poison message) зацикливает потребителя.
- `javax.jms` против `jakarta.jms`: Spring Boot 3 и ActiveMQ Classic 6 / Artemis 2.2x+ работают только с Jakarta; смешивание со старыми клиентскими jar даёт `ClassNotFoundException` при старте.
- Связанные заметки: [[rabbitmq-basics]], [[kafka-architecture]], [[kafka-consumer]], [[outbox-pattern]], [[idempotency]].
