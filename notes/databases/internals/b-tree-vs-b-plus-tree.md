---
title: B-tree vs B+tree in database systems
tags: [databases, indexes, b-tree, data-structures, storage]
learned: 2026-10-30
order: 4
---
## TL;DR
A database index must find one key among billions with very few page reads. A **B-tree** does it with wide nodes (one node = one page, hundreds of keys per node), so the tree is only 3–4 levels deep. The original B-tree stores keys **and values** in every node, which wastes internal nodes on data and makes range scans jump up and down the tree. A **B+tree** keeps only keys in internal nodes (so they hold more keys and fit in memory) and puts all values in the leaves, which are linked in a list, so a range scan is "find the first key, then walk the leaves". Practically every relational index (Postgres, InnoDB, SQL Server, Oracle, SQLite) is a B+tree.

## Key points
### Why not a full table scan
- Without an index, finding a row means reading every page of the heap ([[how-tables-and-indexes-are-stored]]). Even in parallel across workers, that is O(n) IO.
- A tree turns this into O(log n), and the base of the log is huge: with ~400 keys per 8 KB page, 3 levels address 400³ = 64 million keys and 4 levels 25 billion.

### The original B-tree
- A B-tree of **degree m** (max children per node): each node has up to m children and m−1 sorted keys; internal nodes (except the root) are at least half full, so the tree stays balanced; all leaves are at the same depth.
- Each node is an element list: **key + value** pairs plus child pointers. The value is the row pointer (tuple id or the row itself).
- Search: start at the root page, binary-search the keys, follow the pointer between the two keys that bound the search key, repeat. Each step = one page read.
- Insert: put the key into its leaf; if the leaf overflows, **split** it in two and push the middle key up into the parent; splits can cascade up and grow the root.
- Delete: remove the key; if a node drops below half full, borrow from a sibling or merge.

```anim
btree-search-split
```

### Limitations of the original B-tree
- **Values in internal nodes**: each internal node stores keys *and* values, so fewer keys fit in a page. Fewer keys per node = more levels = more IO. With a big value (a whole row in a clustered table) internal nodes hold very few keys.
- **Range queries are expensive**: `WHERE id BETWEEN 1 AND 1000` must traverse up and down the tree because keys of the range are spread across levels. Each jump is a random page read.
- Internal nodes are hard to keep in memory because they are large.

### The B+tree
- Internal nodes store **only keys** (separators) and child pointers. More keys per page → shallower tree, and all internal levels usually fit in RAM.
- **Leaves store all keys and values**; a key may appear twice (in an internal node as a separator and in its leaf).
- Leaves are linked (doubly in most engines): a range scan descends once to the first key and then reads leaf after leaf sequentially. `ORDER BY key` and `LIMIT` come for free.
- Every search ends in a leaf, so cost is predictable: tree height + leaf reads.

| | B-tree | B+tree |
|---|---|---|
| Internal nodes | keys + values | keys only |
| Values | anywhere | leaves only |
| Keys per internal page | fewer | more → shallower tree |
| Range scan | traverse the tree | descend once, walk linked leaves |
| Duplicate keys | no | separator keys repeated in leaves |
| Used by | rarely in modern DBs (some MongoDB docs call WiredTiger's B+tree a "B-tree") | Postgres, InnoDB, SQL Server, Oracle, SQLite |

### DBMS considerations
- **Node = page**: the B+tree node size equals the database page (8 KB Postgres, 16 KB InnoDB), so one node read is one IO.
- Internal nodes fit in memory, often all of them; leaves are read from the buffer pool or disk. A 3-level index with cached internal levels costs one IO per lookup.
- The leaf linked list makes range scans sequential, and sequential IO is far cheaper than random on HDD and still cheaper on SSD.
- Keep keys small: a `bigint` key (8 B) fits roughly twice as many entries per page as a 16-byte UUID, and 4–5× more than a 36-character string. Wider key = taller tree = more memory.
- Page splits leave half-empty pages; ever-increasing keys append to the right-most leaf and fill pages completely (Postgres and InnoDB optimize this case). Random keys (UUIDv4) cause splits everywhere ([[primary-vs-secondary-indexes]]).
- **Fill factor** leaves free space in each leaf for future updates (Postgres `fillfactor`, default 90 for B-tree indexes).

### Storage cost: MySQL vs Postgres
- **InnoDB**: the table is the PK B+tree, leaves contain full rows. Secondary index leaves contain the PK value, so a big PK (UUID string) inflates every secondary index.
- **Postgres**: all indexes, including the PK, store the 6-byte ctid in leaves, so index size does not depend on the PK type, but the heap is separate and every new row version needs new index entries.
- Postgres also deduplicates equal keys in B-tree leaves (since 13): one key with a list of ctids, which shrinks indexes on low-cardinality columns.

## Example
```sql
-- Postgres: inspect the tree with pageinspect
CREATE EXTENSION IF NOT EXISTS pageinspect;
SELECT * FROM bt_metap('orders_pkey');            -- root page, level (height - 1)
SELECT itemoffset, ctid, data
FROM bt_page_items('orders_pkey', 1) LIMIT 5;     -- leaf entries: key bytes → ctid

-- range scan: one descent, then leaf pages in order
EXPLAIN SELECT id FROM orders WHERE id BETWEEN 1000 AND 2000 ORDER BY id;
--  Index Only Scan using orders_pkey ... (no Sort node: leaves are already ordered)
```

## Pitfalls
- Calling every database index "a B-tree": the structure in Postgres and InnoDB is a B+tree; the distinction explains range scans and memory use.
- Wide index keys (long strings, many columns) make the tree taller and less cache friendly; index a hash or a prefix if you only need equality.
- Random inserts fragment the index; `REINDEX CONCURRENTLY` (Postgres) or `OPTIMIZE TABLE` (InnoDB) compacts it.
- B+trees are read-optimized; for write-heavy workloads LSM trees trade read cost for sequential writes ([[database-engines]]).
- Related: [[trees-and-bst]], [[database-indexes]], [[postgres-indexes]].
