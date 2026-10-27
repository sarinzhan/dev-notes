---
title: Kafka consumer — группы, ребалансировка, offsets, lag
tags: [kafka, messaging, consumer-groups, delivery-semantics]
learned: 2026-10-22
---
## Кратко
Консьюмер сам **забирает** (pull) пакеты записей у лидеров партиций по offset, десериализует и обрабатывает их, а затем делает **commit** offset, чтобы после перезапуска продолжить с того же места. Консьюмеры с одинаковым `group.id` образуют **consumer group**: координатор группы на стороне брокера назначает каждую партицию ровно одному участнику, поэтому параллелизм ограничен числом партиций. Любое изменение состава группы или партиций запускает **rebalance**. Момент commit относительно обработки определяет выбор между at-least-once и at-most-once; **lag** (log end offset минус закоммиченный offset) — первая метрика, за которой стоит следить.

## Основное

### Цикл poll
- `KafkaConsumer` однопоточный и не потокобезопасный. Цикл: `subscribe(topics)` → `poll(timeout)` → обработка записей → commit. `poll()` также отправляет heartbeat, вступает в группу и получает метаданные от брокеров (а не от ZooKeeper, как пишут в старых материалах).
- Настройка выборки: `max.poll.records` (500), `fetch.min.bytes` / `fetch.max.wait.ms` (пакетирование на стороне брокера), `max.partition.fetch.bytes`. Консьюмер заранее подгружает следующий пакет, пока ты обрабатываешь текущий.
- Записи видны только до **high watermark** (закоммичены всеми репликами ISR), а с `isolation.level=read_committed` — только закоммиченные транзакционные записи ([[kafka-producer]]).
- Чтение основано на pull: медленный консьюмер просто отстаёт; брокер никогда его не захлёстывает, в отличие от брокеров на основе push ([[rabbitmq-basics]]).

### Consumer group и назначение партиций
- `group.id` объединяет консьюмеров, которые делят работу над топиком. Каждая партиция принадлежит **ровно одному** консьюмеру в группе; один консьюмер может владеть многими партициями из нескольких топиков. Консьюмеров больше, чем партиций → лишние простаивают, поэтому выбирай число партиций с запасом на масштабирование ([[kafka-architecture]]).
- Разные группы независимы: группа `billing` и группа `analytics` обе читают каждую запись топика со своими offsets — это pub/sub поверх очереди.
- Протокол членства (классический): один брокер является **координатором группы** (group coordinator; выбирается хешированием `group.id` на `__consumer_offsets`). При вступлении координатор выбирает одного участника **лидером группы**, который выполняет `partition.assignment.strategy` и отправляет назначение обратно через координатора. Стратегии: `RangeAssignor` (по умолчанию, ко-партиционирует топики, может быть неравномерной), `RoundRobinAssignor`, `StickyAssignor` (минимизирует перемещения), `CooperativeStickyAssignor` (sticky + инкрементальная).
- **KIP-848, новый протокол consumer group** (preview в 3.7, GA в **4.0**, `group.protocol=consumer`): назначение полностью переходит к координатору, участники получают инкрементальные назначения через heartbeat, и глобального барьера синхронизации больше нет. Гораздо более быстрые rebalance для больших групп.
- Почему группа лучше одиночного консьюмера с `assign()`: offsets хранятся на стороне брокера для каждой группы, членство и переключение при сбое автоматические, а масштабироваться можно, просто запустив ещё один экземпляр.

### Heartbeat, живость и ребалансировка
- У каждого консьюмера есть фоновый поток heartbeat (`heartbeat.interval.ms` 3 с). Если координатор ничего не слышит в течение `session.timeout.ms` (45 с начиная с 3.0), участник объявляется мёртвым и удаляется.
- Отдельно: если приложение не вызывает `poll()` в течение `max.poll.interval.ms` (5 мин) — обработка слишком медленная или зависла, — консьюмер **сам покидает группу**, и его партиции переназначаются. Тогда обработка параллельно продолжается где-то ещё: дубликаты.
- **Rebalance** происходит при любом изменении: участник вступает или уходит (deploy, падение, масштабирование), у топика из подписки появляются новые партиции, подписка по регулярному выражению находит новый топик. Во время **eager (stop-the-world)** rebalance каждый участник отказывается от всех партиций, никто не потребляет, пока новое назначение не стабилизируется, и незакоммиченная работа выполняется заново.
- Смягчение: `CooperativeStickyAssignor` (2.4+, инкрементальная кооперативная ребалансировка: отзываются только перемещаемые партиции), **статическое членство** (static membership; `group.instance.id`, 2.3+: перезапущенный под с тем же id получает свои старые партиции без rebalance, если возвращается в пределах `session.timeout.ms`) и протокол KIP-848 в 4.0.
- Rebalance — обычная причина ситуации «задеплоили и получили дубликаты»: делай commit в `onPartitionsRevoked` у `ConsumerRebalanceListener` или поручи это фреймворку ([[spring-kafka-consumer]]).

```anim
kafka-consumer-group
```

### Offsets и commit
- Закоммиченный offset — это **следующий offset для чтения**; он хранится во внутреннем компактируемом топике `__consumer_offsets` с ключом (группа, топик, партиция). Не делать commit допустимо: следующий консьюмер партиции перечитает те же записи.
- `enable.auto.commit=true` (по умолчанию, каждые `auto.commit.interval.ms` = 5 с) коммитит то, что `poll()` вернул в прошлый раз, при следующем `poll()`. Просто, но падение между commit и окончанием обработки теряет записи, а падение до commit их дублирует.
- Вручную: `commitSync()` (блокирует, делает retry, безопасен в shutdown hook) и `commitAsync()` (отправил и забыл, с колбэком; не повторяй асинхронный commit вручную — более поздний commit мог уже записать больший offset). Конкретные offsets коммить через `commitSync(Map<TopicPartition, OffsetAndMetadata>)` после записи в БД.
- `auto.offset.reset` применяется, только когда у группы **нет** закоммиченного offset для партиции (новая группа или offset истёк после `offsets.retention.minutes`, по умолчанию 7 дней): `latest` (по умолчанию) пропускает всё, что уже есть в топике, `earliest` перечитывает с начала, `none` бросает исключение.
- Повторное чтение: `seek()` / `seekToBeginning()` у консьюмера или `kafka-consumer-groups.sh --reset-offsets --to-datetime ...` при остановленной группе.

### Семантика доставки на стороне консьюмера
| Шаблон | Когда commit | Сбой между обработкой и commit | Результат |
|---|---|---|---|
| **At-most-once** | до обработки (или auto-commit при быстрой обработке) | запись пропускается | потеря, без дубликатов |
| **At-least-once** | после обработки | запись перечитывается | дубликаты, без потерь — вариант по умолчанию, под который стоит проектировать |
| **Exactly-once** внутри Kafka | offsets отправляются в транзакции продюсера (`sendOffsetsToTransaction`) | откатывается и то, и другое | без потерь/дубликатов для consume→produce |
| «Exactly-once» с БД | offset сохраняется в той же транзакции БД, что и результат, при старте `seek()` на него | откатывается и то, и другое | фактически один раз, сама Kafka по-прежнему at-least-once |

- At-least-once плюс **идемпотентный обработчик** — практичный ответ для большинства сервисов; см. [[idempotency]] и сторону консьюмера в [[outbox-pattern]].

### Lag
- **Consumer lag** = log end offset − закоммиченный offset, для каждой партиции. Это расстояние между тем, что записали продюсеры, и тем, что обработала группа. Стабильно растущий lag означает, что консьюмеры не успевают (масштабируйся до числа партиций, ускорь обработку или обрабатывай пакетами); скачки lag при deploy означают rebalance.
- Следить за ним: `kafka-consumer-groups.sh --describe --group <g>`, клиентская метрика `records-lag-max`, Burrow или Kafka exporter в Prometheus/Grafana.

## Пример
At-least-once с ручным commit после бизнес-записи; commit по каждой партиции отдельно, чтобы сбой в одной партиции не блокировал остальные.

```java
Properties p = new Properties();
p.put(BOOTSTRAP_SERVERS_CONFIG, "kafka-1:9092");
p.put(GROUP_ID_CONFIG, "provisioning");
p.put(GROUP_INSTANCE_ID_CONFIG, System.getenv("POD_NAME"));        // статическое членство
p.put(PARTITION_ASSIGNMENT_STRATEGY_CONFIG, CooperativeStickyAssignor.class.getName());
p.put(ENABLE_AUTO_COMMIT_CONFIG, false);
p.put(AUTO_OFFSET_RESET_CONFIG, "earliest");
p.put(MAX_POLL_RECORDS_CONFIG, 200);
p.put(KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
p.put(VALUE_DESERIALIZER_CLASS_CONFIG, KafkaJsonDeserializer.class);

try (KafkaConsumer<String, ActivationRequested> consumer = new KafkaConsumer<>(p)) {
    consumer.subscribe(List.of("esim.activation"));
    while (running) {
        ConsumerRecords<String, ActivationRequested> records = consumer.poll(Duration.ofMillis(500));
        for (TopicPartition tp : records.partitions()) {
            long last = -1;
            for (ConsumerRecord<String, ActivationRequested> r : records.records(tp)) {
                provisioning.activateIdempotently(r.value());   // безопасно повторять
                last = r.offset();
            }
            if (last >= 0) {
                consumer.commitSync(Map.of(tp, new OffsetAndMetadata(last + 1))); // следующий offset для чтения
            }
        }
    }
}
```

## Подводные камни
- Commit offset `N` вместо `N + 1`: закоммиченное значение — это *следующая* запись для чтения, поэтому последняя обработанная запись доставляется повторно после каждого перезапуска.
- Обработка одного пакета `poll()` дольше `max.poll.interval.ms` (по умолчанию 5 мин) заставляет консьюмера покинуть группу; симптом — бесконечные rebalance и дубликаты, а не ошибка в твоём коде. Уменьши `max.poll.records` или передай работу другому потоку и поставь партиции на паузу.
- Auto-commit — это не «commit после каждой записи»: он коммитит весь последний poll при следующем poll. Считай его at-least-once с возможностью потери, если упасть сразу после commit.
- `auto.offset.reset=latest` у совершенно новой группы молча пропускает накопившиеся данные; `earliest` на огромном топике перегонит всё в твою базу данных.
- Consumer group, остановленная дольше `offsets.retention.minutes` (7 дней), теряет свои offsets и стартует заново согласно `auto.offset.reset`.
- Связанные заметки: [[spring-kafka-consumer]] (контейнер, `AckMode`, обработчики ошибок, DLT), [[kafka-producer]], [[kafka-architecture]], [[idempotency]].
