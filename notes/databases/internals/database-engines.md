---
title: Database engines (storage engines)
tags: [databases, storage-engines, innodb, rocksdb, lsm]
learned: 2026-10-30
order: 6
---
## TL;DR
A **database engine** (storage engine, embedded database) is the library that actually stores bytes on disk and does CRUD: it owns pages, indexes, transactions and crash recovery. A **DBMS** (MySQL, MariaDB, CockroachDB) is the server around it: SQL parser, planner, network protocol, users, replication. Some DBMSs let you pick the engine per table (MySQL: InnoDB, MyISAM, MyRocks); others have one built in (Postgres). Engines split into two families: **B+tree** engines (InnoDB, SQLite, BerkeleyDB, WiredTiger) optimized for reads, and **LSM-tree** engines (LevelDB, RocksDB) optimized for writes.

## Key points
### DBMS vs engine vs embedded database
- The engine exposes a low-level API: `get(key)`, `put(key, value)`, iterators, transactions. No SQL, no network.
- "Embedded" means the engine runs inside your process as a library: no client-server hop. SQLite, RocksDB, LevelDB, BerkeleyDB are used this way directly.
- Many systems are a front end over an embedded engine: MySQL over InnoDB, MongoDB over WiredTiger, CockroachDB and TiKV over RocksDB/Pebble, Kafka Streams state stores over RocksDB.

### The engines
| Engine | Structure | Transactions | Notes |
|---|---|---|---|
| **MyISAM** | B-tree indexes, heap data file (`.MYD`) with offsets | no | original MySQL default; table-level locks; fast counts and inserts at the end; crashes can corrupt tables (needs repair); no foreign keys. Removed as default in MySQL 5.5 |
| **Aria** | MyISAM successor | partial | MariaDB's crash-safe replacement for MyISAM, used for internal temp tables |
| **InnoDB** | B+tree clustered by PK | full ACID, MVCC | MySQL default since 5.5; row-level locks, foreign keys, redo + undo logs, doublewrite buffer ([[primary-vs-secondary-indexes]]) |
| **XtraDB** | InnoDB fork | full | Percona's enhanced InnoDB; was MariaDB's default until 10.2, then MariaDB returned to InnoDB |
| **SQLite** | B+tree, one file | full ACID (WAL mode) | embedded SQL database; most deployed database in the world (phones, browsers); one writer at a time |
| **BerkeleyDB** | B-tree / hash | yes | old embedded key-value engine (1990s); was a MySQL engine; Oracle owns it now |
| **LevelDB** | LSM tree | atomic batches only | Google, 2011, by Jeff Dean and Sanjay Ghemawat; single process, levels of SSTables |
| **RocksDB** | LSM tree | yes | Facebook's LevelDB fork (2012): multithreaded compaction, column families, transactions; used in MyRocks, Kafka Streams, CockroachDB (until Pebble), TiKV, Flink |
| **WiredTiger** | B+tree (also LSM option) | yes, MVCC | MongoDB's engine since 3.2 ([[mongodb-basics]]) |

### B+tree vs LSM tree
- **B+tree** updates pages in place ([[b-tree-vs-b-plus-tree]]): reads are 3–4 page reads, writes are random IO plus WAL.
- **LSM tree** (log-structured merge): writes go to a WAL and an in-memory sorted table (**memtable**); when full, it is flushed to an immutable sorted file (**SSTable**) on level 0. Background **compaction** merges SSTables into larger levels, dropping overwritten and deleted keys (tombstones).
- Reads check memtable, then each level; **Bloom filters** per SSTable skip files that cannot contain the key ([[bloom-filters-and-probabilistic]]).
- Trade-off: LSM gives sequential writes and high write throughput, good compression, but reads may touch several files (read amplification) and compaction rewrites data many times (write amplification, [[wal-redo-undo-and-durability]]). B+tree gives predictable reads.
- Rule of thumb: write-heavy, append-mostly, time-series or key-value workloads → LSM; read-heavy OLTP with range queries → B+tree. Cassandra and ScyllaDB are LSM too ([[column-and-wide-column-stores]]).

### Switching engines in MySQL
- `ENGINE=` per table; `ALTER TABLE t ENGINE=InnoDB` rebuilds the table in the new engine.
- Each engine keeps its own files and features: a MyISAM table ignores `BEGIN/ROLLBACK` silently, so a transaction mixing InnoDB and MyISAM tables is only half transactional.
- MyRocks (MySQL with RocksDB) saved Facebook about half the storage of InnoDB for its user database thanks to compression and less fragmentation.

## Example
```sql
-- MySQL: which engines does this server have, and what does each table use?
SHOW ENGINES;
SELECT table_name, engine FROM information_schema.tables WHERE table_schema = 'shop';

CREATE TABLE audit_log (id BIGINT AUTO_INCREMENT PRIMARY KEY, msg TEXT) ENGINE=MyISAM;
START TRANSACTION;
INSERT INTO audit_log(msg) VALUES ('test');
ROLLBACK;                          -- the row is still there: MyISAM has no transactions
ALTER TABLE audit_log ENGINE=InnoDB;  -- rebuild as transactional
```

## Pitfalls
- Leftover MyISAM tables in an InnoDB schema: no transactions, no FKs, table locks, corruption after crashes.
- Picking RocksDB for a read-heavy OLTP workload with many range scans; compaction stalls and read amplification hurt latency.
- Ignoring compaction settings in LSM stores: write stalls when level 0 fills up faster than compaction drains it.
- Embedding SQLite in a multi-writer server: one writer at a time; fine for apps, edge and tests, not for a busy shared backend.
- Related: [[how-tables-and-indexes-are-stored]], [[storage-choices]], [[sql-vs-nosql-choosing]].
