---
title: Leases, heartbeats and health checks
tags: [distributed-systems, reliability, kubernetes, kafka]
learned: 2026-10-27
---
## TL;DR
A **heartbeat** is a periodic "I am alive" message; a **lease** is a grant ("you own this partition / you are leader / this session is valid") that expires unless renewed by a heartbeat. Together they are how distributed systems detect liveness without a global clock or a reliable network: miss enough heartbeats and the lease expires, the resource is reassigned. The design tension is the same everywhere: a short lease detects failure fast but misfires on GC pauses and network blips; a long lease is stable but slow to recover. And a lease only tells the *others* you are gone; the holder must notice expiry itself and stop, which is why writes need fencing.

## Key points

### Heartbeats and failure detection
- Heartbeat interval I, timeout T (usually 3–10 × I). A node is **suspected** after T without heartbeats; suspected is not dead, the heartbeat may be delayed by a pause or a partition ([[failure-modes-and-timeouts]]).
- Directions: node → coordinator (Kafka consumer → group coordinator, kubelet → API server), leader → followers (Raft `AppendEntries`), peer-to-peer gossip (Cassandra, Consul).
- **φ-accrual** detectors replace the fixed T with a suspicion level derived from observed inter-arrival times.
- A heartbeat thread keeps running while the worker thread is deadlocked, so the node looks alive and does nothing. Kafka's `max.poll.interval.ms` exists for this: heartbeats run in the background, but `poll()` must still be called in time.

### Leases
- Lease = ownership + expiry. The grantor (coordinator, consensus store, database row) hands out a lease with a TTL; the holder renews before expiry; on expiry the grantor reassigns.
- **Clock assumption**: the holder's clock and the grantor's clock drift at a bounded rate, and the holder gives up at `expiry − safetyMargin` by *its* clock. Google's Chubby and etcd both build on this ([[time-and-clocks]]).
- **The holder's problem**: after a 20 s GC pause, the holder's lease expired 10 s ago and someone else holds it. The holder cannot know this until it next talks to the grantor. Therefore any write under a lease must carry a **fencing token** the resource verifies ([[distributed-locks-and-fencing]]).
- Lease vs lock: a lock without a TTL is held forever by a crashed process; a lease is a lock that frees itself. The TTL is the price: liveness in exchange for a window of possible double ownership.

### Where leases live in the stack
| System | Lease / heartbeat | Interval / TTL defaults | On expiry |
|---|---|---|---|
| Kafka consumer group | `heartbeat.interval.ms` → `session.timeout.ms`; `max.poll.interval.ms` for processing | 3 s / 45 s; 5 min | consumer removed, partitions rebalanced ([[kafka-consumer]]) |
| Kafka ISR | follower fetch recency `replica.lag.time.max.ms` | 30 s | follower dropped from ISR ([[kafka-architecture]]) |
| etcd | lease with TTL, `KeepAlive` stream | app-defined, e.g. 10 s | keys attached to the lease deleted; watchers notified |
| ZooKeeper | session with `tickTime` heartbeats | session timeout 2–20 ticks | ephemeral znodes removed |
| Kubernetes | kubelet node lease (`kube-node-lease`) | renew 10 s, `node-monitor-grace-period` 40 s | node `NotReady`, pods evicted after toleration ([[kubernetes-basics]]) |
| Kubernetes leader election | `Lease` object, `coordination.k8s.io` | 15 s duration, 10 s renew, 2 s retry | another replica of the controller takes over |
| Patroni | leader key in etcd/Consul with `ttl` | 30 s, `loop_wait` 10 s | standby promoted ([[postgres-replication-and-failover]]) |
| Temporal | activity heartbeat with `heartbeatTimeout` | per activity | activity timed out and retried on another worker ([[temporal-concepts]]) |
| Raft | leader heartbeat = empty `AppendEntries`; election timeout | ~100 ms / 150–300 ms (etcd) | new election ([[raft-explained]]) |

### Health checks are leases from the load balancer's point of view
- **Liveness**: "should this process be restarted?" Only fail it on unrecoverable state (deadlock, corrupted internal state). Failing liveness on a slow dependency restarts healthy pods in a loop.
- **Readiness**: "should this instance get traffic?" Fail it when a required dependency is down, during warm-up, and during graceful shutdown so the LB drains connections before the process exits ([[signals-and-process-lifecycle]]).
- **Startup** probe for slow-starting JVMs so liveness does not kill them mid-boot.
- Spring Boot Actuator exposes `/actuator/health/liveness` and `/readiness` as separate groups; readiness can include `db`, `kafka` indicators, liveness should not ([[spring-boot-starters-and-autoconfiguration]]).
- A health check with no timeout is a lie: it hangs with the dependency and the LB keeps sending traffic.

### Tuning rules of thumb
- Timeout ≥ 3 × interval, both longer than your worst observed GC pause ([[garbage-collection]]); renew at one third of the TTL; stop acting at 70–80% of the TTL if renewal is unconfirmed.

## Example
Kubernetes probes and the matching Spring Boot configuration for a Kafka consumer service, with readiness tied to real dependencies and liveness kept independent of them.
```yaml
# deployment.yaml
livenessProbe:
  httpGet: { path: /actuator/health/liveness, port: 8080 }
  periodSeconds: 10
  failureThreshold: 3            # 30 s of real brokenness before restart
readinessProbe:
  httpGet: { path: /actuator/health/readiness, port: 8080 }
  periodSeconds: 5
  failureThreshold: 2            # out of rotation within 10 s
startupProbe:
  httpGet: { path: /actuator/health/liveness, port: 8080 }
  failureThreshold: 30           # up to 5 min for a cold JVM
  periodSeconds: 10

# application.yaml
management:
  endpoint.health.probes.enabled: true
  endpoint.health.group:
    readiness.include: readinessState, db, kafka      # dependency-aware
    liveness.include: livenessState                   # never depends on the network
server.shutdown: graceful
spring.lifecycle.timeout-per-shutdown-phase: 30s      # drain in-flight work before exit
spring.kafka.consumer.properties:
  session.timeout.ms: 45000
  heartbeat.interval.ms: 3000
  max.poll.interval.ms: 300000
```
On `SIGTERM` Spring flips readiness to `OUT_OF_SERVICE` first, the LB stops routing, in-flight requests finish, then the Kafka consumer leaves the group cleanly (a graceful leave triggers an immediate rebalance instead of waiting for `session.timeout.ms`).

## Pitfalls
- Liveness probe that calls the database: a DB outage restarts every pod, which then starts cold and makes the DB outage worse.
- `session.timeout.ms` shorter than a full GC: the consumer is kicked out mid-batch, its partitions go to another instance, both process the same records ([[delivery-guarantees]]).
- A lease holder that renews from a background thread and never checks the result; a failed renewal must stop the work, not just log.
- Heartbeats that prove only "the heartbeat thread runs": pair them with a progress check (records processed, last loop timestamp).
- Related: [[quorums-and-leader-election]] for leases as election, [[backpressure-and-flow-control]] for keeping `poll()` alive under load, [[slo-sli-and-alerting]] for turning health signals into alerts.
