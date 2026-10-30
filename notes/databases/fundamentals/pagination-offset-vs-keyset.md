---
title: Pagination: OFFSET vs keyset
tags: [databases, sql, pagination, performance]
learned: 2026-10-30
---
## TL;DR
`LIMIT 10 OFFSET 100000` does not jump to row 100,000: the database reads and **throws away** 100,000 rows, then returns 10. Every next page gets slower, and rows inserted or deleted between requests shift the window so users see duplicates or miss rows. **Keyset pagination** (seek method, cursor pagination) remembers the last key of the previous page and asks for `WHERE id < :last_id ORDER BY id DESC LIMIT 10`: an index seek straight to the start of the page, constant cost on page 1 and page 10,000.

## Key points
### Why OFFSET is slow
- `ORDER BY id DESC OFFSET 1000 LIMIT 10` = fetch 1,010 rows through the index (and usually from the heap too), discard the first 1,000. The cost is O(offset + limit).
- On a big table, page 1 takes milliseconds, page 10,000 can take seconds; with `ORDER BY` on a non-indexed column the database sorts everything first.
- `EXPLAIN ANALYZE` shows it: the Limit node's child returns `offset + limit` rows ([[explain-analyze]]).

### Why OFFSET is wrong
- Between page 1 and page 2 a new row is inserted at the top: every row moves down by one, page 2 repeats the last row of page 1.
- A row is deleted: one row is skipped and never shown.
- Infinite scrolls and data exports suffer the most.

### Keyset (seek) pagination
- The client sends back the sort key of the last row it saw; the query starts **after** it: `WHERE id < 5001 ORDER BY id DESC LIMIT 10`.
- With an index on the sort key, the database descends the B+tree to 5001 and reads 10 entries ([[b-tree-vs-b-plus-tree]]). Cost O(log n + limit) for every page.
- Inserts and deletes do not shift pages: the next page always starts strictly after the last seen key.
- Non-unique sort keys need a tiebreaker: order by `(created_at, id)` and compare with a **row value**: `WHERE (created_at, id) < (:ts, :id)`, backed by an index on `(created_at, id)`.
- The API returns an opaque **cursor** (e.g. base64 of `created_at|id`) instead of a page number ([[api-design-for-system-design]]).

```anim
offset-vs-keyset
```

### Trade-offs
| | OFFSET | Keyset |
|---|---|---|
| Cost of page n | grows with n | constant |
| Stable under inserts/deletes | no | yes |
| Jump to page 57 | yes | no (only next/previous) |
| Total count / page numbers | easy (but `COUNT(*)` is itself expensive) | not natural |
| Arbitrary sort | any column | needs an index matching the sort + unique tiebreaker |

- OFFSET is fine for small tables, admin UIs and the first few pages. For feeds, APIs and exports, use keyset.
- Previous page: flip the comparison and the order (`WHERE id > :first_id ORDER BY id ASC LIMIT 10`), then reverse the result in the app.
- Batch processing (jobs over all rows) is keyset by nature: `WHERE id > :last ORDER BY id LIMIT 1000` in a loop, never OFFSET.

## Example
```sql
CREATE INDEX ON news (created_at DESC, id DESC);

-- page 1
SELECT id, title, created_at FROM news
ORDER BY created_at DESC, id DESC LIMIT 20;

-- next page: client sent the last row's (created_at, id)
SELECT id, title, created_at FROM news
WHERE (created_at, id) < ('2026-10-30 09:15:00+06', 48213)
ORDER BY created_at DESC, id DESC LIMIT 20;
```

```java
// Spring Data JPA: keyset scrolling (Spring Data 3.1+)
Window<News> page = repo.findFirst20ByOrderByCreatedAtDescIdDesc(ScrollPosition.keyset());
Window<News> next = repo.findFirst20ByOrderByCreatedAtDescIdDesc(page.positionAt(page.size() - 1));
```

## Pitfalls
- Keyset on a non-unique column without a tiebreaker: rows with the same value are skipped or repeated.
- No index matching the `ORDER BY`: keyset still sorts the whole table.
- `Pageable` in Spring Data runs an extra `COUNT(*)` query per page; use `Slice` or `Window` when you do not need totals ([[postgres-performance-checklist]]).
- Using OFFSET in batch jobs over millions of rows: quadratic total cost.
- Related: [[database-cursors]], [[design-news-feed]], [[database-indexes]].
