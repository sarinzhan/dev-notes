---
title: Elasticsearch basics: inverted index, mappings, when to use it
tags: [elasticsearch, search, nosql, inverted-index]
learned: 2026-10-27
---
## TL;DR
Elasticsearch (and its fork OpenSearch) is a distributed search engine built on Lucene: documents are analyzed into terms, stored in an **inverted index** (term → list of documents), and queried with relevance scoring (BM25), filters, aggregations and highlighting. It is near-real-time (a refresh every second), eventually consistent, and not a system of record. Use it when search needs relevance ranking, typo tolerance, faceting or log analytics over large text volumes; keep Postgres as the source of truth and feed the index asynchronously ([[search-and-indexing-services]]). Postgres full-text search covers simpler cases ([[postgres-jsonb-and-full-text]]).

## Key points

### Inverted index
- A B-tree answers "what is the value of field X for row Y"; an inverted index answers "which documents contain term T". Each field's text goes through an **analyzer** (character filters → tokenizer → token filters: lowercase, stemming, stop words, synonyms, n-grams) and produces terms; the index maps each term to a postings list of document ids, positions and term frequencies.
- Scoring: BM25 weighs term frequency in the document, inverse document frequency across the index, and document length. `match` queries score; `term`/`range` inside a `filter` context do not score and are cached as bitsets, so put exact conditions (status, tenant, date range) in `filter` and only text in `must`.
- Lucene segments are immutable; a **refresh** (default 1 s) makes new segments searchable, a **flush** writes the translog to disk, **merges** compact segments in the background. The same LSM-shaped life cycle as [[column-and-wide-column-stores]]; deletes are markers until merge.

### Mappings
- A mapping is the schema: field types and analyzers. Dynamic mapping guesses types from the first document (a string becomes both `text` and `keyword`), which is convenient and dangerous: a field first seen as `long` rejects later strings. Define explicit mappings for anything that matters, and `"dynamic": "strict"` on production indices.
- Field types: `text` (analyzed, for search), `keyword` (exact, for filters, sorting, aggregations), `long`/`double`, `date`, `boolean`, `ip`, `geo_point`, `nested` (array of objects queried independently), `object` (flattened). A field cannot change type in place: reindex into a new index and switch an alias.
- Index per time period (`logs-2026.10.27`) with an alias for writes and index lifecycle management (hot → warm → delete) is the standard log pattern; one index per entity type for application search.

### Cluster
- An index is split into **primary shards** (fixed at creation) with **replica shards**; shards are Lucene indices spread across data nodes. A query fans out to one copy of every shard, then the coordinating node merges results (scatter-gather, [[cross-shard-queries-and-transactions]]). Too many small shards waste heap; aim for shards of 10–50 GB.
- Writes go to the primary shard then replicate synchronously to replicas before acknowledgement; a document is visible after the next refresh. `GET` by id is real-time through the translog; search is not.
- Master-eligible nodes elect a master via a Raft-like protocol (Zen2, 7.x+) with a quorum of master-eligible nodes ([[quorums-and-leader-election]]). Split-brain was a classic 6.x failure mode, fixed by voting configurations.
- Deep pagination (`from: 10000`) is forbidden by default (`index.max_result_window`); use `search_after` with a sort tiebreaker for scrolling through results.

### Query DSL essentials
| Query | Use |
|---|---|
| `match`, `multi_match` | analyzed full-text search over one or several fields with boosts |
| `match_phrase`, `slop` | words in order, with tolerance |
| `term`, `terms`, `range`, `exists` | exact filters on `keyword`/numeric/date fields |
| `bool` with `must`/`should`/`filter`/`must_not` | combine, scoring vs filtering |
| `fuzzy`, `match` with `fuzziness: AUTO` | typo tolerance by edit distance |
| `nested` | query objects inside arrays as units |
| aggregations: `terms`, `date_histogram`, `avg`, `percentiles` | facets, dashboards, log analytics |
- `completion` suggesters and edge n-gram analyzers implement autocomplete; `highlight` returns snippets.

### Keeping the index in sync
- The application never dual-writes to Postgres and Elasticsearch in one request ([[outbox-pattern]] explains why). Options: outbox → Kafka → indexer service; Debezium CDC → Kafka Connect Elasticsearch sink; a periodic full reindex for small datasets. Index by business id so re-delivery is idempotent ([[idempotency]]).
- Reindexing on mapping change: create `products_v2`, run `_reindex` or replay from the source of truth, then atomically switch the alias `products` from v1 to v2.
- Delete from the index when the source row is deleted or hidden; otherwise search returns ghosts and the UI shows 404s on click.

### Spring
- Spring Data Elasticsearch (`ElasticsearchOperations`, `@Document`, repositories) or the official Java client (`ElasticsearchClient`, builder-based DSL). Version alignment between client, server and Spring Boot is strict; OpenSearch needs its own client.

## Example
```json
PUT /tickets
{
  "settings": { "number_of_shards": 1, "number_of_replicas": 1 },
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "id":        { "type": "keyword" },
      "subject":   { "type": "text", "analyzer": "english" },
      "body":      { "type": "text", "analyzer": "english" },
      "status":    { "type": "keyword" },
      "tenant":    { "type": "keyword" },
      "createdAt": { "type": "date" }
    }
  }
}

GET /tickets/_search
{
  "query": {
    "bool": {
      "must":   [{ "multi_match": { "query": "profile download failed",
                                    "fields": ["subject^3", "body"], "fuzziness": "AUTO" } }],
      "filter": [{ "term": { "tenant": "beeline-kg" } },
                 { "terms": { "status": ["OPEN", "IN_PROGRESS"] } },
                 { "range": { "createdAt": { "gte": "now-30d" } } }]
    }
  },
  "highlight": { "fields": { "body": {} } },
  "aggs": { "by_status": { "terms": { "field": "status" } } },
  "sort": [ "_score", { "createdAt": "desc" } ],
  "size": 20
}
```
The text search scores; tenant, status and date are filters, cached and excluded from scoring; the aggregation gives the facet counts for the sidebar in the same round trip.

## Pitfalls
- Using Elasticsearch as the primary store: no transactions, documents can be lost on an unclean shard failover, and a mapping mistake requires a full reindex from somewhere.
- Dynamic mapping in production: the first document with `"amount": "12.50"` makes the field a string forever on that index.
- Sorting or aggregating on a `text` field (`fielddata: true`): loads the whole field into heap; use the `.keyword` sub-field.
- Querying immediately after indexing in a test and getting nothing: the refresh has not happened; call `_refresh` in tests or use `refresh=wait_for` on the write.
- When Postgres is enough: [[postgres-jsonb-and-full-text]]; where search sits in a system design: [[search-and-indexing-services]].
