---
title: Data modeling for services: ownership, aggregates, soft delete, audit
tags: [data-modeling, microservices, postgres, schema-design]
learned: 2026-10-27
---
## TL;DR
Each service owns its database (or at least its schema) and exposes data only through its API or events; no other service reads its tables. Inside the schema, model around aggregates: one root table per aggregate, child tables for parts that live and die with it, references to other aggregates by id without foreign keys across service boundaries. Add the operational columns from day one (`created_at`, `updated_at`, `version`, who changed it), decide deliberately between soft and hard delete, and keep status machines explicit. Normalization rules are in [[normalization-and-denormalization]]; this note is the service-level conventions.

## Key points

### Database per service
- Sharing one database between services couples their schemas: a column rename in one deploys all of them, and a slow query in one starves the other's connection pool ([[monolith-vs-microservices]]).
- Options from loose to strict: separate schema in one Postgres cluster with per-service roles (cheap, enforces access through `GRANT`), separate database, separate cluster (needed when load or failure domains must be isolated).
- Data another service needs arrives by API call or as an event copy ([[event-driven-architecture]], [[outbox-pattern]]). The copy is a local, read-only projection with its own table; it may be stale and must never be written locally. Keep the source's id as the join key.
- Reporting and analytics read from a replica or a warehouse fed by CDC, never from the service's primary ([[postgres-replication-and-failover]]).

### Aggregates as tables
- An aggregate ([[aggregates-and-invariants]]) is the transactional unit: `esim_order` with `esim_order_item` children. Children have a foreign key to the root with `ON DELETE CASCADE` and are only reached through the root; another aggregate (`subscriber`) is referenced by `subscriber_id bigint NOT NULL` without a foreign key if it lives in another service, with one if it lives here ([[constraints-and-relationships]]).
- One transaction touches one aggregate; cross-aggregate changes go through events or a saga ([[saga]]). This keeps row locks short and makes the aggregate the natural partition key later ([[sharding-strategies]]).
- Put invariants the database can express into constraints: `CHECK (quantity > 0)`, `UNIQUE (subscriber_id, iccid)`, exclusion constraints for non-overlapping ranges. The database is the last line of defence against concurrent requests the service logic did not foresee.

### Standard columns
| Column | Type | Why |
|---|---|---|
| `id` | `bigint identity` or `uuid` (v7) | see [[time-money-and-ids]] |
| `created_at`, `updated_at` | `timestamptz NOT NULL DEFAULT now()` | debugging, retention, ordering; `updated_at` via trigger or ORM `@UpdateTimestamp` |
| `version` | `int NOT NULL DEFAULT 0` | optimistic locking (`@Version`), [[jpa-locking]] |
| `created_by`, `updated_by` | `text` or user id | audit; Spring Data `@CreatedBy` with `AuditorAware` |
| `status` | `text` + `CHECK` or a lookup table | explicit state machine; avoid booleans that multiply (`is_active`, `is_blocked`, `is_deleted`) |
| `external_ref` / `idempotency_key` | `text UNIQUE` | dedupe inbound requests and map to partner systems ([[idempotency]]) |

- Prefer `text` with a `CHECK` over a Postgres `enum` type: adding an enum value is DDL that cannot run inside a transaction in older versions and is awkward to remove; a lookup table is better when values carry attributes.

### Soft delete vs hard delete
- **Soft delete** (`deleted_at timestamptz NULL`): keeps history and references intact, allows undo, needed for audit/legal retention. Cost: every query must filter `deleted_at IS NULL` (Hibernate `@SQLRestriction`, or a view), unique constraints need a partial index `WHERE deleted_at IS NULL`, foreign keys still point at "deleted" rows, and tables never shrink.
- **Hard delete** with an **audit/archive table** or an event: simpler queries, real cleanup, GDPR erasure is a real delete. Cost: cascading deletes must be designed, and history lives elsewhere.
- Rule of thumb: soft delete for user-facing entities that can be restored or must be shown in history (orders, subscriptions); hard delete plus audit for transient or high-volume data (sessions, tokens, events); partition and drop for logs ([[postgres-partitioning]]).

### Audit and history
- Minimum: `created_*`/`updated_*` columns plus application logs with a trace id ([[observability-three-pillars]]).
- Row history: an `*_history` table filled by a trigger or by the application (Hibernate Envers writes `_AUD` tables), or an append-only domain events table that doubles as the outbox. Temporal tables with `tstzrange` plus an exclusion constraint give "as of" queries.
- Never audit by overwriting: `UPDATE ... SET status = 'CANCELLED'` without a history row loses who did it and when.

### Avoiding common shapes
- Entity-attribute-value tables (`entity_id, key, value`) to be "flexible": use `jsonb` for real sparse attributes and columns for everything else ([[postgres-jsonb-and-full-text]]).
- A `settings` row per tenant with 80 nullable columns: split by concern or use `jsonb` with a schema check.
- Comma-separated ids in a text column: an array column with a GIN index, or a proper join table.
- Multi-tenant columns and isolation levels are a separate decision ([[multi-tenancy-strategies]]).

## Example
```sql
-- V3__esim_order_aggregate.sql
CREATE TABLE esim_order (
    id              uuid        PRIMARY KEY,                -- UUID v7 generated by the app
    subscriber_id   bigint      NOT NULL,                   -- other service: no FK, just the id
    idempotency_key text        NOT NULL,
    status          text        NOT NULL DEFAULT 'NEW'
                    CHECK (status IN ('NEW','RESERVED','DOWNLOADING','ACTIVE','FAILED','CANCELLED')),
    operator_attrs  jsonb       NOT NULL DEFAULT '{}',
    version         int         NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text        NOT NULL,
    deleted_at      timestamptz
);
CREATE UNIQUE INDEX esim_order_idem_uq ON esim_order (idempotency_key) WHERE deleted_at IS NULL;
CREATE INDEX esim_order_subscriber_idx ON esim_order (subscriber_id, created_at DESC) WHERE deleted_at IS NULL;

CREATE TABLE esim_order_item (
    id          bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id    uuid    NOT NULL REFERENCES esim_order(id) ON DELETE CASCADE,
    iccid       text    NOT NULL,
    profile     text    NOT NULL,
    UNIQUE (order_id, iccid)
);
CREATE INDEX esim_order_item_order_idx ON esim_order_item (order_id);   -- FK columns are not indexed automatically

CREATE TABLE esim_order_status_history (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id    uuid        NOT NULL REFERENCES esim_order(id) ON DELETE CASCADE,
    from_status text,
    to_status   text        NOT NULL,
    changed_by  text        NOT NULL,
    changed_at  timestamptz NOT NULL DEFAULT now(),
    reason      text
);
```

## Pitfalls
- Foreign keys across service boundaries (even when both schemas sit in one cluster): the next team to split the cluster finds dangling references and a migration nobody planned.
- Soft delete without partial unique indexes: the "deleted" email blocks re-registration, or worse, uniqueness is dropped to allow it.
- Reusing a status column for two concerns (`status = 'PAID_BUT_NOT_SHIPPED'`): states multiply; model `payment_status` and `fulfilment_status` separately.
- Forgetting `ON DELETE` behaviour: the default `NO ACTION` blocks deletes of a root with children, and a cascade on the wrong FK wipes history.
- Evolving this schema safely: [[schema-migrations-flyway-liquibase]]; DDD background: [[aggregates-and-invariants]], [[bounded-contexts-and-context-mapping]].
