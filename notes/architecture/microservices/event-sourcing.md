---
title: Event sourcing
tags: [event-sourcing, architecture, ddd, postgres]
learned: 2026-10-27
---
## TL;DR
With event sourcing the **source of truth is the append-only log of domain events** per aggregate (`SimRegistered`, `ProfileActivated`, `SimSuspended`), not the current-state row. Current state is rebuilt by replaying the events, **snapshots** shortcut long histories, and **projections** turn the log into query-friendly tables ([[cqrs]]). You get a complete audit trail, temporal queries ("what was the state on 1 March") and natural integration events, at the cost of a different programming model, schema evolution of events that live forever, and eventual consistency on every read model. It is a per-aggregate decision, not a system-wide one; a ledger or a card-authorisation history is a good candidate, a tariff catalogue is not.

## Key points

### The model
- **Event store**: a table `events(stream_id, version, type, payload, metadata, occurred_at)` with a unique `(stream_id, version)`; one stream per aggregate. Appends are the only writes. Postgres is fine for most teams; EventStoreDB, Axon Server or Kafka (with care: no per-key optimistic concurrency) are alternatives.
- **Load**: read the stream, `apply` each event to an empty aggregate in order. The aggregate's `apply` methods only set state and never throw; the `decide` methods (commands) validate invariants against that state and return new events ([[aggregates-and-invariants]]).
- **Save**: append the new events with `expectedVersion`; a conflict means another writer appended first, so reload and retry (optimistic concurrency without a `@Version` column).
- **Snapshot**: every N events store the serialised state with its version; loading starts from the snapshot and replays only the tail. Snapshots are a cache, deletable at any time.
- **Projection**: a consumer of the store (polling by global position, or Kafka via outbox/CDC) that builds read tables. Rebuildable by resetting its position to zero.

### Event sourcing vs state storage vs audit table
| | Current-state row | State + audit/history table | Event sourcing |
|---|---|---|---|
| Source of truth | The row | The row (history is derived) | The events (state is derived) |
| Audit completeness | None | Whatever you remembered to log | Complete, by construction |
| Query current state | Direct | Direct | Via projection or replay |
| Schema evolution | Migration | Migration | Upcasting old events, forever |
| Concurrency | `@Version` | `@Version` | Expected stream version |
| Integration events | Need the [[outbox-pattern]] | Same | The events *are* the integration source (still through an outbox/relay) |
- A history table gives most of the audit benefit with none of the programming-model change; pick it when audit is the only driver.

### Where it pays off
- Money and ledgers: balance = sum of entries, and the entries are the legal record ([[design-payment-processing]], [[emv-card-processing]] authorisation/clearing flows).
- Regulatory "why did this happen" questions, dispute handling, replaying a bug fix over history.
- Domains where the business thinks in events already (subscription lifecycle, order lifecycle).

### Versioning events
- Events are immutable and old ones are read forever: never rename a field, add optional fields only, and **upcast** (`ProfileActivatedV1` → `V2` in a converter on read) when the shape must change. A schema registry helps even without Kafka ([[message-schema-evolution]]).
- Do not put the whole entity in every event; record the *delta* and the intent (`PriceChanged(old, new, reason)`).
- Personal data in immutable events vs GDPR deletion: encrypt PII per subject with a key you can discard (crypto-shredding), or keep PII out of events and reference a mutable profile.

### Operational reality
- Reads of a long stream are slow without snapshots; a `Subscriber` with 100k usage events is a stream design mistake (split the aggregate, close streams per period).
- Projections lag; a UI that reads a projection right after a command sees stale data ([[cqrs]] discusses the options).
- Testing is pleasant: given events, when command, then events: pure functions, no database ([[junit-and-mockito]]).

## Example
A Postgres event store with optimistic appends, and an event-sourced `Sim` aggregate split into decide/apply.

```sql
CREATE TABLE sim_events (
  global_pos  bigserial PRIMARY KEY,                 -- projections poll by this
  stream_id   uuid        NOT NULL,
  version     int         NOT NULL,
  type        text        NOT NULL,
  payload     jsonb       NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (stream_id, version)                        -- optimistic concurrency
);
```

```java
public final class Sim {
    private UUID id; private int version; private String activeIccid; private boolean suspended;
    private final List<DomainEvent> pending = new ArrayList<>();

    public static Sim load(List<DomainEvent> history) {
        var sim = new Sim();
        history.forEach(sim::apply);
        return sim;
    }

    // decide: validate, emit; never mutate directly
    public void activate(Iccid iccid) {
        if (suspended) throw new InvariantViolation("suspended SIM cannot activate a profile");
        if (iccid.value().equals(activeIccid)) return;                 // idempotent no-op
        emit(new ProfileActivated(id, iccid.value()));
    }

    // apply: pure state transition, used both for new events and for replay
    private void apply(DomainEvent e) {
        switch (e) {
            case SimRegistered r     -> id = r.simId();
            case ProfileActivated a  -> activeIccid = a.iccid();
            case SimSuspended s      -> suspended = true;
            default -> {}                                               // unknown old types ignored
        }
        version++;
    }

    private void emit(DomainEvent e) { apply(e); pending.add(e); }
    public int version() { return version; }
    public List<DomainEvent> pending() { return List.copyOf(pending); }
}

@Repository
@RequiredArgsConstructor
public class SimEventStore {
    private final JdbcClient jdbc; private final EventCodec codec;

    public Sim load(UUID simId) {
        var events = jdbc.sql("SELECT type, payload FROM sim_events WHERE stream_id = :id ORDER BY version")
                .param("id", simId).query((rs, i) -> codec.decode(rs.getString(1), rs.getString(2))).list();
        return Sim.load(events);
    }

    @Transactional
    public void append(UUID simId, int expectedVersion, List<DomainEvent> events) {
        int v = expectedVersion;
        for (DomainEvent e : events) {
            v++;
            jdbc.sql("INSERT INTO sim_events (stream_id, version, type, payload) VALUES (:id, :v, :t, :p::jsonb)")
                .param("id", simId).param("v", v).param("t", codec.typeOf(e)).param("p", codec.encode(e))
                .update();                       // unique violation here = concurrent writer -> retry the command
        }
    }
}
```

## Pitfalls
- Event-sourcing the whole system: catalogue, settings and reference data gain nothing and lose simple `UPDATE`s.
- Using Kafka as the event store: no per-stream optimistic concurrency, no "load one aggregate" read path, compaction deletes history. Kafka is the transport for projections, not the store.
- Events that leak implementation (`RowUpdated(columns...)`) instead of intent; the log becomes unreadable and unreplayable after a refactor.
- Forgetting that the event store and the integration bus are different: publishing from the store still needs an outbox/relay or CDC, or you lose events on crash.
- Related: [[cqrs]], [[domain-events-and-integration-events]], [[aggregates-and-invariants]], [[event-driven-architecture]], [[time-money-and-ids]].
