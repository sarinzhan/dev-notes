---
title: Multi-leader and leaderless replication
tags: [distributed-systems, replication, conflicts, crdt]
learned: 2026-10-27
---
## TL;DR
When one leader is not enough, because users write in several regions, or because you want writes to succeed even when nodes are unreachable, you let more than one node accept writes. **Multi-leader** has a few leaders (one per datacenter) that replicate to each other asynchronously; **leaderless** (Dynamo, Cassandra, Riak) lets a client write to any replica and uses read/write quorums. Both buy availability and locality at the price of **write conflicts**: two nodes accepted different changes to the same item and somebody has to merge them. Last-writer-wins silently loses data; CRDTs and application-level merge keep it.

## Key points

### Multi-leader
- Typical topology: one leader per region, clients write locally, leaders exchange change logs asynchronously. Also: offline-first clients (a phone is a leader with a very long replication delay), collaborative editing.
- Benefits: write latency equals local latency, a region keeps working during an inter-region partition ([[multi-region-basics]]).
- Cost: conflicts are inherent, auto-increment keys collide, triggers and constraints run per leader (a unique constraint cannot be enforced globally), and the replication topology (ring, star, all-to-all) determines what happens when one link breaks.
- Postgres has no built-in multi-master; BDR/pgEdge and logical replication both ways with conflict handling exist and are operationally demanding ([[postgres-replication-and-failover]]).

### Leaderless (Dynamo-style)
- No leader, every replica is equal. A write goes to all N replicas in parallel and succeeds when W acknowledge; a read asks all and succeeds when R respond; the client takes the newest version.
- **W + R > N** gives overlap: at least one of the R replicas has the latest acknowledged write. `N=3, W=2, R=2` is the usual default; `W=1, R=1` is "whatever".
- Keeping replicas converged: **read repair** (the client writes the newest version back to stale replicas it saw) and **anti-entropy** (background Merkle-tree diffing).
- **Sloppy quorums** and **hinted handoff**: if the designated replicas are unreachable, write to any N reachable nodes and hand the data over later. Increases availability, breaks the overlap guarantee until handoff completes.
- Even with strict quorums, concurrent writes and partial failures mean leaderless reads are not linearizable; treat them as "likely latest" ([[consistency-models]]).

### Detecting conflicts: versions and vector clocks
- "Concurrent" means neither write knew about the other, not "same millisecond". Each key carries a **version vector** (per-replica counters); a write that includes the version it read is a descendant, otherwise it is a **sibling** ([[time-and-clocks]]).
- Siblings must be stored and returned to the application for merging, or collapsed by a rule.

### Resolving conflicts
| Strategy | How | Loses data? | Used by |
|---|---|---|---|
| **Last-writer-wins (LWW)** | highest timestamp wins, others discarded | yes, silently; depends on clock skew | Cassandra (per cell), many caches |
| **Merge on read** | keep siblings, app merges (union of cart items) | no, but app complexity | Riak, Dynamo paper's shopping cart |
| **Custom resolver on write** | user-provided function per table | depends on function | BDR/pgEdge, CouchDB hooks |
| **CRDTs** | data types whose merge is commutative, associative, idempotent | no, by construction | Riak data types, Redis Enterprise, collaborative editors (Yjs, Automerge) |
| **Avoid the conflict** | route all writes for one key to one leader/home region | n/a | the pragmatic answer for most backends ([[sharding-strategies]]) |

- CRDT examples: G-counter (per-node counters summed), PN-counter (two G-counters), OR-set (add wins over concurrent remove), LWW-register (the honest name for LWW). A PN-counter works for likes; it does not work for an account balance that must not go negative, because the invariant is global.

### Interview framing
- "How do you handle writes in two regions?" First answer: do not; give each record a home region and route writes there (a telecom subscriber belongs to one country). Second: multi-leader with conflict-free data (append-only events, CRDT counters). Third, only if forced: LWW with the data loss spelled out.
- "Cassandra `QUORUM` is consistent, right?" Consistent as in `W + R > N`; not linearizable, not transactional, and LWW on write conflicts.

## Example
A Cassandra keyspace and table that make the trade-offs explicit: quorum reads/writes for subscriber state (one home region per subscriber, so conflicts are rare), `ONE` for a usage counter table that is append-only and conflict-free by design.
```yaml
# keyspace: 3 replicas per region
CREATE KEYSPACE telecom WITH replication = {
  'class': 'NetworkTopologyStrategy', 'eu-central': 3, 'asia-central': 3 };

# subscriber state: written only from the home region, read at LOCAL_QUORUM (2 of 3)
#   driver profile
datastax-java-driver:
  profiles:
    subscriber:
      basic.request.consistency: LOCAL_QUORUM        # W=2, R=2 of N=3 -> overlap
    usage-events:
      basic.request.consistency: ONE                 # append-only, no conflicts to resolve
      basic.request.timeout: 500 milliseconds
```
Cross-region replication stays asynchronous; `LOCAL_QUORUM` means a write is acknowledged by 2 of the 3 replicas in the writer's own region, so a transatlantic partition does not block it, and a reader in the other region may lag ([[column-and-wide-column-stores]]).

## Pitfalls
- LWW with clocks that are not synchronised: the "newest" write is the one from the node whose clock is ahead, not the one the user made last.
- Auto-increment ids across leaders: use UUIDs or region-prefixed ranges ([[time-money-and-ids]]).
- Assuming a unique constraint holds globally in a multi-leader setup; it holds per leader, and conflicts surface after replication as two rows with the same "unique" email.
- Sloppy quorum enabled by default (Cassandra hinted handoff): a write acknowledged by nodes that are not the real replicas, invisible to readers until handoff.
- Related: [[leader-follower-replication]] for the single-leader baseline, [[cap-and-pacelc]] for why these designs pick availability, [[event-sourcing]] for append-only models that sidestep conflicts.
