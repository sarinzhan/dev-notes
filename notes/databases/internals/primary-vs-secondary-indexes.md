---
title: Primary (clustered) vs secondary indexes
tags: [databases, indexes, innodb, postgres, uuid]
learned: 2026-10-30
order: 3
---
## TL;DR
A **clustered (primary) index** *is* the table: rows are stored in the index's leaf pages, sorted by the key. A **secondary index** is a separate structure whose entries point back to the row. InnoDB (MySQL) and SQL Server cluster the table by its primary key, and secondary indexes store the **primary key** value, so a lookup via a secondary index goes through two B+trees. Postgres has no clustered tables: every index, including the primary key, is secondary and points to the heap by **ctid**. This one difference explains why random UUID keys hurt MySQL more, why Postgres updates touch all indexes, and why Uber once moved from Postgres to MySQL.

## Key points
### Clustered / primary key (InnoDB, SQL Server)
- The table is organized around the primary key: leaf pages of the PK B+tree hold full rows, in key order. One table, one clustered index.
- Range queries by PK (`WHERE id BETWEEN 1000 AND 2000`) read consecutive pages: very fast.
- The insert position is decided by the key: sequential keys append to the last page; random keys land in the middle and cause **page splits**, half-empty pages and random IO.
- If you do not declare a PK, InnoDB uses the first unique NOT NULL index, or adds a hidden 6-byte row id.
- SQL Server: the clustered index can be on any column(s), not necessarily the PK; a table without one is a heap. Choose a narrow, unique, ever-increasing, rarely-updated key (the classic advice for clustered index design).

### Secondary index
- Entries are `secondary key → pointer`. What the pointer is depends on the engine:
  - **InnoDB**: the primary key value. Lookup = secondary B+tree → PK value → PK B+tree → row. Two tree descents. A wide PK (UUID string, composite) makes every secondary index wider.
  - **Postgres**: the ctid `(page, slot)` of the row version in the heap. Lookup = index → heap page directly.
- InnoDB's choice means rows can move (page splits) without touching secondary indexes. Postgres's choice means a lookup is one hop, but every new row version (each UPDATE writes one) gets a new ctid, so **every index must get a new entry**, unless the update is HOT ([[postgres-architecture]]).

### Postgres vs MySQL, the fundamental difference
| | Postgres | MySQL InnoDB |
|---|---|---|
| Table storage | heap, unordered | clustered by PK |
| Secondary index points to | ctid (physical) | PK value (logical) |
| Lookup by secondary key | index → heap | index → PK tree → row |
| UPDATE of one column | new tuple; all indexes updated unless HOT | in place; only indexes on changed columns updated; old version to undo log |
| Range scan by PK | index scan + random heap reads (unless correlated) | sequential leaf pages |
| Cost of a random PK | index bloat, scattered heap | page splits in the table itself |

- **Write amplification**: in Postgres a one-column update on a table with 10 indexes may write 11 places plus WAL. This is the core of Uber's 2016 post on moving from Postgres to MySQL (along with replication of physical WAL and upgrades); Postgres added HOT updates long ago and keeps improving, but the trade-off is real ([[wal-redo-undo-and-durability]]).

### UUIDs as keys
- Random UUIDv4 values are spread over the whole key space. Every insert hits a random leaf page of the B+tree, so the working set is the **whole index** instead of the right-most page. Once the index is bigger than memory, inserts become disk reads; page splits leave pages ~50–70 % full.
- In InnoDB this hits the table itself (clustered), plus each secondary index stores the 16-byte (or 36-char string) PK.
- Shopify moved from UUIDv4 to ULID-style time-ordered ids in a write-heavy MySQL table and saw large gains in insert throughput and buffer pool usage.
- Fixes: **UUIDv7** (time-ordered, RFC 9562, 2024) or ULID, or a `bigint` sequence internally and a UUID only as a public id; store UUIDs as `uuid`/`BINARY(16)`, never as `varchar(36)` ([[time-money-and-ids]]).

### Index scan vs index seek
- SQL Server vocabulary: **seek** = descend the B+tree to a key or range start (what Postgres calls an Index Scan with an index condition); **scan** = read all leaf pages of the index. Postgres "Index Scan" may be either, depending on whether there is an index condition.

## Example
```sql
-- InnoDB: the PK is the table; idx_email stores (email, id)
CREATE TABLE users (
  id        BINARY(16) PRIMARY KEY,           -- UUIDv7 bytes: time-ordered
  email     VARCHAR(255) NOT NULL,
  name      VARCHAR(100),
  KEY idx_email (email)
) ENGINE=InnoDB;
-- SELECT name FROM users WHERE email = ?  → idx_email → id → PK tree → row

-- Postgres: heap + separate PK index; every index points to a ctid
CREATE TABLE users (
  id    uuid PRIMARY KEY DEFAULT uuidv7(),     -- uuidv7() is built in from Postgres 18
  email text NOT NULL UNIQUE,
  name  text
);
```

## Pitfalls
- Random UUIDv4 primary keys on a large, write-heavy InnoDB table; use UUIDv7 or a sequence.
- A wide composite PK in InnoDB silently widens every secondary index.
- Adding "just one more index" on Postgres: every non-HOT update now writes it too.
- Expecting `CLUSTER` in Postgres to keep the order; it is a one-time rewrite under an exclusive lock.
- Related: [[how-tables-and-indexes-are-stored]], [[b-tree-vs-b-plus-tree]], [[database-indexes]], [[postgres-indexes]].
