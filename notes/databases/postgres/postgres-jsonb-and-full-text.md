---
title: Postgres jsonb and full-text search basics
tags: [postgres, jsonb, full-text-search, gin]
learned: 2026-10-27
---
## TL;DR
`jsonb` stores parsed, binary JSON that can be indexed with GIN and queried with containment (`@>`), path (`->`, `->>`, `#>>`), existence (`?`) and SQL/JSON path (`@?`, `@@`) operators. It is the right tool for sparse or vendor-specific attributes next to a relational core, not a replacement for columns you filter, join or constrain on. Full-text search turns text into a `tsvector` of normalized lexemes, matches it against a `tsquery`, ranks with `ts_rank`, and uses the same GIN index family. It covers "search the description field" well; relevance tuning, facets and fuzzy matching across millions of documents are where [[elasticsearch-basics]] starts.

## Key points

### json vs jsonb
| | `json` | `jsonb` |
|---|---|---|
| Storage | original text | decomposed binary, keys deduplicated and sorted |
| Write | cheap | parse cost, slightly larger |
| Read | reparses on every access | direct access, indexable |
| Preserves | whitespace, key order, duplicate keys | none of these |
| Use | audit copies of a payload as received | everything else |

### Operators and functions
- `->` returns `jsonb`, `->>` returns `text`; `#>'{a,b}'` and `#>>` for paths. `data->>'iccid'` is `text`, so comparing with a number needs a cast: `(data->>'retries')::int > 3`.
- `@>` containment: `data @> '{"profileType": "prepaid"}'` is the operator GIN accelerates best. `?` key exists, `?|` any of, `?&` all of.
- SQL/JSON path (PG 12+): `data @? '$.tags[*] ? (@ == "vip")'`, `jsonb_path_query(data, '$.sims[*].iccid')`. PG 17 added the standard `JSON_TABLE`, `JSON_EXISTS`, `JSON_QUERY`, `JSON_VALUE`.
- Modification: `||` merges top-level keys, `-` removes a key, `jsonb_set(data, '{status}', '"ACTIVE"')`, `jsonb_build_object`, `jsonb_agg`. An update rewrites the whole value (TOAST), so a 100 KB document updated for one flag is 100 KB of WAL each time.
- `jsonb_to_recordset` / `jsonb_array_elements` unnest arrays into rows for joins and aggregation.

### Indexing jsonb
- `CREATE INDEX ... USING gin (data)` with the default `jsonb_ops`: supports `@>`, `?`, `?|`, `?&`, `@?`, `@@`; indexes every key and value.
- `USING gin (data jsonb_path_ops)`: supports only `@>` and the path operators but is smaller and faster; the usual choice when queries are containment-shaped.
- For one hot field, an expression B-tree beats GIN: `CREATE INDEX ON sim ((data->>'eid'))` serves `WHERE data->>'eid' = ?` with equality and ordering, and can be `UNIQUE`. GIN cannot do range or sort.
- GIN writes are expensive; `fastupdate` buffers entries in a pending list that is merged by vacuum or when it exceeds `gin_pending_list_limit`. Watch for a sudden slow insert that triggered the merge ([[postgres-indexes]]).
- Statistics on jsonb are poor (no per-key histograms); the planner guesses selectivity for `@>` as a constant, so `EXPLAIN ANALYZE` estimate errors on jsonb predicates are normal ([[explain-analyze]]).

### When to use jsonb
- Vendor-specific or optional attributes (per-operator eSIM profile metadata), event payloads, configuration blobs, audit snapshots, schemaless ingestion before the model stabilizes.
- Not for: anything with a foreign key, anything you aggregate or sort on routinely, money, status columns. Promote a key to a real column the moment it appears in a `WHERE` of a hot query or in a constraint ([[data-modeling-for-services]]).
- Validate shape with `CHECK (jsonb_typeof(data->'sims') = 'array')` or with `pg_jsonschema`; otherwise the schema lives only in Java.
- Hibernate 6 maps `jsonb` with `@JdbcTypeCode(SqlTypes.JSON)` on a `Map` or a record field; no extra library needed.

### Full-text search
- `to_tsvector('english', text)` lowercases, strips stop words, stems (`activations` → `activ`) and records positions. `to_tsquery('english', 'activ & fail')` / `plainto_tsquery` / `websearch_to_tsquery('"profile download" -test')` build queries; `@@` matches.
- Store the vector as a generated column: `search tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(body,''))) STORED` (PG 12+), index it with GIN. `setweight` lets title matches outrank body matches; `ts_rank_cd` orders results; `ts_headline` builds snippets.
- Language configuration matters: `'simple'` only lowercases and splits, safe for identifiers and multilingual text; `'english'`/`'russian'` stem. Mixed-language content usually gets `'simple'` plus a trigram index.
- Trigram search (`pg_trgm`, `GIN (col gin_trgm_ops)`) handles `LIKE '%abc%'`, `ILIKE` and `similarity()` for typo tolerance; it is not full-text but solves most "search by partial name/MSISDN" requests.
- Limits compared to a search engine: no BM25 by default, no faceting, no synonyms without a thesaurus dictionary, ranking cannot use an index so `ORDER BY ts_rank` sorts every match. Fine up to a few million rows with selective queries ([[search-and-indexing-services]]).

## Example
```sql
-- Operator-specific attributes in jsonb, hot key promoted to an expression index
ALTER TABLE sim ADD COLUMN attrs jsonb NOT NULL DEFAULT '{}';
CREATE INDEX CONCURRENTLY sim_attrs_gin ON sim USING gin (attrs jsonb_path_ops);
CREATE UNIQUE INDEX CONCURRENTLY sim_eid_idx ON sim ((attrs->>'eid')) WHERE attrs ? 'eid';

SELECT id, attrs->>'eid' AS eid, attrs #>> '{profile,type}' AS profile_type
FROM sim
WHERE attrs @> '{"profile": {"type": "prepaid"}, "flags": ["esim"]}'
  AND status = 'ACTIVE';

-- Support ticket search with weighted ranking and a snippet
ALTER TABLE tickets ADD COLUMN search tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', coalesce(subject, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(body, '')),    'B')
  ) STORED;
CREATE INDEX CONCURRENTLY tickets_search_idx ON tickets USING gin (search);

SELECT id, subject, ts_rank_cd(search, q) AS rank,
       ts_headline('simple', body, q, 'MaxWords=20') AS snippet
FROM tickets, websearch_to_tsquery('simple', 'profile download failed') q
WHERE search @@ q
ORDER BY rank DESC
LIMIT 20;
```

## Pitfalls
- `WHERE data->>'status' = 'ACTIVE'` with only a GIN index on `data`: GIN does not support `->>` equality; use `@>` or add an expression B-tree.
- A jsonb column that grows into the whole domain model: no constraints, no FKs, every query a `->>` chain, and no statistics for the planner.
- Updating one key of a large jsonb value rewrites the full TOASTed value and defeats HOT updates; keep frequently changed fields as columns ([[postgres-vacuum-and-bloat]]).
- `to_tsvector` without a fixed configuration argument uses `default_text_search_config`, which can differ between environments and silently changes what the stored vector contains.
- When Postgres search is not enough: [[elasticsearch-basics]]; choosing between flexible columns and a document store: [[sql-vs-nosql-choosing]].
