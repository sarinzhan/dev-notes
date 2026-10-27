---
title: SQL command categories and join types
tags: [sql, joins, ddl, dml]
learned: 2026-10-22
---
## TL;DR
SQL statements fall into DDL (define structure: `CREATE`, `ALTER`, `DROP`), DML (change rows: `INSERT`, `UPDATE`, `DELETE`, with `SELECT` sometimes split out as DQL), DCL (permissions: `GRANT`, `REVOKE`) and TCL (`BEGIN`, `COMMIT`, `ROLLBACK`, `SAVEPOINT`). Joins combine rows of two tables by a condition: `INNER` keeps matches only, `LEFT`/`RIGHT` keep all rows of one side with `NULL`s for the missing other side, `FULL` keeps everything, `CROSS` produces every combination.

## Key points

### Command categories
| Group | Statements | Notes |
|---|---|---|
| **DDL** (data definition) | `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `RENAME` | In Postgres DDL is transactional: a failed migration rolls back the `CREATE TABLE` too (MySQL and Oracle auto-commit DDL). `TRUNCATE` is DDL-like: fast, resets the table, fires no row `DELETE` triggers |
| **DML** (data manipulation) | `INSERT`, `UPDATE`, `DELETE`, `MERGE` (Postgres 15+), `INSERT ... ON CONFLICT` (upsert) | Row changes, logged in WAL, subject to constraints and triggers |
| **DQL** (data query) | `SELECT` | Often filed under DML; reading only |
| **DCL** (data control) | `GRANT`, `REVOKE` | Privileges on tables, views, schemas, sequences; roles are created with `CREATE ROLE` (DDL) |
| **TCL** (transaction control) | `BEGIN`/`START TRANSACTION`, `COMMIT`, `ROLLBACK`, `SAVEPOINT`, `ROLLBACK TO SAVEPOINT`, `SET TRANSACTION ISOLATION LEVEL` | See [[transactions-acid-and-isolation]]; Spring issues these for you via [[spring-transactional]] |

- Logical order of a `SELECT`: `FROM` → `JOIN` → `WHERE` → `GROUP BY` → `HAVING` → `SELECT` → `DISTINCT` → `ORDER BY` → `LIMIT/OFFSET`. This is why a column alias from `SELECT` cannot be used in `WHERE`, and why a filter on a joined table in `WHERE` can turn a `LEFT JOIN` into an inner join.
- `DELETE` vs `TRUNCATE`: `DELETE` is row by row, can have `WHERE`, fires triggers, keeps the sequence; `TRUNCATE` drops all pages, can `RESTART IDENTITY`, needs an exclusive lock.

### Join types
| Join | Result rows | Typical question |
|---|---|---|
| `INNER JOIN` | only pairs where the condition matches | customers with at least one order |
| `LEFT [OUTER] JOIN` | all rows of the left table; right columns `NULL` when no match | all customers and their orders, including customers without orders |
| `RIGHT [OUTER] JOIN` | mirror of LEFT; rewrite as LEFT for readability | rarely used |
| `FULL [OUTER] JOIN` | all rows from both sides, `NULL`-padded | reconciliation: rows present on one side only |
| `CROSS JOIN` | every row of A × every row of B | generating combinations (dates × tariffs) |
| `SELF JOIN` | a table joined with itself | employee → manager in the same table |
| `LATERAL` join | right side can reference the left row (a correlated subquery in `FROM`) | "last 3 orders per customer" |
| semi/anti join | `WHERE EXISTS` / `WHERE NOT EXISTS` | filter by existence without duplicating rows |

- Anti-join idiom: `LEFT JOIN b ON ... WHERE b.id IS NULL` returns left rows without a match. `NOT IN (subquery)` returns nothing if the subquery contains a `NULL`; prefer `NOT EXISTS`.
- `USING (customer_id)` instead of `ON a.customer_id = b.customer_id` when column names match; `NATURAL JOIN` joins on all same-named columns and breaks silently when a column is added.
- Join algorithms the planner picks (visible in `EXPLAIN`): **nested loop** (small outer × indexed inner), **hash join** (build a hash of the smaller side; needs `work_mem`), **merge join** (both sides sorted on the key). You do not choose them; you provide [[database-indexes]] and accurate statistics.
- Joining many rows in SQL is almost always cheaper than fetching ids and looping in Java; that loop is the [[jpa-n-plus-one]] problem.

## Example
```sql
-- All customers, their active subscription (if any), and the last payment per subscription
SELECT c.id,
       c.msisdn,
       t.code                      AS tariff,
       lp.paid_at                  AS last_payment_at,
       lp.amount                   AS last_payment
FROM customers c
LEFT JOIN subscriptions s
       ON s.customer_id = c.id AND s.status = 'ACTIVE'   -- filter here, not in WHERE, to keep the LEFT
LEFT JOIN tariffs t
       ON t.id = s.tariff_id
LEFT JOIN LATERAL (
       SELECT p.paid_at, p.amount
       FROM payments p
       WHERE p.subscription_id = s.id
       ORDER BY p.paid_at DESC
       LIMIT 1
) lp ON true
WHERE c.created_at >= date '2026-01-01'
ORDER BY c.id;

-- Customers that never paid (anti-join)
SELECT c.id FROM customers c
WHERE NOT EXISTS (SELECT 1 FROM payments p WHERE p.customer_id = c.id);
```

## Pitfalls
- `LEFT JOIN b ... WHERE b.status = 'X'` silently discards the unmatched left rows (`NULL = 'X'` is not true); put the condition into `ON`.
- Joining a one-to-many and then `COUNT(*)` or `SUM` double-counts the parent; aggregate in a subquery or use `COUNT(DISTINCT ...)`.
- `SELECT *` across a join returns duplicate column names (`id`, `id`), which breaks JDBC `getLong("id")` and JPA native mappings.
- DDL in Postgres takes an `ACCESS EXCLUSIVE` lock on the table; `ALTER TABLE ... ADD COLUMN ... DEFAULT` is instant since PG 11, but adding a `NOT NULL` with a check still rewrites; `CREATE INDEX` needs `CONCURRENTLY` (see [[postgres-indexes]]).
- How keys make the join valid in the first place: [[constraints-and-relationships]]; what the schema should look like before joining: [[normalization-and-denormalization]].
