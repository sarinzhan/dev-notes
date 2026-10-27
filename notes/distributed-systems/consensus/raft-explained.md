---
title: Raft consensus explained
tags: [distributed-systems, consensus, raft, kafka]
learned: 2026-10-27
---
## TL;DR
Raft lets a cluster of nodes agree on a single **replicated log** even when some nodes crash or the network partitions, as long as a **majority** is alive and connected. One node is **leader**; it appends client commands to its log, replicates them to followers, and marks an entry **committed** once a majority has it. If the leader dies, followers time out and hold an **election**; a candidate wins only if its log is at least as up to date as the majority's, which guarantees committed entries are never lost. etcd (so Kubernetes), Consul, CockroachDB and Kafka's KRaft controller all run it.

## Key points

### The problem it solves
- Replicating state by itself is easy; agreeing on the *order* of changes while nodes fail is the hard part. Consensus = all live nodes decide the same value, a decided value was proposed by someone, and the decision eventually happens ([[quorums-and-leader-election]]).
- Paxos solves it too but is famously hard to implement correctly; Raft was designed (2014) to be understandable: leader-based, with the log as the central object.

### Roles and terms
| Role | Does |
|---|---|
| **Leader** | accepts client writes, appends to log, sends `AppendEntries` (also used as heartbeat) |
| **Follower** | passive; replies to leader and candidates; starts an election if no heartbeat within the election timeout |
| **Candidate** | requests votes; becomes leader with a majority, steps down if it sees a higher term |

- **Term**: a monotonically increasing number, one leader per term at most. Every message carries the sender's term; a node seeing a higher term immediately becomes a follower and adopts it. Terms are the logical clock that makes stale leaders harmless ([[time-and-clocks]]).

### Leader election
- Election timeout is randomised (e.g. 150–300 ms) so that usually one follower times out first, becomes candidate, increments term, votes for itself and sends `RequestVote`.
- A node grants at most one vote per term, and only to a candidate whose last log entry is **at least as up to date** (higher term, or equal term and longer log). This is the *election restriction*: whoever wins already has every committed entry.
- Split vote: nobody gets a majority, timeouts fire again with new random values; converges quickly in practice.

### Log replication and commit
- Leader appends the command, sends `AppendEntries(prevLogIndex, prevLogTerm, entries, leaderCommit)` to all followers. A follower rejects if its entry at `prevLogIndex` does not match `prevLogTerm`; the leader then backs up `nextIndex` for that follower until the logs agree, and overwrites the follower's divergent suffix.
- An entry is **committed** when stored on a majority *and* it belongs to the current term (a leader never commits an entry from an older term by counting replicas; it commits it indirectly by committing a newer one on top). The leader advances `commitIndex`, applies to its state machine, and tells followers in the next heartbeat.
- Client gets the response after commit + apply. Read requests must also go through the leader and be confirmed (read index or lease) to be linearizable ([[consistency-models]]).

### Safety properties (what you can promise)
- **Election safety**: at most one leader per term.
- **Log matching**: if two logs have an entry with the same index and term, the logs are identical up to that index.
- **Leader completeness**: a committed entry is in the log of every future leader.
- **State machine safety**: no two nodes apply different commands at the same index.

### Practical extras
- **Membership changes** use joint consensus or single-server changes so two disjoint majorities can never exist mid-change.
- **Snapshots** compact the log; a follower too far behind receives `InstallSnapshot`.
- **Pre-vote** and **check-quorum** (etcd) stop a partitioned node from bumping terms endlessly and dethroning a healthy leader when it reconnects.
- **Kafka KRaft** (default since Kafka 3.3, ZooKeeper removed in 4.0): the controller quorum replicates cluster metadata as a Raft log (`__cluster_metadata`); brokers are followers that fetch it. Data partitions still use Kafka's own ISR replication, not Raft ([[kafka-architecture]]).

## Example
Pseudo-code of the two RPC handlers; the whole protocol fits on a page.
```text
on RequestVote(term, candidateId, lastLogIndex, lastLogTerm):
    if term < currentTerm: reply(false)
    if term > currentTerm: currentTerm = term; votedFor = null; become FOLLOWER
    logOk = lastLogTerm > myLastTerm or (lastLogTerm == myLastTerm and lastLogIndex >= myLastIndex)
    if (votedFor in {null, candidateId}) and logOk:
        votedFor = candidateId; resetElectionTimer(); reply(true)
    else reply(false)

on AppendEntries(term, leaderId, prevIndex, prevTerm, entries, leaderCommit):
    if term < currentTerm: reply(false)
    become FOLLOWER; currentTerm = term; resetElectionTimer()
    if log[prevIndex].term != prevTerm: reply(false)          # leader will retry with prevIndex-1
    delete log entries after prevIndex that conflict; append entries
    commitIndex = min(leaderCommit, lastIndex)
    apply log[lastApplied+1 .. commitIndex] to state machine
    reply(true)

leader loop:
    for each follower f: send AppendEntries(log[nextIndex[f]..]); on success matchIndex[f] = lastIndex
    N = largest index replicated on a majority with log[N].term == currentTerm
    if N > commitIndex: commitIndex = N; apply; respond to waiting clients
```
In KRaft terms: `process.roles=controller`, `controller.quorum.voters=1@c1:9093,2@c2:9093,3@c3:9093`; three voters tolerate one failure, five tolerate two.

## Pitfalls
- Running an even number of voters: 4 nodes tolerate exactly as many failures as 3 (one) but need more messages per commit.
- Treating a follower read as consistent: it may lag or belong to a deposed leader's view; etcd `--consistency=l` versus `s` exists for this reason.
- Election timeout too close to heartbeat interval or to a typical GC pause: spurious elections under load, which look like "the cluster is flapping" ([[leases-heartbeats-and-health]]).
- Disk fsync is part of the protocol: a node that acknowledges an entry without persisting `currentTerm`/`votedFor`/log can vote twice after a restart and break election safety.
- Related: [[quorums-and-leader-election]] for the majority arithmetic, [[distributed-locks-and-fencing]] for how to use a Raft store (etcd) safely from application code.
