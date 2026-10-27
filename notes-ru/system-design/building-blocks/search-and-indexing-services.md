---
title: Сервисы поиска и индексации — когда добавлять Elasticsearch и как держать его в синхроне
tags: [system-design, search, elasticsearch, cdc]
learned: 2026-10-27
---
## Кратко
Реляционная база хорошо отвечает на «найти строки, где column = value» и плохо — на «найти документы, где упоминается *роуминг* рядом с *Турцией*, ранжированные по релевантности, с количеством по каждому тарифу». Поисковый сервис (Elasticsearch/OpenSearch, построенный на инвертированных индексах Lucene) отвечает на второй тип запросов за миллисекунды ценой второй копии данных, **согласованной в конечном счёте (eventually consistent)** с источником истины. Решения проектирования: хватит ли полнотекстового поиска Postgres, что индексировать (денормализованный документ на каждую сущность, по которой ищут), как синхронизировать (outbox или CDC в Kafka, consumer-индексатор, идемпотентные upsert по id и версии) и как переиндексировать с нуля без простоя (алиасы). Внутреннее устройство Elasticsearch — в [[elasticsearch-basics]].

## Основное

### Нужен ли он?
| Потребность | Postgres | Поисковый движок |
|---|---|---|
| `WHERE name ILIKE '%x%'` по 100 K строк | нормально с триграммным GIN-индексом | избыточно |
| Полнотекстовый поиск со стеммингом, ранжированием, один язык | `tsvector` + GIN, `ts_rank` ([[postgres-jsonb-and-full-text]]) | лучшая релевантность (BM25), анализаторы для нескольких языков |
| Нечёткий поиск / устойчивость к опечаткам, автодополнение, синонимы | слабо | нативно |
| Фасеты («количество по статусу, по каналу») по миллионам строк | медленные агрегаты | агрегации за мс |
| Поиск по нескольким сущностям / сервисам | join между сервисами невозможны | один денормализованный индекс |
| Исследование логов и событий | не тот инструмент | классическое применение ([[logging-stacks]]) |
| Гео-запросы | PostGIS | geo_point, хватает для «рядом со мной» |

Правило: начинай с полнотекстового поиска Postgres; добавляй поисковый сервис, когда релевантность, фасеты, поиск по нескольким сущностям или объём (десятки миллионов документов, тысячи запросов/с) делают Postgres узким местом.

### Как выглядит индекс
- Один **документ на каждую сущность поиска**, денормализованный: документ уведомления содержит имя и тариф абонента, текст шаблона и ошибку провайдера, так что запросу не нужен join ([[normalization-and-denormalization]]).
- Явный **mapping**: `keyword` для точных фильтров и фасетов (статус, канал), `text` с анализатором для свободного текста, `date`, `long`; отключай индексацию полей, которые хранятся только для отображения. Динамический mapping в production приводит к взрыву числа полей.
- **Шарды и реплики**: шард — это индекс Lucene; ориентируйся на 10–50 GB на шард; реплики — для пропускной способности чтения и HA. Число шардов фиксируется для индекса, поэтому планируй под размер к концу срока хранения или используй индексы по времени (`notifications-2026.10`) с алиасом.
- Поисковый трафик — **в основном чтение** и всплесками; записи приходят пачками от индексатора. Держи их на разных путях (bulk API, refresh interval 1–30 с).

### Синхронизация
| Подход | Согласованность | Связанность | Заметки |
|---|---|---|---|
| Двойная запись из сервиса (БД, затем ES) | потерянные обновления при сбое | высокая | проблема двойной записи (dual write); избегать |
| Outbox → Kafka → индексатор | в конечном счёте, секунды | низкая | сервис уже публикует события ([[outbox-pattern]]) |
| CDC (Debezium) → Kafka → индексатор | в конечном счёте, меньше секунды | нет в коде приложения | захватывает каждое изменение строки, включая backfill; изменения схемы требуют аккуратности |
| Периодическая полная/инкрементальная задача переиндексации | минуты | нет | запасной вариант и начальная загрузка; водяной знак `updated_at` |

- **Индексатор** — это Kafka consumer, который делает upsert документов по id; он обязан быть идемпотентным, потому что доставка at-least-once ([[idempotency]]). Используй **внешнее версионирование (external versioning)** (`version = updated_at` или порядковый номер события), чтобы устаревшее событие не могло перезаписать более новый документ.
- Удаления — тоже события (tombstones); без них в индексе остаются призраки.
- Отставание индекса от БД — это метрика и SLO («доступно для поиска в течение 5 с»); после записи UI не должен искать только что созданную строку (или должен читать её из БД) (в [[read-replicas-and-stale-reads]] та же проблема).

### Переиндексация без простоя
Создать `notifications-v2` с новым mapping, запустить индексатор в оба индекса (или проиграть топик Kafka / CDC-снимок в v2), сверить количества, затем атомарно переключить алиас `notifications` с v1 на v2 и удалить v1. Алиасы — единица развёртывания; код никогда не называет физический индекс.

### Эксплуатация
- Поиск — производное хранилище: потерял — восстанови из Postgres. Но всё равно делай бэкапы (снимки в блоб-хранилище), чтобы сократить восстановление.
- Память: Lucene нужен страничный кэш ОС; heap JVM ≤ 50 % RAM и ≤ 32 GB. Запросы с глубокой пагинацией (`from: 10000`) дорогие; используй `search_after` (keyset, та же идея, что в [[api-design-for-system-design]]).
- Безопасность: никогда не открывай кластер клиентам; сервис строит запросы и применяет фильтры по тенанту (`term: tenantId`) на стороне сервера ([[multi-tenancy-strategies]]).
- Для инструментов операторов Kibana/OpenSearch Dashboards поверх тех же данных закрывают 80 % запросов «дайте мне дашборд».

## Пример
Consumer-индексатор: событие из outbox превращается в денормализованный документ с внешним версионированием и записывается пачкой; алиас — единственное имя, которое знает код.

```java
@Component
public class NotificationIndexer {
    private static final String ALIAS = "notifications";
    private final ElasticsearchClient es;
    private final SubscriberLookup subscribers;   // кэшированное чтение имени/тарифа, см. заметку о кэшировании

    public NotificationIndexer(ElasticsearchClient es, SubscriberLookup subscribers) {
        this.es = es; this.subscribers = subscribers;
    }

    @KafkaListener(topics = "notifications.changed", groupId = "search-indexer", batch = true)
    public void onBatch(List<ConsumerRecord<String, NotificationChanged>> records, Acknowledgment ack) throws IOException {
        var ops = new ArrayList<BulkOperation>(records.size());
        for (var r : records) {
            NotificationChanged e = r.value();
            long version = e.updatedAt().toEpochMilli();            // монотонна для сущности, устаревшие события проигрывают
            if (e.deleted()) {
                ops.add(BulkOperation.of(b -> b.delete(d -> d.index(ALIAS).id(e.id())
                        .version(version).versionType(VersionType.External))));
                continue;
            }
            var subscriber = subscribers.get(e.subscriberId());
            var doc = new NotificationDoc(e.id(), e.subscriberId(), subscriber.name(), subscriber.tariff(),
                    e.template(), e.renderedText(), e.channel(), e.status(), e.providerError(), e.createdAt());
            ops.add(BulkOperation.of(b -> b.index(i -> i.index(ALIAS).id(e.id()).document(doc)
                    .version(version).versionType(VersionType.External))));
        }
        BulkResponse resp = es.bulk(b -> b.operations(ops).refresh(Refresh.False));
        for (var item : resp.items()) {
            // 409 version_conflict = более старое событие пришло после более нового: уже корректно, пропускаем.
            if (item.error() != null && !"version_conflict_engine_exception".equals(item.error().type())) {
                throw new IllegalStateException("index failed for " + item.id() + ": " + item.error().reason());
            }
        }
        ack.acknowledge();                                           // at-least-once; upsert идемпотентен
    }
}

record NotificationDoc(String id, long subscriberId, String subscriberName, String tariff, String template,
                       String text, String channel, String status, String providerError, Instant createdAt) {}
```

Mapping индекса (задаётся один раз, до того как на него укажет алиас): `status`, `channel`, `tariff` как `keyword` для фасетов; `text` и `providerError` как `text` с анализатором `russian`/`english`; `createdAt` как `date`; `subscriberName` как `text` с подполем `keyword` для точных совпадений.

## Подводные камни
- Двойная запись (`repo.save(); es.index();`): упавший второй вызов навсегда оставляет индекс неверным; используй outbox или CDC ([[outbox-pattern]]).
- Индекс без внешнего версионирования: повторная доставка Kafka или rebalance меняют порядок двух обновлений одного id, и побеждает более старое.
- Динамический mapping на JSON произвольной формы: каждый новый ключ становится полем, mapping взрывается, кластер замедляется и в итоге отказывается принимать записи.
- Глубокая пагинация через `from/size`: страница 1 000 выбирает и сортирует 10 000 совпадений на каждом шарде; используй `search_after` и point-in-time.
- Использование поискового кластера как базы данных для бизнес-чтений (`GET /notifications/{id}` из ES): данные устаревшие по определению, а инцидент кластера становится инцидентом продукта. Истину читай из Postgres, ищи по индексу ([[storage-choices]]).
- Связанные заметки: [[elasticsearch-basics]] — подробности о кластере и запросах; [[message-queues-and-streams]] — конвейер, который питает индексатор.
