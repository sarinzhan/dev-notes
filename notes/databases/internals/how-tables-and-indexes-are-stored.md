---
title: How tables and indexes are stored on disk
tags: [databases, storage, pages, heap, indexes]
learned: 2026-10-30
order: 1
---
## TL;DR
A table is not a list of rows on disk; it is a file of fixed-size **pages** (8 KB in Postgres, 16 KB in InnoDB), and each page holds many rows. The database never reads one row: it reads a whole page, so the cost of a query is the number of pages it touches. The **heap** is the unordered pile of pages that holds the table's rows. An **index** is a separate structure (a B+tree, also made of pages) that maps a key to the location of the row, so a query can read a few index pages plus one heap page instead of scanning the whole heap.

## Key points
### Row id and tuple id
- Every row has an internal address. In Postgres it is the **ctid** `(page number, slot in page)`, e.g. `(3, 7)`: page 3, item 7. In MySQL/InnoDB there is no separate address: the primary key is the address, because the table is stored inside the primary key B+tree ([[primary-vs-secondary-indexes]]).
- Users rarely see these ids, but every index entry points at one.

### Pages
- A page is the unit of IO and caching. Rows are packed into pages; a page header, a slot array (item pointers) at the front and the row data growing from the end. Free space sits in the middle.
- Reading row `(3, 7)` means: fetch page 3 into the buffer pool (from the OS page cache or the disk) and pick slot 7. The other ~100 rows in that page come for free; that is why scanning a table sequentially is fast and why rows that are queried together should sit together.
- Page size is fixed per database: Postgres 8 KB, InnoDB 16 KB, SQL Server 8 KB, Oracle configurable. Bigger pages: fewer IOs for scans, more wasted reads for point lookups.
- Values that do not fit in a page are moved out of line: TOAST in Postgres, overflow pages in InnoDB. Wide `jsonb` or `text` columns therefore cost an extra lookup.

### IO
- An IO is one read request to the disk; it returns at least one page, often several (read-ahead). Goal of every access path: **fewer IOs**.
- Pages are cached in the database's buffer pool (`shared_buffers`, `innodb_buffer_pool_size`) and in the OS page cache ([[storage-management]]). A "hot" page costs a memory access; a cold one costs a disk read.
- Why databases read pages and not single rows: the disk and the OS already work in blocks (4 KB or more), so reading less than a block is impossible, and neighbouring rows are usually needed next.

### The heap
- The heap is the table data with no particular order. Inserts go wherever there is free space (Postgres tracks it in the free space map).
- Finding a row in the heap without an index = **full table scan**: read every page. On 1 million rows of 100 bytes that is ~12,500 pages of 8 KB.
- In Postgres an UPDATE writes a new row version, possibly on another page; old versions stay until VACUUM ([[postgres-vacuum-and-bloat]]).

### Index
- An index is a separate data structure, usually a B+tree ([[b-tree-vs-b-plus-tree]]), whose entries are `key → row id`. It also lives in pages, so it costs IO too, but far fewer: 3–4 page reads find any key among billions.
- The index tells you **which page** to read; then one heap page read fetches the row. Without the index you would read every page.
- If the index itself contains all columns the query needs, the heap read is skipped: an index-only scan ([[postgres-indexes]]).
- Small indexes fit in memory; large ones fall out of the cache, which is when random index lookups start hitting disk.

```anim
heap-index-lookup
```

### Clustered tables (index-organized)
- Some engines store the heap **inside** an index, sorted by key: InnoDB tables (by primary key), SQL Server clustered indexes, Oracle IOTs. Range reads by the key are then sequential, but inserts with random keys split pages ([[primary-vs-secondary-indexes]]).
- Postgres tables are always heaps. `CLUSTER` reorders a table once, but the order is not maintained afterwards.

### NULLs
- In Postgres a row with any NULL column gets a null bitmap in its header (1 bit per column) and the NULL value itself takes no space. Many NULLs in a wide, sparse table shrink rows and so pack more rows per page.
- `NULL` is not equal to anything, including another `NULL`, so `WHERE x = NULL` never matches; use `IS NULL`. B-tree indexes in Postgres do store NULLs and can serve `IS NULL`.

## Example
```sql
-- where does a row physically live?
SELECT ctid, id, name FROM employees WHERE id = 42;     -- ctid = (0,42)

-- how many pages does the table and its index take?
SELECT relname, relpages, reltuples
FROM pg_class WHERE relname IN ('employees', 'employees_pkey');

-- buffers = pages touched; shared hit = from cache, read = from disk
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM employees WHERE id = 42;
--  Index Scan using employees_pkey ...  Buffers: shared hit=4
```

## Pitfalls
- Thinking in rows instead of pages: a query that returns 10 rows scattered over 10 pages costs 10 IOs; 10 rows on one page cost 1.
- `SELECT *` pulls wide TOASTed columns you do not need; list columns ([[row-vs-column-storage]]).
- A bloated heap (many dead row versions) means more pages for the same rows, so every scan gets slower ([[postgres-vacuum-and-bloat]]).
- Caching makes the first run slow and the second fast; measure with `BUFFERS` and look at `read`, not only time.
- Related: [[database-indexes]], [[storage-management]], [[wal-redo-undo-and-durability]].
