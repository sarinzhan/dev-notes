---
title: Database wire protocols (Postgres and MongoDB)
tags: [databases, protocols, postgres, mongodb, networking]
learned: 2026-10-30
order: 7
---
## TL;DR
A database driver talks to the server over TCP with a binary **wire protocol**: length-prefixed messages with a type byte. Postgres uses its own **frontend/backend protocol v3**: a startup and auth exchange, then either the **simple query** flow (one SQL string, text results) or the **extended query** flow (Parse/Bind/Execute/Sync: prepared statements, binary parameters, pipelining). MongoDB uses **OP_MSG** carrying BSON documents. Knowing the protocol explains prepared statement behaviour, why PgBouncer breaks some features, how SQL injection is avoided with bind parameters, and where network round trips come from.

## Key points
### Postgres protocol v3
- Every message: 1 type byte, 4-byte length (including itself), payload. The startup message has no type byte.
- **Startup**: client sends `StartupMessage` (protocol 3.0, `user`, `database`, `application_name`…). Optional `SSLRequest` first to upgrade to TLS ([[tls-handshake-and-certificates]]).
- **Authentication**: server replies `AuthenticationSASL` (SCRAM-SHA-256, the default since Postgres 14), `AuthenticationMD5Password` (legacy), or `AuthenticationOk` for trust. Then `ParameterStatus` messages (server_version, client_encoding, TimeZone…), `BackendKeyData` (pid + secret, used to cancel a query on a separate connection) and `ReadyForQuery` with the transaction status: `I` idle, `T` in transaction, `E` failed transaction.
- **Simple query**: `Query("SELECT ...")` → `RowDescription` (column names and type OIDs) → `DataRow` × n (values as text) → `CommandComplete("SELECT 3")` → `ReadyForQuery`. A string may contain several statements, which is exactly what SQL injection abuses.
- **Extended query**: `Parse` (SQL with `$1, $2`, optional name) → `Bind` (parameter values, text or binary; result formats) → `Describe` → `Execute` (with a row limit, so results can be fetched in chunks) → `Sync`. Parameters never get parsed as SQL, so they cannot inject. JDBC and most drivers use this flow.
- **Pipelining**: the client may send many Parse/Bind/Execute without waiting and one Sync at the end; the server replies in order. One round trip for a batch (JDBC batch, pgx pipeline, libpq pipeline mode since Postgres 14).
- **COPY** sub-protocol streams rows in bulk (`CopyData`), far faster than INSERTs.
- Errors come as `ErrorResponse` with fields: severity, SQLSTATE code (`23505` unique violation, `40001` serialization failure, `40P01` deadlock), message, detail, hint.
- Connection = one backend process on the server ([[postgres-architecture]]); TCP 5432.

### MongoDB protocol
- Message header: `messageLength`, `requestID`, `responseTo`, `opCode`. Since MongoDB 3.6 the main opcode is **OP_MSG** (opcode 2013); old OP_QUERY/OP_INSERT are removed in 5.1+.
- OP_MSG body: flag bits plus sections of **BSON** documents. A command is a document: `{ find: "orders", filter: { status: "NEW" }, limit: 10, $db: "shop" }`. Results come back as documents with a cursor id; `getMore` fetches the next batch ([[database-cursors]]).
- Section kind 1 carries a document sequence (e.g. many documents for a bulk insert) without wrapping them in one huge array.
- Handshake: `hello` command (formerly `isMaster`) returns topology info: primary, replica set members, max message size (48 MB), max BSON size (16 MB).
- TCP 27017; TLS and SCRAM / x.509 auth.

### Largest SQL statement you can send
- Postgres: a message length is a 32-bit integer, but the server limits a query string to about 1 GB (`MaxAllocSize`), and a single parameter value to 1 GB. A huge `IN (...)` list or a 100,000-row `VALUES` statement works but parses slowly and burns memory; the bind protocol limits parameters to **65,535** per statement (16-bit count).
- MySQL: `max_allowed_packet` (64 MB default in 8.0) limits a statement or row.
- Practical answer: send big data with COPY, batches of a few hundred to a few thousand rows, or `unnest($1::bigint[])` with one array parameter.

```java
// one array parameter instead of 50,000 placeholders
try (PreparedStatement ps = conn.prepareStatement(
        "SELECT id, status FROM orders WHERE id = ANY(?)")) {
    ps.setArray(1, conn.createArrayOf("bigint", ids.toArray()));
    try (ResultSet rs = ps.executeQuery()) { while (rs.next()) handle(rs); }
}
```

## Example
```bash
# watch the protocol: run a query and capture port 5432 (no TLS)
sudo tcpdump -i lo -A -s0 'tcp port 5432'
psql "host=127.0.0.1 sslmode=disable user=app dbname=shop" -c "SELECT 1"
# you will see: StartupMessage (user, database), 'R' auth, 'S' ParameterStatus,
# 'K' BackendKeyData, 'Z' ReadyForQuery, 'Q' SELECT 1, 'T' RowDescription,
# 'D' DataRow, 'C' CommandComplete "SELECT 1", 'Z' ReadyForQuery
```

## Pitfalls
- Concatenating values into SQL text (simple query) instead of binding them: SQL injection and no plan reuse.
- Named prepared statements through PgBouncer in transaction mode: the next transaction may run on another backend that does not have the statement (PgBouncer 1.21+ can track protocol-level prepared statements).
- Exceeding 65,535 bind parameters in one generated `INSERT ... VALUES`: the driver throws; split into batches.
- Fetching a huge result without a row limit or cursor: the driver buffers everything in memory ([[database-cursors]]).
- Related: [[network-access-to-databases]], [[sockets-and-connection-pooling]], [[tcpdump]], [[database-security-and-permissions]].
