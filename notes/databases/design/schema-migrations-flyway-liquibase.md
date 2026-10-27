---
title: Schema migrations with Flyway and Liquibase, and zero-downtime changes
tags: [migrations, flyway, liquibase, postgres, deployment]
learned: 2026-10-27
---
## TL;DR
Schema changes are versioned files in the repository, applied in order by a tool that records what ran in a history table (`flyway_schema_history`, `databasechangelog`). Flyway is SQL files with a naming convention; Liquibase is changesets in XML/YAML/SQL with rollback support and database abstraction. The harder problem is not the tool but **zero-downtime**: during a rolling deploy old and new code run against the same schema, so every change must be backward compatible for one release. The **expand/contract** pattern (add the new thing, migrate data, switch code, remove the old thing in a later release) plus `lock_timeout` and `CONCURRENTLY` cover almost every case.

## Key points

### Flyway vs Liquibase
| | Flyway | Liquibase |
|---|---|---|
| Format | `V3__add_eid.sql`, plain SQL (Java migrations possible) | changesets in YAML/XML/JSON/SQL with `id`, `author`, preconditions |
| Ordering | version number; `R__` repeatable scripts (views, functions) re-run when their checksum changes | file order in the changelog; `runOnChange`, `runAlways` |
| Rollback | paid feature (`undo`); community practice is forward-only fixes | `rollback` block per changeset, `rollback-count` |
| DB portability | none, write Postgres SQL | abstracted change types (`addColumn`) or raw SQL |
| Spring Boot | starter, runs on startup before JPA; `spring.flyway.*` | starter, `spring.liquibase.*` |
- Both lock the history table so parallel pods do not run the same migration twice (Flyway uses `SELECT ... FOR UPDATE` on its history row; Liquibase a `databasechangeloglock` row that can get stuck after a crash and needs manual release).
- Never edit an applied migration: the checksum mismatch fails startup (`flyway repair` only for a genuine mistake). Write a new one.
- `ddl-auto: validate` with JPA in every environment above local: Hibernate checks the mapping against the migrated schema and never generates DDL ([[jpa-entities-and-relationships]]).

### Running migrations
- On application startup is fine for small services with one database; the first pod applies, others wait on the lock. Problems: a slow data migration blocks the readiness probe and the rollout; a failed migration leaves the pod crash-looping. For large changes run migrations as a separate job (Kubernetes `Job` or init step in the pipeline) before the rollout, with `spring.flyway.enabled=false` in the app.
- Separate **schema** migrations (fast, DDL) from **data** migrations (slow, batched, resumable). A data backfill of 50 million rows belongs in a batched job with `LIMIT` and commits per batch, not in one `UPDATE` inside a migration transaction.
- Migrations run with `lock_timeout` set (`SET lock_timeout = '5s';` as the first line, or `spring.flyway.init-sql`), so a DDL waiting behind a long transaction fails and is retried instead of queueing every query in the service ([[postgres-locks]]).
- Each Flyway migration runs in one transaction by default; `CREATE INDEX CONCURRENTLY` cannot, so that file gets `-- flyway:executeInTransaction=false` as its first line and nothing else in it.

### Expand/contract for zero downtime
During a rolling deploy version N and N+1 run side by side; the schema must work for both.
| Change | Dangerous way | Safe sequence |
|---|---|---|
| Rename column | `ALTER TABLE ... RENAME` breaks the old code immediately | 1 add new column; 2 code writes both, reads new with fallback; 3 backfill in batches; 4 code reads/writes new only; 5 drop old |
| Add `NOT NULL` column | `ADD COLUMN x NOT NULL` fails on existing rows; a volatile `DEFAULT` rewrites the table | add nullable (PG 11+ constant default is metadata only); backfill; `ADD CONSTRAINT ... CHECK (x IS NOT NULL) NOT VALID`; `VALIDATE CONSTRAINT` (no long lock); then `SET NOT NULL` (PG 12+ uses the validated check) |
| Change type (`int` → `bigint`) | `ALTER COLUMN TYPE` rewrites the table under `ACCESS EXCLUSIVE` | new column + dual write + backfill + swap, or accept a maintenance window for small tables |
| Add index | `CREATE INDEX` blocks writes | `CREATE INDEX CONCURRENTLY`, check for `INVALID` after failure ([[postgres-indexes]]) |
| Add foreign key | validates all rows under lock | `ADD CONSTRAINT ... NOT VALID`, then `VALIDATE CONSTRAINT` |
| Drop column | old pods still select it | deploy code that stops using it first; drop next release; JPA entities must not map it |
| Add enum value | `ALTER TYPE ... ADD VALUE` could not run in a transaction before PG 12 | use `text` + `CHECK`, or add the value in its own non-transactional migration |
- The contract step is a **separate release**, after the expand release has been fully rolled out and confirmed. Keeping a "to drop" list in the repository prevents forgotten dual-write code.
- Backward compatibility also applies to events and APIs that carry the renamed field ([[message-schema-evolution]]).

### Testing migrations
- Run the whole migration chain against a real Postgres in CI with [[testcontainers]]; a Hibernate `validate` on startup catches mapping drift. Test the expand step with the *previous* application version still running against the new schema when the change is risky.
- Keep a production-like dataset (anonymized dump) for timing data migrations; a backfill that takes 2 s on 10 000 rows takes an hour on 100 million and holds locks the whole time if written as one statement.

## Example
```sql
-- V14__add_eid_to_sim_expand.sql  (release N: expand)
SET lock_timeout = '5s';
ALTER TABLE sim ADD COLUMN eid text;                     -- nullable, instant
ALTER TABLE sim ADD CONSTRAINT sim_eid_format
    CHECK (eid ~ '^[0-9]{32}$') NOT VALID;               -- enforced for new rows only, no scan
```
```sql
-- V15__sim_eid_unique_index.sql  (release N, separate file: no transaction)
-- flyway:executeInTransaction=false
CREATE UNIQUE INDEX CONCURRENTLY sim_eid_uq ON sim (eid) WHERE eid IS NOT NULL;
```
```java
// Backfill as a batched job, not a migration: resumable, commits per batch, no long lock
@Transactional
public int backfillBatch() {
    return jdbc.update("""
        UPDATE sim SET eid = attrs->>'eid'
        WHERE id IN (SELECT id FROM sim WHERE eid IS NULL AND attrs ? 'eid'
                     ORDER BY id LIMIT 5000 FOR UPDATE SKIP LOCKED)
        """);
}
```
```sql
-- V18__sim_eid_contract.sql  (release N+1: contract, after all pods write eid)
SET lock_timeout = '5s';
ALTER TABLE sim VALIDATE CONSTRAINT sim_eid_format;      -- SHARE UPDATE EXCLUSIVE, writes continue
ALTER TABLE sim ALTER COLUMN eid SET NOT NULL;           -- requires every row to have eid
ALTER TABLE sim DROP COLUMN legacy_eid;
```

## Pitfalls
- `ALTER TABLE ... ADD COLUMN ... DEFAULT now()`: volatile default, full table rewrite under an exclusive lock; constant defaults are free since PG 11, volatile ones are not.
- A migration that mixes DDL and a multi-million-row `UPDATE`: the DDL lock is held for the duration of the update and the service stops.
- Running migrations from every pod on startup with a slow migration: the readiness probe times out, Kubernetes restarts the pod mid-migration, Liquibase's lock row stays locked.
- Dropping a column while the previous version is still receiving traffic, or while Hibernate's `SELECT` list still names it: `column does not exist` for the duration of the rollout.
- Lock mechanics behind every DDL: [[postgres-locks]]; the modeling conventions these migrations implement: [[data-modeling-for-services]]; release safety: [[database-scaling-path]] for when schema changes become sharding changes.
