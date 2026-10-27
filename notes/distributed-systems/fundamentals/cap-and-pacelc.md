---
title: CAP and PACELC, what the letters really mean
tags: [distributed-systems, consistency, availability, theory]
learned: 2026-10-27
---
## TL;DR
CAP says: when a network partition happens, a system must choose between staying **consistent** (linearizable: every read sees the latest write) and staying **available** (every request to a live node gets a non-error answer). You cannot "pick two" in advance, because partitions are not optional: the only real choice is C or A *during* a partition. PACELC adds the part that matters the other 99.9% of the time: **else** (no partition) you still trade **latency** against **consistency**. Use it as a vocabulary for design decisions, not as a classification of databases.

## Key points

### The three letters, precisely
| Letter | CAP meaning | What people usually think |
|---|---|---|
| **C** | Linearizability: a single, up-to-date copy of the data, as if there were one node ([[consistency-models]]) | "ACID consistency" (constraints, invariants) — unrelated |
| **A** | Every request to a **non-failed** node returns a non-error response, eventually | "99.9% uptime" — unrelated; CAP availability is a property of the algorithm, not an SLA |
| **P** | The network may drop or delay messages between nodes arbitrarily | "we have a good network, so we skip P" — no; you cannot opt out |

- The theorem (Gilbert and Lynch, 2002) is narrow: for a single register under an actual partition, you cannot have both C and A. It says nothing about latency, throughput, or what happens when the network is fine.
- "CA system" is a meaningless label for anything that runs on more than one machine. A single-node Postgres is "CA" only because it has nothing to partition from; the moment you add a replica, you are choosing.

### What choosing looks like in practice
- **CP behaviour**: a minority side of a partition refuses writes (and often reads) rather than risk divergence. Examples: a Raft/etcd cluster losing quorum, Kafka with `acks=all` and `min.insync.replicas` unmet, Postgres synchronous replication when the sync standby is unreachable ([[postgres-replication-and-failover]]). The client sees timeouts or errors — that *is* the design working.
- **AP behaviour**: both sides keep accepting writes and reconcile later. Examples: Dynamo-style stores with sloppy quorums, Cassandra at `ONE`, DNS, a shopping cart that merges on read ([[multi-leader-and-leaderless]]). The price is conflict resolution and stale reads.
- Most real systems are neither globally: they are CP for some operations (unique usernames, account balance) and AP for others (view counters, presence). Design per operation, not per database.

### PACELC
- **P**artition → **A**vailability or **C**onsistency; **E**lse → **L**atency or **C**onsistency.
- The "else" branch is the everyday cost: a synchronous replica write adds a network round trip to every commit; a leader-only read avoids staleness but cannot be served by the replica next to the user.
- Typical placements (approximate, configurable in all of them):

| System | P branch | E branch | Why |
|---|---|---|---|
| etcd / ZooKeeper / Consul | PC | EC | linearizable by design; quorum on every write, leader reads |
| Kafka (`acks=all`, ISR) | PC | EC/EL tunable | ISR shrinks rather than loses acknowledged data |
| Postgres async streaming | PA for reads, PC for writes | EL | replicas serve stale reads, one writer |
| Cassandra / Dynamo | PA | EL | tunable `W + R > N` moves it toward EC |
| MongoDB (majority write/read concern) | PC | EC | default concerns are weaker and shift it toward EL |

### Interview framing
- "Which do you pick, C or A?" Answer with the operation: "for the eSIM activation I need C, a duplicated activation costs money; for the activity feed A is fine." Then name the mechanism: quorum, leader, idempotent retry ([[idempotency]]).
- "Is CAP still relevant?" Yes as a reminder that partitions force a decision; no as a taxonomy. PACELC and [[consistency-models]] are the better vocabulary.
- CAP's A means the *algorithm* never blocks; a CP system can still have five nines of measured uptime if partitions are rare and short. Do not confuse the two ([[slo-sli-and-alerting]]).

## Example
A Kafka topic configured to behave as CP for acknowledged writes: a producer with `acks=all` is told "no" when fewer than two replicas are in sync, instead of being told "yes" and losing data on a leader crash.
```yaml
# topic-level (or broker default) settings
min.insync.replicas: 2        # with replication.factor=3
unclean.leader.election.enable: false   # never elect a stale follower

# producer
acks: all
enable.idempotence: true
retries: 2147483647
delivery.timeout.ms: 120000
```
With one replica partitioned away, the ISR drops to 2 and writes continue. With two away, the ISR is 1 and the producer gets `NotEnoughReplicasException` until the partition heals: consistency over availability, by configuration ([[kafka-architecture]], [[kafka-producer]]).

## Pitfalls
- Saying "we chose CA" in an interview signals you have not run a multi-node system; every networked system is P.
- Treating an AP store as a source of truth for money or uniqueness; "eventually consistent" means a window where two nodes disagree and both say yes.
- Assuming CP means "always safe": a CP system under partition returns errors, and the *client* code that retries blindly can still create duplicates ([[exactly-once-myth]]).
- Forgetting the E branch: synchronous replication across regions turns every commit into an intercontinental round trip ([[multi-region-basics]], [[latency-numbers]]).
- Related: [[consistency-models]] for what C actually offers, [[quorums-and-leader-election]] for how CP systems decide who may answer, [[failure-modes-and-timeouts]] for why "partition" is indistinguishable from "slow".
