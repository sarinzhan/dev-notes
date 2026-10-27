---
title: Actor and message-passing concurrency models
tags: [concurrency, actors, message-passing, csp, architecture]
learned: 2026-10-27
---
## TL;DR
Shared-memory concurrency (threads + locks) fails because any thread can touch any state; **message passing** removes the shared state and lets concurrent units communicate only by sending data. Two main schools: the **actor model** (Erlang, Akka/Pekko, Orleans, Temporal workflows in spirit) where each actor owns private state, has a mailbox, processes one message at a time and can create other actors; and **CSP** (Go channels, Clojure core.async, Java `BlockingQueue` pipelines) where anonymous processes synchronise through typed channels. Both trade locks for queues: no data races by construction, but you must now think about mailbox growth, ordering, delivery guarantees and backpressure. In a Java backend you already use these ideas: Kafka consumers, thread-per-partition pipelines, Temporal workflows and workflow-engine activities are message-passing systems.

## Key points

### Why leave shared memory
- With locks, correctness depends on every writer following the same discipline; one missed `synchronized` anywhere breaks the invariant ([[race-conditions-and-critical-sections]]). Composition is hard: two individually thread-safe classes combined need a new lock, and nested locks invite deadlock ([[deadlocks-and-synchronization-primitives]]).
- Message passing makes ownership explicit: state has exactly one owner, and the only way to change it is to send a message that the owner processes sequentially. The "lock" becomes the mailbox, and it never deadlocks on itself.
- It also maps naturally onto distribution: a message to a local actor and a message to an actor on another node look the same, so the model scales from threads to clusters ([[service-communication-sync-vs-async]], [[event-driven-architecture]]).

### Actor model
- An actor has: private state, a mailbox (queue), a behaviour that handles one message at a time, and an address. In response to a message it can change its state, send messages, and spawn actors. Nothing else can read its state.
- Guarantees typically offered: at-most-once delivery by default (Akka), per-sender-pair FIFO ordering, no concurrent processing within an actor. Reliability is layered on top: acknowledgements, persistence (event-sourced actors, [[event-sourcing]]), supervision.
- **Supervision**: actors form a tree; a parent decides what to do when a child fails (restart, resume, stop, escalate). "Let it crash" (Erlang) replaces defensive code with a restart from a known good state.
- **Location transparency**: the address may be local or remote; cluster sharding places an actor per entity id (one actor per subscriber, per order) on some node, so all commands for that entity are serialised without a distributed lock ([[distributed-locks-and-fencing]]).
- Costs: stack traces stop at the mailbox, ordering across actors is undefined, request/response needs correlation ids and timeouts, and an unbounded mailbox is a memory leak waiting for a traffic spike ([[backpressure-and-flow-control]]).

### CSP and channels
- Hoare's Communicating Sequential Processes: anonymous processes, first-class typed channels; a send on an unbuffered channel blocks until a receiver takes it (rendezvous), which gives backpressure for free. `select` waits on several channels at once.
- Go's "share memory by communicating" is CSP; Java gets the same shape from `BlockingQueue` between stages, Reactor streams with demand-based backpressure ([[spring-reactive-webclient-sse]]), and structured concurrency (JDK 25) with [[virtual-threads]].

### Comparison
| | Threads + locks | Actors | CSP / channels |
|---|---|---|---|
| State | shared, guarded by locks | private per actor | private per process; data moves over channels |
| Communication | memory writes | async messages to a named mailbox | sync or buffered send on an anonymous channel |
| Failure handling | exceptions propagate on the calling thread | supervision tree, restart | error values / closing channels |
| Deadlock | lock ordering bugs | none on state; possible with synchronous `ask` cycles | possible when both sides block on each other's send |
| Backpressure | implicit (threads block) | must be built (bounded mailboxes, pull-based streams) | built in for unbuffered channels |
| Distribution | separate concern | native (remote actors, sharding) | usually in-process |
| Debuggability | stack traces, thread dumps | message traces, correlation ids | goroutine dumps |
| Java/Kotlin options | `java.util.concurrent` | Akka/Pekko, Kotlin actors, Vert.x verticles | `BlockingQueue`, Reactor, Kotlin channels, JDK structured concurrency |

### Same patterns in a Spring/Kafka service
- A Kafka partition with one consumer is an actor mailbox with durable, ordered, at-least-once messages; the per-key state it updates is the actor state ([[kafka-consumer]], [[delivery-guarantees]]).
- A Temporal workflow is an actor with a persisted mailbox (history): one execution per business entity, signals are messages, activities are messages to workers, replay is the restart story ([[temporal-concepts]], [[workflow-engines-vs-sagas]]).
- A single-thread `ExecutorService` plus its queue is a one-actor system: the simplest way to serialise access to a non-thread-safe resource without locks ([[executors-and-thread-pools]]).

## Example
```java
// A single-threaded "actor" that owns the only mutable state for one eSIM profile:
// all commands go through its queue, so there are no locks and no interleaving.
final class ProfileActor implements AutoCloseable {
    sealed interface Msg permits Download, Enable, Query {}
    record Download(String iccid, CompletableFuture<Void> reply) implements Msg {}
    record Enable(CompletableFuture<Void> reply) implements Msg {}
    record Query(CompletableFuture<ProfileState> reply) implements Msg {}

    private final BlockingQueue<Msg> mailbox = new ArrayBlockingQueue<>(1_000);   // bounded: backpressure
    private final Thread loop;
    private ProfileState state = ProfileState.EMPTY;                               // private, owner-only

    ProfileActor(String name) {
        loop = Thread.ofVirtual().name("profile-" + name).start(this::run);        // JDK 21 virtual thread
    }

    void tell(Msg m) throws InterruptedException {
        if (!mailbox.offer(m, 1, TimeUnit.SECONDS)) throw new MailboxFull();        // do not grow unbounded
    }

    private void run() {
        try {
            while (!Thread.currentThread().isInterrupted()) {
                switch (mailbox.take()) {                                          // one message at a time
                    case Download d -> { state = state.download(d.iccid()); d.reply().complete(null); }
                    case Enable e   -> { state = state.enable();            e.reply().complete(null); }
                    case Query q    -> q.reply().complete(state);
                }
            }
        } catch (InterruptedException ignored) { }
    }

    @Override public void close() { loop.interrupt(); }
}
```
The reply `CompletableFuture` is the `ask` pattern; the bounded `ArrayBlockingQueue` is the mailbox with backpressure; the `switch` over a sealed interface is the behaviour. Akka/Pekko adds supervision, clustering and persistence on top of exactly this shape.

## Pitfalls
- Unbounded mailboxes: the actor "never blocks" until the JVM runs out of heap during the first burst. Bound them and decide what happens on overflow (drop, shed, slow the producer).
- Sending mutable objects in messages: the receiver and the sender now share memory again and the model's guarantee is gone. Send immutable records or copies ([[immutability-and-side-effects]]).
- Synchronous `ask` chains between actors (A asks B, B asks A) deadlock just like nested locks; set timeouts and prefer fire-and-forget with replies.
- Assuming ordering across actors or partitions: only per-sender, per-mailbox FIFO holds; a saga across several entities still needs explicit sequencing ([[saga]]).
- Reaching for Akka in a Spring service that only needs to serialise per-key work: a `ConcurrentHashMap` of single-thread executors, or Kafka keys, usually suffice and keep the team's debugging tools.
- Related: [[mutex-semaphore-monitor]] for what the mailbox replaces, [[message-queues-and-streams]] for the same model across process boundaries.
