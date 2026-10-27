---
title: Cross-shard queries and transactions, scatter-gather and 2PC
tags: [distributed-systems, partitioning, transactions, 2pc]
learned: 2026-10-27
---
## TL;DR
Once data is sharded, two things become expensive: a **query** whose filter does not contain the shard key (it must be sent to every shard and merged: scatter-gather), and a **transaction** that touches rows on two shards (it needs an atomic commit protocol such as **two-phase commit**, which is slow and blocks on coordinator failure). The practical answer is to design the shard key so both are rare, serve the remaining cross-shard reads from a separate read model, and replace cross-shard writes with sagas or idempotent, eventually-consistent steps.

## Key points

### Scatter-gather reads
- The router fans the query out to all N shards in parallel, waits for all (or a quorum, for approximate results), and merges: concatenate, re-sort, re-apply `LIMIT`, re-aggregate.
- Latency = the slowest shard (tail latency amplification): with 100 shards and a p99 of 50 ms per shard, nearly every fan-out hits a 50 ms shard ([[latency-numbers]]).
- Pagination is the trap: `ORDER BY created_at LIMIT 20 OFFSET 1000` must fetch 1020 rows from *every* shard. Use keyset pagination (`WHERE created_at < :cursor`) so each shard returns only 20 ([[api-design-for-system-design]]).
- Aggregates: `COUNT`/`SUM` merge trivially; `AVG` needs sum and count per shard; `DISTINCT` and percentiles need all values or sketches ([[bloom-filters-and-probabilistic]]).
- Elasticsearch and Cassandra do this internally; a hand-rolled shard router needs the same merge logic and timeouts.

### Avoiding scatter-gather
- **Co-locate** everything one use case needs under one shard key (subscriber and all its SIMs and orders).
- **Global secondary index** or **lookup table**: `email → subscriber_id` on its own shard, then a single targeted query. Maintained asynchronously, so it can be briefly stale ([[sharding-strategies]]).
- **Read model** built from events: cross-tenant reports, admin search and analytics go to Elasticsearch, ClickHouse or a reporting replica fed by CDC/outbox ([[cqrs]], [[search-and-indexing-services]], [[outbox-pattern]]).

### Cross-shard transactions: two-phase commit
- Participants: a **coordinator** and the shards (**participants**). Phase 1 `PREPARE`: each participant writes its changes and a prepare record durably and votes yes/no. Phase 2 `COMMIT`/`ABORT`: the coordinator records the decision durably, then tells everyone. A participant that voted yes **must** wait for the decision; it cannot unilaterally abort.
- Guarantees atomicity across nodes. Costs: two round trips plus three fsyncs on the critical path; locks held across both phases; and the famous **blocking** problem: if the coordinator dies after participants prepared, they hold locks and in-doubt state until it comes back ([[failure-modes-and-timeouts]]).
- Postgres supports it (`PREPARE TRANSACTION`, `max_prepared_transactions`), Java has XA (`XADataSource`, JTA via Narayana/Atomikos); Kafka has no XA. Orphaned prepared transactions in Postgres block vacuum and hold locks indefinitely ([[postgres-vacuum-and-bloat]], [[postgres-locks]]).
- 3PC adds a pre-commit phase to reduce blocking but assumes bounded delays; nobody uses it. Modern systems (Spanner, CockroachDB, YugabyteDB) run 2PC *over Raft groups* so the coordinator state is itself replicated and cannot be lost ([[raft-explained]]).

### Alternatives to 2PC
| Approach | Atomicity | Isolation | When |
|---|---|---|---|
| **Avoid**: redesign the key so the write is single-shard | full (local tx) | full | first choice; most "cross-shard" needs are modelling mistakes |
| **Saga** (choreography or orchestration) | eventual, via compensations | none between steps | business workflows: order → payment → provisioning ([[saga]], [[temporal-concepts]]) |
| **Idempotent async steps** via outbox/events | eventual | none | denormalised copies, counters, projections ([[outbox-pattern]], [[idempotency]]) |
| **Single source of truth + eventual copy** | the SoT is atomic; copies converge | none for copies | one shard owns the fact, others cache it |
| **2PC / XA** | full | depends on participants | rare: two legacy DBs with no other option, JMS + JDBC |
| **Distributed SQL** (Spanner, CockroachDB) | full | serializable | when the vendor does the hard part and you accept the latency |

- A saga is not a transaction: a step can observe the partial result of another saga. Design for it with reservation states (`RESERVED` → `CONFIRMED`) rather than pretending isolation exists.

### Interview framing
- "Transfer money between two users on different shards?" Do not 2PC the balances. Append a transfer record on the sender's shard (debit + outbox event), consume it on the receiver's shard idempotently, mark the transfer complete; compensate on rejection. A ledger with immutable entries makes this natural ([[design-payment-processing]]).
- "Search users by name across shards?" Scatter-gather works for small N with bounded fan-out; at scale, a search index fed by events.

## Example
Scatter-gather with a bounded fan-out, a deadline, and keyset pagination, on top of the shard router from [[sharding-strategies]].
```java
public record Page<T>(List<T> items, Instant nextCursor) {}

public Page<OrderRow> recentOrdersAcrossTenants(Instant cursor, int size) {
    String sql = """
        SELECT id, tenant_id, created_at FROM orders
        WHERE created_at < ? ORDER BY created_at DESC LIMIT ?""";   // LIMIT per shard, no OFFSET

    List<CompletableFuture<List<OrderRow>>> calls = shards.values().stream()
        .map(ds -> CompletableFuture.supplyAsync(
            () -> new JdbcTemplate(ds).query(sql, ORDER_ROW, Timestamp.from(cursor), size), fanOutPool))
        .toList();

    List<OrderRow> merged = new ArrayList<>();
    for (var f : calls) {
        try { merged.addAll(f.get(800, TimeUnit.MILLISECONDS)); }       // deadline per shard
        catch (TimeoutException e) { f.cancel(true); partialResult.increment(); }  // degrade, don't fail
    }
    merged.sort(Comparator.comparing(OrderRow::createdAt).reversed());
    List<OrderRow> page = merged.subList(0, Math.min(size, merged.size()));
    Instant next = page.isEmpty() ? null : page.getLast().createdAt();
    return new Page<>(page, next);
}
```
Each shard returns at most `size` rows, the merge is a sort of N·size rows, and a slow shard produces a flagged partial page instead of a timeout for the whole request ([[completable-future]], [[executors-and-thread-pools]]).

## Pitfalls
- `OFFSET` pagination over shards: cost grows with page number × shard count; always keyset.
- XA "because the framework supports it": the first orphaned prepared transaction after a coordinator crash will teach you why it is avoided.
- Sagas without idempotent steps: a retried step double-charges; every step needs a key ([[idempotency]]).
- Unbounded fan-out thread pools: 50 shards × 200 concurrent requests = 10 000 blocked threads; use a bounded pool or virtual threads with a semaphore ([[virtual-threads]]).
- Related: [[sharding-strategies]] for choosing a key that keeps writes local, [[saga]] for the workflow replacement, [[transactions-acid-and-isolation]] for what you give up when leaving one node.
