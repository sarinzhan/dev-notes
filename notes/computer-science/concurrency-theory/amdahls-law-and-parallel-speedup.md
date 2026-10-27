---
title: Amdahl's law, parallel speedup and Little's law
tags: [concurrency, performance, amdahl, littles-law, capacity]
learned: 2026-10-27
---
## TL;DR
**Amdahl's law** says speedup from parallelism is capped by the fraction of work that stays serial: with 5% serial work, infinite cores give at most 20×. In practice the serial part includes locks, the GC, the database, the single Kafka partition, and coordination overhead that grows with thread count (Universal Scalability Law), so throughput can even fall as you add threads. **Little's law** (`L = λ × W`: items in system = arrival rate × time in system) is the tool for sizing: a pool handling 200 req/s at 50 ms each holds 10 requests on average, so 10 threads or connections is the floor, not 200. Together they answer "how many threads/pods/connections" and "why did doubling the pool not double throughput".

## Key points

### Amdahl's law
- `Speedup(n) = 1 / ((1 − p) + p / n)` where `p` is the parallelisable fraction and `n` the number of workers. Limit as `n → ∞` is `1 / (1 − p)`.
| Serial fraction | Max speedup | Speedup with 8 cores | with 64 cores |
|---|---|---|---|
| 1% | 100× | 7.5× | 39× |
| 5% | 20× | 5.9× | 15.4× |
| 10% | 10× | 4.7× | 8.8× |
| 25% | 4× | 2.9× | 3.7× |
| 50% | 2× | 1.8× | 2.0× |
- What counts as serial in a backend service: a `synchronized` block on a shared cache, a single-threaded Kafka consumer per partition, the Postgres primary for writes, a leader-only operation, stop-the-world GC pauses, `CompletableFuture.join()` waiting for the slowest fan-out call, a global sequence or rate limiter.
- **Gustafson's law** is the optimistic counterpart: if the problem grows with the cores (more requests, not a faster single request), the serial part shrinks relatively. Throughput scales better than latency; this is why horizontal scaling of stateless request handling works ([[horizontal-vs-vertical-scaling]]) while making one request faster by parallelising it hits Amdahl quickly.

### Universal Scalability Law
- Amdahl ignores coordination cost. Gunther's USL adds a term for **coherency**: `C(n) = n / (1 + α(n − 1) + βn(n − 1))` where `α` is contention (serial fraction) and `β` is crosstalk (cache-line ping-pong, lock convoying, consensus traffic, cross-node invalidation). With `β > 0` throughput peaks at some `n` and then **declines**: more threads make it slower.
- Symptoms of the `β` regime: adding pool threads increases p99 and CPU but not throughput; `futex` calls dominate `strace -c` ([[syscalls-and-file-descriptors]]); false sharing in a hot counter ([[cpu-caches-and-cache-lines]]); a database with 500 connections doing less work than with 50 ([[hikaricp-pool-sizing]]).
- Fix the `β`, not the `n`: shard the state (striped locks, `LongAdder`, per-partition consumers), batch to amortise coordination, go lock-free ([[lock-free-and-cas]]), or stop sharing ([[actor-and-message-passing-models]]).

### Little's law
- For any stable system (arrivals = departures over the window): `L = λ × W`. Items in the system = throughput × average time each item spends inside. No assumptions about distributions; applies to queues, pools, the whole service, a Kafka consumer group.
- Uses:
  - **Pool sizing**: 200 req/s × 0.05 s average service time = 10 busy workers on average. Size the pool at this plus headroom for variance (and never at "max concurrent users").
  - **Connections**: a service doing 300 queries/s with 20 ms average query time needs ~6 connections; HikariCP's default of 10 is usually right.
  - **Backlog**: queue depth 5 000 messages with consumer throughput 250 msg/s means a 20 s lag; to cut it to 2 s you need 10× throughput, i.e. more partitions and consumers ([[kafka-consumer]], [[backpressure-and-flow-control]]).
  - **Reverse**: measured in-flight requests (`L`) and throughput (`λ`) give the real average latency `W` even when you cannot instrument it.
- Pairs with the **utilisation law**: `ρ = λ × S` (arrival rate × service time per server, per server count). Above ~70–80% utilisation queueing delay grows sharply (M/M/1: `W = S / (1 − ρ)`), which is why "the pod is at 85% CPU and fine" turns into timeouts at 95%. Capacity planning targets utilisation, not 100% ([[back-of-envelope-estimation]], [[slo-sli-and-alerting]]).

### In Java terms
- `parallelStream()` over a small collection: fork overhead exceeds the work ([[fork-join-framework]]). Fan-out with `CompletableFuture.allOf`: latency becomes `max` instead of `sum`, and the slowest call is the new serial part ([[completable-future]]). Virtual threads raise `n` for IO-bound work but do nothing for the serial fraction ([[virtual-threads]]).

## Example
```java
// Size a worker pool from measured numbers with Little's law, then sanity-check against Amdahl.
// Measured: 240 orders/s arrive; one order takes 80 ms end to end (mostly waiting on SM-DP+ and Postgres),
// of which ~6 ms is CPU on this service.
double lambda = 240.0;            // arrivals per second
double w      = 0.080;            // seconds in system per order
double inFlight = lambda * w;     // L = 19.2 orders in the system on average

int workers = (int) Math.ceil(inFlight * 1.5);          // 29: headroom for burstiness (Poisson arrivals)
int cpuNeeded = (int) Math.ceil(lambda * 0.006);        // 1.44 cores busy -> a 2-CPU limit is tight at peak

// Amdahl check on the per-order path: parallelising the 3 downstream calls (each ~25 ms, run concurrently)
// cuts W from 80 ms to ~35 ms (serial part: 6 ms CPU + 4 ms DB commit + max(25 ms) = 35 ms).
// Max speedup of the order path = 80 / 35 = 2.3x, no matter how many threads you add.
```
With `W` at 35 ms the same 240 req/s needs only `L ≈ 8.4` in-flight workers: reducing time-in-system is worth more than enlarging the pool, and it also reduces connection and memory pressure.

## Pitfalls
- Sizing a thread pool or connection pool to "max concurrent users": Little's law says it is throughput × latency, usually an order of magnitude smaller; oversized pools raise `β` (contention) and slow everything.
- Measuring speedup on a dev laptop with 8 cores and extrapolating to 64: the `β` term shows up only at scale; test at production thread counts.
- Treating 100% CPU as the goal: at high utilisation queueing delay dominates latency; keep headroom and scale horizontally before saturation ([[cpu-scheduling]]).
- Forgetting that Little's law needs a stable system: during a backlog burn-down arrivals ≠ departures and the formula gives averages over a window, not instantaneous values.
- Parallelising the wrong part: speeding up a 10% step by 10× saves 9% of total time; profile first ([[latency-numbers]]).
- Related: [[hikaricp-pool-sizing]] for the connection-pool application, [[rate-limiting]] and [[load-balancing]] for where utilisation and queueing decide behaviour under load.
