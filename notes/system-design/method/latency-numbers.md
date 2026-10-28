---
title: Latency numbers — the ladder from L1 cache to cross-region and what it implies
tags: [system-design, performance, latency, interview]
learned: 2026-10-27
---
## TL;DR
Latency grows roughly ten times per rung of the ladder: nanoseconds inside the CPU, hundreds of nanoseconds for RAM, tens of microseconds for an SSD, hundreds of microseconds for a round trip inside a data centre, milliseconds for a database query, tens to hundreds of milliseconds across continents. Knowing the rungs lets you add up a request's **latency budget** and see where it is spent: one cross-region call costs more than a thousand Redis reads, and a chatty N+1 pattern over the network is the most common reason a p99 explodes.

## Key points

### The ladder (2026-era hardware, order of magnitude)
| Operation | Time | Same unit (µs) |
|---|---|---|
| L1 cache hit | 1 ns | 0.001 |
| Branch mispredict | 3 ns | 0.003 |
| L2 cache hit | 4 ns | 0.004 |
| Mutex lock/unlock, uncontended | 20 ns | 0.02 |
| Main memory (DRAM) read | 100 ns | 0.1 |
| Compress 1 KB (LZ4/Snappy) | 2 µs | 2 |
| NVMe SSD random 4 KB read | 20–100 µs | 20–100 |
| Read 1 MB sequentially from RAM | 10 µs | 10 |
| Read 1 MB sequentially from NVMe | 0.3–1 ms | 300–1 000 |
| Round trip inside one data centre | 0.2–0.5 ms | 200–500 |
| Redis GET, same DC | 0.2–1 ms | 200–1 000 |
| Postgres indexed point read, warm cache | 0.5–2 ms | 500–2 000 |
| Postgres commit with `fsync` to NVMe | 1–3 ms | 1 000–3 000 |
| Kafka produce, `acks=all`, 3 replicas | 2–10 ms | 2 000–10 000 |
| HDD seek | 5–10 ms | 5 000–10 000 |
| Round trip between AZs in one region | 1–2 ms | 1 000–2 000 |
| Round trip Bishkek ↔ Frankfurt | 70–90 ms | 70 000–90 000 |
| Round trip Europe ↔ US East | 80–100 ms | 80 000–100 000 |
| Round trip Europe ↔ Singapore | 150–200 ms | 150 000–200 000 |
| TLS 1.3 handshake, new connection, same region | 1 RTT + CPU ≈ 2–5 ms | — |
| JVM GC pause (G1, young) | 5–50 ms | — |

Rules that follow from the table:
- **RAM is 100× faster than SSD, SSD is 100× faster than a network hop, a cross-continent hop is 100× slower than an in-DC one.** Each boundary crossed costs two orders of magnitude.
- **Sequential beats random**: 1 MB sequential from NVMe (~1 ms) vs 250 random 4 KB reads (~10 ms). This is why Kafka and WAL-based databases are fast: they append ([[kafka-architecture]], [[postgres-indexes]]).
- **Light speed is a floor**: ~200 km per millisecond in fibre; a 4 000 km path is ≥ 20 ms one way, ≥ 40 ms RTT before any switch or queue. No optimisation fixes geography; only moving data closer does ([[cdn]], [[multi-region-basics]]).
- **Connections are expensive**: TCP + TLS setup is several RTTs, so pool connections ([[sockets-and-connection-pooling]], [[tls-handshake-and-certificates]]). HTTP/2 multiplexing helps the same way ([[http-1-1-vs-2-vs-3]]).

### Latency budget: adding up a request
A request's latency is the sum of the sequential hops plus the maximum of the parallel ones, plus queueing. Write it down per endpoint when the SLO is tight ([[requirements-and-constraints]]):

```text
GET /v1/subscribers/{id}/notifications   target p99 = 300 ms, client in Bishkek, service in Frankfurt
  client → edge (Bishkek→Frankfurt RTT)         80 ms   (already 27 % of the budget, and unavoidable)
  TLS resumed (0-RTT / session ticket)            0 ms   (new connection would add 1 RTT = +80 ms)
  LB → service, in DC                            0.5 ms
  auth: JWT verify in process                    0.1 ms  (an introspection call to an IdP would add 2–5 ms)
  Redis GET prefs                                 1 ms
  Postgres: SELECT last 20 by (user_id, created_at DESC) via index, warm   2 ms
  JSON serialisation 10 KB                        0.5 ms
  response 10 KB over the WAN ≈ 1 RTT + transfer  80 ms
  ------------------------------------------------------
  p50 ≈ 165 ms; p99 adds queueing + GC + a cold DB page: ≈ 220–250 ms → fits, barely.
  Any sequential extra call to another service (+1 ms in DC, +80 ms if cross-region) must be parallelised.
```

Interview-grade implications:
- **Tail latency amplification**: if one request fans out to 10 services each with a 1 % chance of being slow, 10 % of user requests are slow. Fewer hops, hedged requests or tighter per-hop p99 are the fixes ([[failure-modes-and-timeouts]]).
- **N+1 over the network** is the dominant real-world bug: 100 sequential Redis GETs = 100 ms; one `MGET` = 1 ms ([[jpa-n-plus-one]] is the ORM version of the same mistake).
- **Synchronous replication across regions** adds a full WAN RTT to every commit (70–100 ms); within a region it adds ~1–2 ms. That single number explains most active/active vs active/passive decisions ([[leader-follower-replication]]).
- **Caching wins by rungs, not percentages**: moving a read from Postgres (2 ms) to Redis (0.5 ms) is 4×; moving it to an in-process cache (100 ns) is 20 000× ([[caching]]).
- **Timeouts must sit above the real p99 of the hop**, not above the mean: a 50 ms timeout on a call whose p99 is 60 ms converts 1 % of traffic into retries and a self-inflicted storm.

### Throughput from latency
Little's law: `concurrency = throughput × latency`. A pod with 200 request threads and a 100 ms average downstream call handles at most `200 / 0.1 s = 2 000 rps`, whatever its CPU. Lower latency or non-blocking I/O ([[virtual-threads]], [[socket-management]]) raises the ceiling; so does a bigger pool, up to the database's connection limit ([[hikaricp-pool-sizing]]).

## Example
A Java micro-benchmark that makes the ladder visible on your own machine: in-process map vs Redis vs Postgres, each 1 000 sequential point reads. Run it once against a local Docker stack and keep the numbers in mind.

```java
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;

public class LatencyLadder {
    static long measureAvgNanos(String label, int n, Supplier<?> op) {
        for (int i = 0; i < 100; i++) op.get();                // warm-up: JIT, connection pools, DB cache
        long start = System.nanoTime();
        for (int i = 0; i < n; i++) op.get();
        long avg = (System.nanoTime() - start) / n;
        System.out.printf("%-28s avg %8d ns  (%.3f ms)%n", label, avg, avg / 1_000_000.0);
        return avg;
    }

    public static void main(String[] args) {
        var local = new ConcurrentHashMap<String, String>();
        local.put("prefs:42", "{\"sms\":true}");
        try (var jedis = new redis.clients.jedis.JedisPooled("localhost", 6379);
             var ds = pgDataSource()) {                       // HikariDataSource, pool size 1 is enough here
            jedis.set("prefs:42", "{\"sms\":true}");
            measureAvgNanos("in-process map get", 1_000, () -> local.get("prefs:42"));          // ~20–50 ns
            measureAvgNanos("redis GET (localhost)", 1_000, () -> jedis.get("prefs:42"));       // ~100–300 µs
            measureAvgNanos("postgres point SELECT", 1_000, () -> {
                try (var c = ds.getConnection();
                     var ps = c.prepareStatement("select prefs from subscriber where id = ?")) {
                    ps.setLong(1, 42L);
                    try (var rs = ps.executeQuery()) { return rs.next() ? rs.getString(1) : null; }
                } catch (java.sql.SQLException e) { throw new IllegalStateException(e); }
            });                                                                                  // ~300 µs–1 ms
        }
    }

    static com.zaxxer.hikari.HikariDataSource pgDataSource() {
        var cfg = new com.zaxxer.hikari.HikariConfig();
        cfg.setJdbcUrl("jdbc:postgresql://localhost:5432/app");
        cfg.setUsername("app"); cfg.setPassword("app"); cfg.setMaximumPoolSize(1);
        return new com.zaxxer.hikari.HikariDataSource(cfg);
    }
}
```

On a laptop the three lines differ by roughly 10 000× and 3×; on a real network add 0.2–0.5 ms to the last two. The point of the exercise is the ratio, not the absolute values.

## Pitfalls
- Quoting the table without units: "Redis is 1" means nothing; always say milliseconds or microseconds.
- Measuring averages. SLOs are about p99; a hop with a 1 ms mean and a 50 ms p99 (GC, lock contention, a cold page) is what breaks the budget. Watch histograms ([[grafana-dashboards-and-red-use]]).
- Ignoring the first request: cold JIT, empty connection pool, cold DB buffer cache and a fresh TLS handshake make the first call 10–100× slower; warm pools and keep-alives ([[sockets-and-connection-pooling]]).
- Assuming the cloud region is next door: a Bishkek user hitting Frankfurt pays ~80 ms per round trip; three sequential round trips already exceed a 200 ms SLO.
- Believing that faster hardware fixes a chatty design: 100 sequential 1 ms calls stay 100 ms on any machine; batching, parallel calls and caching are the real levers.
- Related: [[back-of-envelope-estimation]] for the capacity side; [[cpu-caches-and-cache-lines]] for the top rungs of the ladder.
