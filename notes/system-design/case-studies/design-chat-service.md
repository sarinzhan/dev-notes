---
title: Design a chat service
tags: [system-design, case-study, websockets, messaging, ordering]
learned: 2026-10-27
---
## TL;DR
A chat service keeps millions of long-lived connections open, routes a message from its sender to every online device of every recipient within a few hundred milliseconds, stores it durably for the offline ones, and shows the same order of messages to everyone. The design is a fleet of stateless-as-possible WebSocket gateways, a session registry that knows which gateway each user is connected to, a message service that persists first and fans out second, a pub/sub or Kafka layer between gateways, and a wide-column or partitioned store keyed by conversation. The hard part is ordering and delivery state across devices and reconnects. Transport options are in [[websockets-sse-and-long-polling]]; push for offline users in [[notifications-and-push]].

## Key points

### Requirements and estimates
- Functional: 1:1 and group chat (up to 500 members), text and media, delivery and read receipts, presence (online/last seen), history with pagination, multi-device.
- Non-functional: end-to-end latency < 300 ms for online users, no lost messages, consistent ordering within a conversation, 99.95% availability.
- Numbers: 10 M DAU, 40 messages/user/day → 400 M/day ≈ 4 600/s average, 15 000/s peak. Message ≈ 300 B → 120 GB/day of text; media in object storage ([[file-and-blob-storage]]). Concurrent connections: 2 M; at ~50 k connections per gateway node (memory-bound, idle sockets are cheap with NIO/virtual threads), 40–60 gateways.

### API
```
WebSocket wss://chat.example.com/ws   (auth via JWT in the first frame or a short-lived ticket)
  client → server:  { type: SEND, clientMsgId, conversationId, body }
  server → client:  { type: ACK, clientMsgId, messageId, seq }
                    { type: MESSAGE, conversationId, messageId, seq, senderId, body, sentAt }
                    { type: RECEIPT, conversationId, messageId, userId, state: DELIVERED|READ }
REST:
  GET  /v1/conversations/{id}/messages?beforeSeq=&limit=50
  POST /v1/conversations            { memberIds }
  POST /v1/media/upload-url         -> presigned URL
```
- `clientMsgId` is the idempotency key for retries after reconnect ([[idempotency]]).

### Data model
- `message(conversation_id, seq, message_id, sender_id, body, sent_at)` — partition key `conversation_id`, clustering by `seq` descending; Cassandra/ScyllaDB fit this write-heavy, read-recent pattern; Postgres partitioned by conversation hash works to tens of thousands of writes/s ([[column-and-wide-column-stores]], [[storage-choices]]).
- `conversation(id, type, created_at)`, `membership(user_id, conversation_id, last_read_seq)` — query both directions, so store it twice or index on both.
- `session(user_id, device_id, gateway_id, connected_at)` in Redis with TTL refreshed by heartbeats ([[redis-data-structures]]).
- `presence(user_id) → last_seen` in Redis.

### High-level design
```
mobile/web ──WebSocket──▶ LB (L4, sticky by connection) ──▶ gateway nodes (N)
                                                                 │ register session in Redis
                                                                 ▼
                                                      message service ──▶ message store (by conversation)
                                                                 │ assign seq, persist, ack sender
                                                                 ▼
                                   fan-out: for each member → lookup gateway in session registry
                                            online  → publish to gateway's channel (Redis pub/sub or Kafka topic per gateway)
                                            offline → push notification + unread counter
```

### Deep dive: ordering and delivery state
- **Sequence per conversation**: the message service assigns a monotonically increasing `seq` per conversation at persist time (Redis `INCR conv:{id}:seq` or a row-level counter with `UPDATE ... RETURNING`). Clients render by `seq`, not by client timestamps, which differ by device clock ([[time-and-clocks]]). Group chats route all sends of one conversation through one partition (Kafka key = `conversationId`) so the assignment is serialised ([[kafka-architecture]]).
- **Persist before fan-out**: the sender gets `ACK` only after the store confirms; fan-out failure is then a retry, not a loss ([[async-processing-and-event-driven-scaling]]).
- **Gap detection on the client**: each device remembers the last `seq` per conversation; on reconnect it requests `?afterSeq=` and merges. Pushes over the socket may be lost during reconnect, so the socket is an optimisation and the store is the truth.
- **Receipts**: DELIVERED is set by the recipient's device acking the frame; READ by an explicit event. Store `last_delivered_seq` / `last_read_seq` per member, not per message: O(members), and "read up to seq 120" compresses hundreds of events. Fan out receipts to the sender only, throttled (one per second per conversation).
- **Multi-device**: fan-out targets all sessions of a user; the sender's other devices receive the message too.
- **Gateway failover**: the LB is sticky per connection only; when a gateway dies, clients reconnect to any other node, re-register, and catch up by `seq`. Sessions in Redis expire by TTL ([[leases-heartbeats-and-health]]).
- **Presence**: heartbeats every 30 s update `last_seen`; "online" = seen within 60 s. Presence fan-out is the biggest hidden load (every status change to every contact): batch it and send only to contacts with an open conversation screen.

### Trade-offs
| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Transport | WebSocket | long polling / SSE | bidirectional, one connection per device; SSE for read-only feeds |
| Inter-gateway bus | Redis pub/sub per gateway | Kafka per gateway | pub/sub is fire-and-forget and fast; the store already guarantees durability |
| Sequence source | per-conversation counter | global Snowflake id | strict per-conversation order is what users see; global order is unnecessary |
| Store | wide-column by conversation | Postgres | write volume and "latest N by conversation" pattern; Postgres is fine below ~10 k writes/s |
| Group fan-out | on write (per member) | on read | groups ≤ 500 keep write amplification bounded; channels with 100 k members switch to fan-out on read ([[design-news-feed]]) |

## Example
Persisting with a per-conversation sequence and acknowledging the sender, idempotent on `clientMsgId`:

```java
@Transactional
public Ack send(SendCommand cmd) {
    Optional<Message> dup = messages.findByClientMsgId(cmd.senderId(), cmd.clientMsgId());
    if (dup.isPresent()) return Ack.of(dup.get());                     // reconnect retry

    long seq = jdbc.queryForObject("""
        UPDATE conversation SET last_seq = last_seq + 1
        WHERE id = ? RETURNING last_seq""", Long.class, cmd.conversationId());  // row lock serialises writers

    Message m = Message.create(cmd, seq);
    messages.save(m);
    outbox.publish(new MessageStored(m.conversationId(), m.id(), seq));  // fan-out after commit
    return Ack.of(m);
}
```

The fan-out consumer reads `MessageStored`, looks up each member's sessions in Redis, and publishes the frame to the gateway channels; members without sessions get a push notification.

## Pitfalls
- Interviewers probe: "two users send at the same time in a group — who is first?" Whoever gets the sequence first; the store, not the gateway, decides. "What if the gateway dies after ACK?" The message is stored; recipients catch up by `seq`.
- Fan-out before persist: a crash loses the message while some recipients already saw it.
- Per-message receipt rows explode storage and traffic; per-member watermarks instead.
- Presence updates broadcast to all contacts on every heartbeat take more bandwidth than the messages.
- Related: [[load-balancing]] for sticky L4 and connection draining, [[design-notification-service]] for the offline path, [[consistency-models]] for why "same order for everyone" is per-conversation sequential consistency.
