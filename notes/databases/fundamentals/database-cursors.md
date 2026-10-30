---
title: Database cursors (server-side vs client-side)
tags: [databases, cursors, jdbc, postgres, streaming]
learned: 2026-10-30
---
## TL;DR
A query that returns 10 million rows should not be loaded into memory at once. A **server-side cursor** keeps the result open on the database server and hands it to the client in batches (`FETCH 1000`), so the client's memory stays small, at the cost of an open transaction and server resources. A **client-side cursor** (the default in most drivers) pulls the whole result over the network into the client's memory, then lets you iterate: simple and fast for small results, an `OutOfMemoryError` for big ones. In JDBC with Postgres you get a server-side cursor only with `autoCommit=false` and `fetchSize > 0`.

## Key points
### What a cursor is
- A cursor is a named pointer into a query's result. `DECLARE c CURSOR FOR SELECT ...` plans and starts the query; `FETCH n FROM c` returns the next n rows; `CLOSE c` releases it.
- The database does not materialize all rows first (for most plans): it produces rows as you fetch, so the first rows come quickly.
- In Postgres a cursor lives inside a transaction (unless `WITH HOLD`, which materializes the result at commit).

### Server-side cursor
- Pros: client memory proportional to the batch, not the result; you can stop early; streaming processing (exports, ETL, migrations).
- Cons: the transaction stays open as long as you iterate: it holds a snapshot, so VACUUM cannot clean dead rows created meanwhile ([[postgres-vacuum-and-bloat]]); the backend and its memory are tied up; more round trips (one per batch); does not work through PgBouncer in transaction mode across transactions.
- Use for: large reads processed sequentially by one consumer.

### Client-side cursor
- The driver executes the query and receives **all** rows before returning the first one. Iteration is local.
- Pros: one round trip (plus TCP streaming), the server is freed immediately, the transaction can end.
- Cons: memory = full result; time to first row = time to last row.
- Use for: small and medium results, which is most OLTP queries.

| | Server-side | Client-side |
|---|---|---|
| Client memory | one batch | whole result |
| Server resources | held during iteration | released after query |
| Transaction | open during iteration | can close |
| Round trips | one per batch | one |
| First row latency | low | after all rows arrive |

### Other cursor uses and types
- `UPDATE ... WHERE CURRENT OF c`: update the row the cursor is on.
- Scrollable cursors (`SCROLL`) can move backwards; `INSENSITIVE` vs `SENSITIVE`: whether changes made after open are visible.
- SQL Server has many server cursor types: **static** (copy of the result in tempdb), **keyset** (fixed set of keys, values re-read), **dynamic** (sees all changes) and **fast-forward** (read-only, forward-only, the cheapest). T-SQL row-by-row loops over cursors are a classic performance anti-pattern; set-based SQL is usually 10–100× faster.
- MongoDB queries always return a cursor: the first batch (101 documents or 16 MB), then `getMore` per batch; idle cursors time out after 10 minutes ([[database-wire-protocols]]).
- For paging through results in an API, use keyset pagination, not a cursor held between HTTP requests ([[pagination-offset-vs-keyset]]).

## Example
```sql
BEGIN;
DECLARE big_orders CURSOR FOR
  SELECT id, customer_id, amount FROM orders WHERE created_at >= '2026-01-01';
FETCH 1000 FROM big_orders;   -- rows 1..1000
FETCH 1000 FROM big_orders;   -- rows 1001..2000
CLOSE big_orders;
COMMIT;
```

```java
// JDBC + Postgres: server-side cursor needs autoCommit=false and a fetch size
try (Connection conn = dataSource.getConnection()) {
    conn.setAutoCommit(false);
    try (PreparedStatement ps = conn.prepareStatement(
            "SELECT id, customer_id, amount FROM orders WHERE created_at >= ?")) {
        ps.setFetchSize(1_000);                       // without this the driver loads everything
        ps.setObject(1, LocalDate.of(2026, 1, 1));
        try (ResultSet rs = ps.executeQuery()) {
            while (rs.next()) exporter.write(rs.getLong(1), rs.getLong(2), rs.getBigDecimal(3));
        }
    }
    conn.commit();
}
// Spring Data JPA: Stream<Order> findAllBy...() with @QueryHints(HINT_FETCH_SIZE) inside @Transactional(readOnly = true)
```

## Pitfalls
- `setFetchSize` with `autoCommit=true` on Postgres: silently ignored, the whole result is loaded.
- MySQL Connector/J streams only with `fetchSize = Integer.MIN_VALUE` or `useCursorFetch=true`.
- Keeping a cursor (and transaction) open for hours during a slow export: bloat and blocked VACUUM; process in keyset batches with short transactions instead.
- Slow per-row work inside the loop holds the backend; buffer to a queue or file and finish the read fast.
- Related: [[postgres-performance-checklist]], [[hikaricp-pool-sizing]], [[sockets-and-connection-pooling]].
