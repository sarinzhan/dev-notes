---
title: WAL, redo and undo logs, and durability
tags: [databases, wal, durability, recovery, fsync]
learned: 2026-10-30
order: 5
---
## TL;DR
Durability means a committed transaction survives a crash. Writing every changed page to its place in the data files at commit would be slow (random IO, full pages for a few bytes), so databases write a **write-ahead log (WAL)** first: a sequential, append-only record of every change. Commit = the WAL record is flushed with `fsync`; the data pages are written later by a checkpoint. After a crash the database **redoes** the log from the last checkpoint. The **undo** log (InnoDB, Oracle) keeps old versions to roll back uncommitted work and to serve consistent reads; Postgres keeps old versions in the heap instead.

## Key points
### Durability techniques
- **WAL**: log the change, flush the log, then change pages in memory. Postgres WAL, InnoDB redo log, SQL Server transaction log.
- **Asynchronous snapshot**: write the whole dataset periodically in the background (Redis RDB). Cheap but loses what happened since the last snapshot.
- **Append-only file** (Redis AOF): log every command; replay on start. Same idea as WAL, at command level ([[redis-patterns-and-persistence]]).
- All of them depend on `fsync`: the OS page cache holds writes in RAM ([[storage-management]]), and `write()` returning does **not** mean the data is on disk. Only `fsync`/`fdatasync`/`O_DIRECT`+`O_DSYNC` gives that guarantee, and it is slow (milliseconds on HDD, tens to hundreds of µs on SSD with power-loss protection).

### Why a log
- The data files are organized for reading (heap pages, B+trees); a change touches random pages. Writing them all at commit = many random IOs, and a page write can be torn by a crash.
- The WAL is **sequential** and compact: one append per change, flushed once per commit. Group commit flushes many transactions in one `fsync`.
- Pages changed in memory are **dirty** and written later in the background; the WAL makes that safe.
- The WAL is also the replication stream: replicas replay it ([[postgres-replication-and-failover]]), and CDC tools (Debezium) read it.

### Redo
- Redo records describe how to re-apply a change ("on page 42 slot 7 set bytes ... to ..."). On recovery the database starts from the last **checkpoint** and replays every record whose effect may be missing from the data files.
- **Checkpoint**: flush all dirty pages up to some point, then mark that point in the log. Everything before it is no longer needed for crash recovery, so old WAL can be recycled. Frequent checkpoints = faster recovery, more IO; rare ones = less IO, longer recovery and more WAL on disk.
- **Full-page writes** (Postgres): the first change to a page after a checkpoint logs the whole page, so a torn half-written page can be restored. InnoDB uses a doublewrite buffer for the same reason.

### Undo
- Undo records describe how to reverse a change ("slot 7 used to contain ..."). Used to:
  - roll back an aborted transaction, and after a crash roll back transactions that never committed;
  - give MVCC readers the old version of a row (InnoDB builds older versions from the undo log; Oracle the same with rollback segments).
- A long-running transaction keeps undo alive, so the undo log grows, and readers walk long version chains: one reason long transactions hurt.
- **Postgres has no undo log**: an UPDATE writes a new tuple and leaves the old one in the heap, marked with transaction ids. Rollback is free (the new tuple is simply invisible), but dead tuples need VACUUM ([[postgres-vacuum-and-bloat]]).

```anim
wal-crash-recovery
```

### Write amplification
- One logical change can cause many physical writes: the WAL record, a full page image after a checkpoint, the heap page, each index page, the replica's copy, and on SSD the FTL's own rewrites ([[storage-management]]).
- Postgres: a non-HOT update writes a new tuple plus an entry in every index plus WAL for each ([[primary-vs-secondary-indexes]]). LSM engines: compaction rewrites data several times ([[database-engines]]).
- Reduce it: fewer indexes, HOT-friendly updates (fillfactor), batched writes, `wal_compression`, avoid updating unchanged columns.

### Durability knobs
| Setting | Effect |
|---|---|
| Postgres `synchronous_commit = on` (default) | commit waits for local WAL flush |
| `synchronous_commit = off` | commit returns before flush; a crash can lose the last ~3× `wal_writer_delay` (≈600 ms) of commits, never corrupts |
| `fsync = off` | never do this in production: a crash can corrupt the cluster |
| InnoDB `innodb_flush_log_at_trx_commit = 1` | flush redo on every commit (durable) |
| `= 2` | write to OS at commit, flush every second: survives a mysqld crash, not an OS crash |
| `= 0` | write and flush once per second |
| Redis `appendfsync always / everysec / no` | per command / once a second / when the OS decides |

## Example
```sql
-- Postgres: see WAL being produced by a statement
SELECT pg_current_wal_lsn();                 -- e.g. 0/3A2B1C8
UPDATE accounts SET balance = balance - 100 WHERE id = 1;
SELECT pg_current_wal_lsn();                 -- moved forward

-- trade a little durability for throughput in one session only (e.g. bulk import of re-creatable data)
SET synchronous_commit = off;

-- checkpoint timing
SHOW checkpoint_timeout;   -- 5min
SHOW max_wal_size;         -- 1GB: a checkpoint is also forced when this much WAL accumulates
```

## Pitfalls
- Trusting `write()` or a disk with a volatile write cache and no power-loss protection: the "fsyncgate" story ([[storage-management]]).
- Turning off `fsync` or full-page writes for speed and corrupting the database after a power cut.
- Long transactions: they pin undo (InnoDB) or dead tuples (Postgres) and slow everyone.
- `max_wal_size` too small: checkpoints every few seconds, huge IO spikes from full-page writes.
- Related: [[transactions-acid-and-isolation]], [[how-tables-and-indexes-are-stored]], [[postgres-architecture]].
