---
title: MongoDB basics: documents, indexes, aggregation, transactions
tags: [mongodb, nosql, document-store, data-modeling]
learned: 2026-10-27
---
## TL;DR
MongoDB stores BSON documents in collections; each document is a self-contained aggregate (nested objects and arrays) with an `_id`, and a single-document write is always atomic. Indexes work like B-trees on any field, including fields inside arrays; the aggregation pipeline is the query language for anything beyond `find`. Multi-document ACID transactions exist since 4.0 (replica sets) and 4.2 (sharded) but cost latency and are meant for the exceptions, not the default. Choose it when the data is naturally document-shaped, read mostly as a whole, and the schema varies; choose Postgres when you need joins, constraints or cross-entity invariants ([[sql-vs-nosql-choosing]]).

## Key points

### Data model
- Document = BSON object up to 16 MB, with typed fields (`ObjectId`, `Date`, `Decimal128`, arrays, embedded documents). Collections have no enforced schema unless you add JSON Schema validation (`validator` on the collection); do that for anything that is a system of record.
- The core decision is **embed vs reference**. Embed what is read and written together and bounded in size (order lines inside an order, addresses inside a customer); reference by id what is shared, unbounded or updated independently (the product a line refers to, the events of a device). Unbounded arrays (a growing `events` array inside a document) hit the 16 MB limit and make every update rewrite the document.
- There are no joins in the relational sense; `$lookup` in the aggregation pipeline does a left outer join against another collection, executed per input document, so it is for small result sets. Denormalize the fields you need at read time and accept that they can be stale ([[normalization-and-denormalization]]).
- `_id` defaults to a 12-byte `ObjectId` (timestamp + random + counter): roughly time-ordered, which helps index locality the same way UUID v7 does ([[time-money-and-ids]]).

### CRUD and update operators
- `find(filter, projection)` with operators `$eq`, `$gt`, `$in`, `$regex`, `$elemMatch` for arrays, dot notation for nested fields (`"profile.type"`).
- Updates are **in-place operators**, not whole-document replacement: `$set`, `$inc`, `$push`, `$addToSet`, `$pull`, array filters (`arrayFilters`) for updating one element. `findOneAndUpdate` with a filter on the current state is the optimistic-locking primitive (`{_id, version: 3}` → `$set: {status}, $inc: {version: 1}`), replacing a transaction in most cases.
- `upsert: true` inserts when nothing matches; combined with a unique index it is the idempotent write ([[idempotency]]).

### Indexes
| Index | Use |
|---|---|
| Single field / compound (`{status: 1, createdAt: -1}`) | same leftmost-prefix and sort rules as in [[database-indexes]]; the ESR rule: Equality fields first, then Sort fields, then Range fields |
| Multikey | automatic when the field is an array; one entry per element; a compound index may contain at most one array field |
| Unique, partial (`partialFilterExpression`), sparse, TTL (`expireAfterSeconds`) | constraints, hot subsets, automatic expiry of sessions and tokens |
| Text, 2dsphere, wildcard | basic full text (one per collection), geo queries, indexing unknown dynamic fields |
- `explain("executionStats")` shows `COLLSCAN` vs `IXSCAN`, `totalKeysExamined` vs `nReturned`; a large gap is the equivalent of `Rows Removed by Filter` ([[explain-analyze]]). Indexes are built in the background by default since 4.2.

### Aggregation pipeline
- A sequence of stages: `$match` → `$group` → `$project` → `$sort` → `$limit`, plus `$unwind` (array to rows), `$lookup`, `$facet`, `$bucket`, `$merge` (write results to a collection). Put `$match` and `$project` first so later stages see fewer, smaller documents and `$match` can use an index.
- Stages are limited to 100 MB of memory each unless `allowDiskUse: true`. Pipelines replace both reporting SQL and most application-side loops.

### Transactions and consistency
- A **replica set** (one primary, N secondaries, automatic election via Raft-like protocol) is the deployment unit; a standalone node is for development only ([[leader-follower-replication]], [[raft-explained]]).
- **Write concern** `w: "majority"` waits for a majority of nodes and survives failover; `w: 1` can lose acknowledged writes on primary failure. **Read concern** `majority` returns only majority-committed data; `local` may return data later rolled back. **Read preference** `secondaryPreferred` routes reads to replicas with lag ([[read-replicas-and-stale-reads]]).
- Multi-document transactions: `session.withTransaction(...)`, snapshot isolation, abort on write conflict (retry), 60 s default lifetime, locks held on touched documents. Each transaction holds a snapshot across the WiredTiger cache, so long or many transactions hurt. Model aggregates so one document is the transactional boundary ([[aggregates-and-invariants]]).
- Sharding: a shard key chosen up front (hashed or ranged) decides the distribution; a monotonic key like a timestamp makes one hot shard, a low-cardinality key makes jumbo chunks. Rules are the same as [[sharding-strategies]].

### Spring Data MongoDB
- `MongoTemplate` for explicit queries and updates, `MongoRepository` for derived queries; `@Document`, `@Indexed`, `@CompoundIndex`; `@Transactional` works with a `MongoTransactionManager`. Map `Decimal128` to `BigDecimal` for money; the default `Double` is wrong for it.

## Example
```javascript
// Subscriber profile as one document; per-operator settings embedded, history referenced
db.subscribers.insertOne({
  _id: ObjectId(),
  msisdn: "996555123456",
  status: "ACTIVE",
  profiles: [
    { iccid: "8999600000000000001", type: "prepaid", operator: "beeline_kg", activatedAt: ISODate() }
  ],
  version: 1,
  updatedAt: ISODate()
});

db.subscribers.createIndex({ msisdn: 1 }, { unique: true });
db.subscribers.createIndex({ "profiles.iccid": 1 });                         // multikey
db.subscribers.createIndex({ status: 1, updatedAt: -1 },
                           { partialFilterExpression: { status: "SUSPENDED" } });

// Optimistic update of one embedded profile, no transaction needed
db.subscribers.findOneAndUpdate(
  { msisdn: "996555123456", version: 1, "profiles.iccid": "8999600000000000001" },
  { $set: { "profiles.$.type": "postpaid", updatedAt: new Date() }, $inc: { version: 1 } },
  { returnDocument: "after" }
);

// Report: active profiles per operator
db.subscribers.aggregate([
  { $match: { status: "ACTIVE" } },
  { $unwind: "$profiles" },
  { $group: { _id: "$profiles.operator", n: { $sum: 1 } } },
  { $sort: { n: -1 } }
]);
```

## Pitfalls
- Modeling relational data as documents with references everywhere, then needing `$lookup` on every read: slower than Postgres joins and without foreign-key integrity.
- Default write concern on older drivers was `w: 1`; acknowledged writes vanished on failover. Set `w: "majority"` on the connection string.
- Unbounded embedded arrays (chat messages in a conversation document) grow until the 16 MB limit and make every append rewrite the document.
- Using multi-document transactions as the normal path because the team is used to `@Transactional`: throughput collapses under contention. Redesign the aggregate instead.
- Flexible schema needs the same migration discipline as SQL: version documents and migrate lazily on read, or in batches ([[schema-migrations-flyway-liquibase]]); a document store is still a choice to justify, see [[sql-vs-nosql-choosing]].
