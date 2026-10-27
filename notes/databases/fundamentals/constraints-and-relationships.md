---
title: Constraints and table relationships
tags: [sql, constraints, foreign-keys, schema-design]
learned: 2026-10-22
---
## TL;DR
Constraints are rules the DBMS enforces on every write: `PRIMARY KEY`, `FOREIGN KEY`, `UNIQUE`, `NOT NULL`, `CHECK`, `DEFAULT` (and Postgres `EXCLUDE`). They are what makes the data trustworthy regardless of which application or script writes it. Relationships between tables are expressed with foreign keys: one-to-one (FK that is also unique), one-to-many (FK on the "many" side), many-to-many (a link table with two FKs). The name "relational" comes from these relations.

## Key points

### Constraints
| Constraint | Guarantees | Notes |
|---|---|---|
| `PRIMARY KEY` | unique + not null identifier of each row; one per table | Postgres creates a unique B-tree index. Prefer a surrogate `bigint GENERATED ALWAYS AS IDENTITY` or `uuid`; natural keys (msisdn, email) change |
| `FOREIGN KEY ... REFERENCES` | the value exists in the referenced table's PK/unique column (referential integrity) | the referenced column must be PK or UNIQUE; the FK column itself is **not** indexed automatically in Postgres: add the index |
| `UNIQUE` | no two rows share the value(s); `NULL`s do not collide (`NULLS NOT DISTINCT` in PG 15 changes that) | implemented as a unique index; also the DB-side half of [[idempotency]] keys |
| `NOT NULL` | value required | cheapest constraint; adding it to a big table scans the table (PG 12+ can skip the scan if a validated `CHECK (col IS NOT NULL)` exists) |
| `CHECK` | arbitrary boolean on the row: `CHECK (amount > 0)`, `CHECK (status IN ('NEW','PAID'))` | cannot reference other rows or tables; for that use triggers or `EXCLUDE` |
| `DEFAULT` | value when the insert omits the column | not a constraint strictly; `DEFAULT now()`, `DEFAULT gen_random_uuid()` |
| `EXCLUDE` (Postgres) | no two rows satisfy an operator over an index, e.g. overlapping time ranges | `EXCLUDE USING gist (room_id WITH =, during WITH &&)` |

- Constraints can be `DEFERRABLE INITIALLY DEFERRED`: checked at commit instead of per statement, useful for circular FKs or swapping unique values.
- Adding a FK or CHECK to a large table: `ADD CONSTRAINT ... NOT VALID` (instant, applies to new rows) and later `VALIDATE CONSTRAINT` (scans with a weaker lock).

### Foreign key actions
What happens to the child rows when the referenced parent row is deleted or its key updated (`ON DELETE` / `ON UPDATE`):
- `NO ACTION` (default) / `RESTRICT` — reject the delete or update while dependent rows exist. The difference: `RESTRICT` checks immediately, `NO ACTION` can be deferred to the end of the statement/transaction.
- `CASCADE` — delete or update the dependent rows too. Convenient for owned data (`order_lines` of an order), dangerous for shared references.
- `SET NULL` — the FK column becomes `NULL` (column must be nullable).
- `SET DEFAULT` — the FK column takes its `DEFAULT` value (which must itself exist in the parent).
- JPA `CascadeType.REMOVE`/`orphanRemoval` is the application-level analogue and does not create a DB-level `ON DELETE CASCADE`; the two should agree (see [[jpa-entities-and-relationships]]).

### Relationships
| Relationship | Schema | Example | JPA |
|---|---|---|---|
| **One-to-one** | FK in one table with a `UNIQUE` constraint (or the same PK shared) | `customers` ↔ `customer_profiles` | `@OneToOne @JoinColumn(unique = true)`; often better as columns in the same table unless the extra data is big or optional |
| **One-to-many** | FK on the "many" side pointing at the "one" side | one `customer` → many `orders` (`orders.customer_id`) | `@ManyToOne` on `Order` is the owner; `@OneToMany(mappedBy)` on `Customer` |
| **Many-to-many** | a **link (junction) table** with two FKs and a composite PK or unique pair | `subscriptions` ↔ `services` via `subscription_services(subscription_id, service_id)` | `@ManyToMany @JoinTable`; promote to an entity as soon as the link needs its own columns (`activated_at`) |
| Self-reference | FK to the same table | `employees.manager_id` | `@ManyToOne Employee manager` |
| Polymorphic | `target_type` + `target_id` with no real FK | comments on orders or tickets | avoid; use one table per target or an inheritance mapping |

- The direction of the FK is "many points to one"; the DB has no notion of "owning side" beyond where the column is. ORMs add that notion.
- Composite primary keys on link tables (`PRIMARY KEY (subscription_id, service_id)`) give uniqueness and an index for lookups by the first column; add a second index on `(service_id)` for the reverse direction.

## Example
```sql
CREATE TABLE subscriptions (
    id            bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    customer_id   bigint NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    tariff_id     bigint NOT NULL REFERENCES tariffs(id),
    status        varchar(16) NOT NULL DEFAULT 'NEW'
                  CHECK (status IN ('NEW', 'ACTIVE', 'SUSPENDED', 'CLOSED')),
    activated_at  timestamptz,
    closed_at     timestamptz,
    CHECK (closed_at IS NULL OR activated_at IS NOT NULL),
    CHECK (closed_at IS NULL OR closed_at >= activated_at)
);
CREATE INDEX subscriptions_customer_idx ON subscriptions (customer_id);  -- FK columns are not indexed by default

-- one-to-one: a profile that may or may not exist
CREATE TABLE esim_profiles (
    subscription_id bigint PRIMARY KEY REFERENCES subscriptions(id) ON DELETE CASCADE,
    iccid           varchar(22) NOT NULL UNIQUE,
    eid             varchar(32)
);

-- many-to-many with attributes on the link
CREATE TABLE subscription_services (
    subscription_id bigint NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    service_id      bigint NOT NULL REFERENCES services(id),
    enabled_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (subscription_id, service_id)
);
CREATE INDEX subscription_services_service_idx ON subscription_services (service_id);
```

## Pitfalls
- Unindexed FK columns: every `DELETE` on the parent does a sequential scan of the child table to check `RESTRICT`, and every join on the FK is slow. Postgres does **not** create that index for you (MySQL InnoDB does).
- `ON DELETE CASCADE` through several levels can delete far more than intended and hold locks for a long time; prefer `RESTRICT` + explicit cleanup for anything not strictly owned.
- Enforcing "status must be one of" only in a Java enum: the first manual `UPDATE` or another service breaks it. Put a `CHECK` or a lookup table in the DB as well.
- `UNIQUE` on a nullable column allows unlimited `NULL`s; use `NULLS NOT DISTINCT` (PG 15) or a partial unique index `WHERE col IS NOT NULL`.
- Dropping a constraint "temporarily" for a bulk load and forgetting to re-add it; use `DEFERRABLE` or `NOT VALID` + `VALIDATE` instead.
- Schema shape behind these rules: [[normalization-and-denormalization]]; indexes created by constraints and the ones you still need: [[database-indexes]].
