---
title: Search and indexing services — when to add Elasticsearch and how to keep it in sync
tags: [system-design, search, elasticsearch, cdc]
learned: 2026-10-27
---
## TL;DR
A relational database answers "find rows where column = value" well and "find documents that mention *roaming* near *Turkey*, ranked by relevance, with counts per tariff" badly. A search service (Elasticsearch/OpenSearch, built on Lucene inverted indexes) answers the second kind in milliseconds, at the cost of a second copy of the data that is **eventually consistent** with the source of truth. The design decisions are: whether Postgres full-text search is enough, what to index (a denormalised document per searchable entity), how to sync (outbox or CDC into Kafka, an indexer consumer, idempotent upserts by id and version), and how to reindex from scratch without downtime (aliases). Internals of Elasticsearch are in [[elasticsearch-basics]].

## Key points

### Do you need it?
| Need | Postgres | Search engine |
|---|---|---|
| `WHERE name ILIKE '%x%'` on 100 K rows | fine with a trigram GIN index | overkill |
| Full-text with stemming, ranking, one language | `tsvector` + GIN, `ts_rank` ([[postgres-jsonb-and-full-text]]) | better relevance (BM25), multi-language analyzers |
| Fuzzy / typo-tolerant, autocomplete, synonyms | weak | native |
| Facets ("count per status, per channel") over millions of rows | slow aggregates | aggregations in ms |
| Search across several entities / services | joins across services impossible | one denormalised index |
| Log and event exploration | wrong tool | the classic use ([[logging-stacks]]) |
| Geo queries | PostGIS | geo_point, fine for "near me" |

Rule: start with Postgres full-text; add the search service when relevance, facets, cross-entity search or volume (tens of millions of documents, thousands of queries/s) make Postgres the bottleneck.

### What the index looks like
- One **document per searchable thing**, denormalised: a notification document carries the subscriber's name and tariff, the template text and the provider error, so a query needs no join ([[normalization-and-denormalization]]).
- Explicit **mapping**: `keyword` for exact filters and facets (status, channel), `text` with an analyzer for free text, `date`, `long`; disable indexing of fields only stored for display. Dynamic mapping in production creates a field explosion.
- **Shards and replicas**: a shard is a Lucene index; aim for 10–50 GB per shard; replicas for read throughput and HA. Shard count is fixed per index, so plan for the size at retention, or use time-based indices (`notifications-2026.10`) with an alias.
- Search traffic is **read-mostly** and bursty; writes come in bulk from the indexer. Keep them on separate paths (bulk API, refresh interval 1–30 s).

### Keeping it in sync
| Approach | Consistency | Coupling | Notes |
|---|---|---|---|
| Dual write from the service (DB then ES) | lost updates on failure | high | the dual-write problem; avoid |
| Outbox → Kafka → indexer | eventual, seconds | low | the service already publishes events ([[outbox-pattern]]) |
| CDC (Debezium) → Kafka → indexer | eventual, sub-second | none in app code | captures every row change, including backfills; schema changes need care |
| Periodic full/incremental reindex job | minutes | none | fallback and bootstrap; `updated_at` watermark |

- The **indexer** is a Kafka consumer that upserts documents by id; it must be idempotent because delivery is at-least-once ([[idempotency]]). Use **external versioning** (`version = updated_at` or an event sequence) so a stale event cannot overwrite a newer document.
- Deletes are events too (tombstones); without them the index keeps ghosts.
- Lag between DB and index is a metric and an SLO ("searchable within 5 s"); after writing, the UI should not search for the row it just created (or should read it from the DB) ([[read-replicas-and-stale-reads]] is the same problem).

### Reindexing without downtime
Create `notifications-v2` with the new mapping, run the indexer into both (or replay the Kafka topic / CDC snapshot into v2), verify counts, then atomically switch the alias `notifications` from v1 to v2 and drop v1. Aliases are the deployment unit; code never names a physical index.

### Operating it
- Search is a derived store: lose it, rebuild it from Postgres. Back up anyway (snapshots to blob storage) to shorten recovery.
- Memory: Lucene wants the OS page cache; JVM heap ≤ 50 % of RAM and ≤ 32 GB. Queries with deep pagination (`from: 10000`) are expensive; use `search_after` (keyset, the same idea as in [[api-design-for-system-design]]).
- Security: never expose the cluster to clients; the service builds queries, enforces tenant filters (`term: tenantId`) server-side ([[multi-tenancy-strategies]]).
- For operator tooling, Kibana/OpenSearch Dashboards on the same data covers 80 % of "give me a dashboard" requests.

## Example
The indexer consumer: an outbox event becomes a denormalised document with external versioning, written in bulk; the alias is the only name the code knows.

```java
@Component
public class NotificationIndexer {
    private static final String ALIAS = "notifications";
    private final ElasticsearchClient es;
    private final SubscriberLookup subscribers;   // cached read of name/tariff, see caching note

    public NotificationIndexer(ElasticsearchClient es, SubscriberLookup subscribers) {
        this.es = es; this.subscribers = subscribers;
    }

    @KafkaListener(topics = "notifications.changed", groupId = "search-indexer", batch = true)
    public void onBatch(List<ConsumerRecord<String, NotificationChanged>> records, Acknowledgment ack) throws IOException {
        var ops = new ArrayList<BulkOperation>(records.size());
        for (var r : records) {
            NotificationChanged e = r.value();
            long version = e.updatedAt().toEpochMilli();            // monotonic per entity, stale events lose
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
            // 409 version_conflict = an older event arrived after a newer one: already correct, skip.
            if (item.error() != null && !"version_conflict_engine_exception".equals(item.error().type())) {
                throw new IllegalStateException("index failed for " + item.id() + ": " + item.error().reason());
            }
        }
        ack.acknowledge();                                           // at-least-once; the upsert is idempotent
    }
}

record NotificationDoc(String id, long subscriberId, String subscriberName, String tariff, String template,
                       String text, String channel, String status, String providerError, Instant createdAt) {}
```

Mapping for the index (set once, before the alias points at it): `status`, `channel`, `tariff` as `keyword` for facets; `text` and `providerError` as `text` with a `russian`/`english` analyzer; `createdAt` as `date`; `subscriberName` as `text` with a `keyword` sub-field for exact matches.

## Pitfalls
- Dual write (`repo.save(); es.index();`): a failed second call leaves the index wrong forever; use the outbox or CDC ([[outbox-pattern]]).
- Index without external versioning: Kafka redelivery or a rebalance reorders two updates to the same id and the older one wins.
- Dynamic mapping on free-form JSON: every new key becomes a field, mapping explodes, cluster slows and eventually refuses writes.
- Deep pagination with `from/size`: page 1 000 fetches and sorts 10 000 hits per shard; use `search_after` and a point-in-time.
- Treating the search cluster as a database for business reads (`GET /notifications/{id}` from ES): stale by design, and a cluster incident becomes a product incident. Read truth from Postgres, search from the index ([[storage-choices]]).
- Related: [[elasticsearch-basics]] for cluster and query details; [[message-queues-and-streams]] for the pipeline that feeds the indexer.
