---
title: Normal forms and denormalization
tags: [databases, schema-design, normalization, sql]
learned: 2026-10-22
---
## TL;DR
Normalization is structuring tables so that each fact is stored once: it removes duplication and the update/insert/delete anomalies that duplication causes. In practice you aim for third normal form: atomic values (1NF), every non-key column depends on the whole key (2NF), and on nothing but the key (3NF). Denormalization is deliberately copying or pre-aggregating data back into a table to make reads faster, accepting the cost of keeping the copies in sync.

## Key points

### Why normalize
- A denormalized `orders` table with `customer_name, customer_phone` repeated on every row has three anomalies: **update** (change the phone in 10 000 rows, miss one), **insert** (cannot store a customer without an order), **delete** (deleting the last order deletes the customer).
- Normalized schemas are smaller, easier to change, and constraints ([[constraints-and-relationships]]) can protect every fact exactly once.
- There are six normal forms plus Boyce-Codd (BCNF); 1NF–3NF are what interviews ask and what production schemas use. 4NF/5NF deal with multi-valued and join dependencies and show up rarely.

### The forms
| Form | Rule | Violation example | Fix |
|---|---|---|---|
| **1NF** | every column holds one atomic value; no repeating groups; rows are unique (there is a key) | `phones = '+996 700..., +996 555...'`, or columns `phone1, phone2, phone3` | child table `customer_phones(customer_id, phone)` |
| **2NF** | 1NF + every non-key column depends on the **whole** primary key, not a part of it (only matters with composite keys) | `order_items(order_id, product_id, qty, product_name)`: `product_name` depends on `product_id` alone | move `product_name` to `products` |
| **3NF** | 2NF + no transitive dependency: non-key columns do not depend on other non-key columns | `employees(id, department_id, department_name)`: `department_name` depends on `department_id`, not on `id` | move `department_name` to `departments` |
| BCNF | every determinant is a candidate key | overlapping composite candidate keys | split; rare |

- Mnemonic for 3NF: every non-key column depends on *the key, the whole key, and nothing but the key*.
- A `jsonb` column is a deliberate 1NF violation; fine for data you never filter on relationally (settings, raw payloads), bad for anything you join or update field by field.

### Denormalization
- Consciously bringing the schema out of normal form to speed up reads, at the price of redundancy and extra write logic.
- Typical forms:
  - **Copied columns**: `orders.customer_name` snapshotted at order time (this one is also *correct* semantically: the invoice should show the name as it was).
  - **Pre-aggregated counters**: `posts.comment_count`, `accounts.balance` instead of `sum(transactions)`. Must be updated in the same transaction as the source rows, or by a trigger.
  - **Flattened read models / materialized views** for dashboards and search (see [[database-fundamentals]]).
  - **Wide tables** for analytics (star schema: fact table + dimension tables) where joins at query time are too slow.
- When: measured read latency on a join-heavy query that [[database-indexes]] cannot fix; read:write ratio strongly skewed to reads; reporting workloads. Not before you have a slow query to show.
- How to keep it consistent: same transaction ([[spring-transactional]]), DB trigger, or an async projection fed by events (the [[outbox-pattern]]) when eventual consistency is acceptable; a periodic reconciliation job catches drift.

## Example
```sql
-- 3NF: each fact once
CREATE TABLE customers (
    id         bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    msisdn     varchar(15) NOT NULL UNIQUE,
    full_name  text NOT NULL
);
CREATE TABLE tariffs (
    id    bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    code  varchar(32) NOT NULL UNIQUE,
    price numeric(12,2) NOT NULL
);
CREATE TABLE subscriptions (
    id           bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    customer_id  bigint NOT NULL REFERENCES customers(id),
    tariff_id    bigint NOT NULL REFERENCES tariffs(id),
    activated_at timestamptz NOT NULL DEFAULT now(),
    -- deliberate denormalization: price frozen at activation, a business snapshot
    price_at_activation numeric(12,2) NOT NULL
);

-- deliberate denormalization: counter maintained in the same transaction
ALTER TABLE customers ADD COLUMN active_subscriptions int NOT NULL DEFAULT 0;
-- in the service: INSERT INTO subscriptions ...; UPDATE customers SET active_subscriptions = active_subscriptions + 1 WHERE id = ?;
```

## Pitfalls
- Over-normalizing lookup values (a `genders` table with two rows) adds joins for nothing; a `CHECK` constraint or enum column is enough.
- Denormalized counters updated outside a transaction drift; add a reconciliation query and run it in CI against a copy of production.
- Snapshot columns (`price_at_activation`) are often mistaken for "bad denormalization" and removed, which then breaks historical invoices.
- Normalization says nothing about performance; a 3NF schema with no indexes is still slow, see [[database-indexes]].
- ORMs push toward entity-per-table 3NF; for read models prefer DTO projections or views rather than denormalized entities, see [[spring-data-repositories]].
