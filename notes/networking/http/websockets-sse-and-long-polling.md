---
title: WebSockets, SSE and long polling
tags: [networking, websocket, sse, real-time]
learned: 2026-10-27
---
## TL;DR
Three ways to get server-initiated updates to a client over HTTP infrastructure. Long polling is a normal request the server holds open until it has something to say; simplest, works everywhere, wastes a connection per client. Server-Sent Events is a single long-lived HTTP response streaming text events one way, with reconnection built into the browser; the right default for notifications and progress. WebSocket upgrades an HTTP connection into a full-duplex binary channel with its own framing; needed for chat, games, collaborative editing, anything client→server at high rate. All three force the same architectural question: how does the instance holding the connection learn about an event produced elsewhere. Spring code for SSE and the SSE-vs-WebSocket decision from the framework side is in [[spring-reactive-webclient-sse]].

## Key points
### Comparison
| | Long polling | SSE | WebSocket |
|---|---|---|---|
| Direction | server → client (one message per request) | server → client stream | both, full duplex |
| Protocol | plain HTTP | HTTP, `text/event-stream` | HTTP `Upgrade` then `ws://`/`wss://` frames (RFC 6455) |
| Payload | anything | UTF-8 text events (`data:` lines, JSON inside) | text or binary frames |
| Reconnect | client loop | automatic in `EventSource`, resumes with `Last-Event-ID` | manual, you write the backoff |
| HTTP/2 | ordinary stream | multiplexed, no 6-connection limit | separate connection per socket (RFC 8441 h2 tunnelling is rarely deployed) |
| Proxies / LB / CDN | fine | fine if buffering is off and timeouts are long | need `Upgrade` support and long idle timeouts |
| Auth | cookies/headers per request | cookies; custom headers only via query or cookie (browser `EventSource` sends none) | cookies at handshake; no headers from browser API, token in first message or query |
| Server cost | a held request per client | an open response per client | an open socket per client plus state |
| Use for | low-frequency updates, legacy environments | notifications, progress, dashboards, LLM token streams | chat, presence, trading UIs, multiplayer |

### Long polling
- Client: `GET /events?since=42`; server parks the request (up to ~30 s), responds as soon as an event arrives or with `204` on timeout; client immediately asks again.
- With servlet threads this held request blocks a thread unless you use async (`DeferredResult`, `ResponseBodyEmitter`) or virtual threads ([[virtual-threads]]). Load balancers must have a request timeout longer than the hold time.
- Still the correct answer when a corporate proxy strips streaming or when the "client" is another backend that already polls.

### Server-Sent Events
- Wire format is lines: `id: 42`, `event: provisioning`, `data: {"iccid":"..."}`, blank line ends an event; `retry: 5000` sets the client reconnect delay; a line starting with `:` is a comment (used as heartbeat).
- The browser reconnects on any drop and sends `Last-Event-ID`, so if you keep a short replay buffer (Redis stream, Kafka offset keyed by event id) the client misses nothing.
- Proxies need `X-Accel-Buffering: no` (nginx) or `proxy_buffering off`, and the response must be flushed per event ([[reverse-proxy-nginx-basics]]). Send a heartbeat comment every 15–30 s so idle timeouts do not kill the stream.
- Server side in Spring: `SseEmitter` (MVC) or `Flux<ServerSentEvent<T>>` (WebFlux). One emitter per client; keep them in a map keyed by subscriber id and remove on completion/timeout.

### WebSocket
- Handshake: `GET /ws HTTP/1.1`, `Upgrade: websocket`, `Connection: Upgrade`, `Sec-WebSocket-Key`; server answers `101 Switching Protocols` with `Sec-WebSocket-Accept`. After that, framed messages (opcodes text/binary/ping/pong/close), client-to-server frames masked.
- No request/response structure: you design the message protocol. Most teams put a JSON envelope on top (`{"type":"...","payload":...}`) or use STOMP, which Spring supports out of the box with a message broker relay (`spring-messaging`, `@MessageMapping`, `SimpMessagingTemplate`).
- Each connection is state on one instance. Scaling needs a broker between instances (Redis pub/sub, Kafka, RabbitMQ STOMP relay) so a message produced on pod A reaches a client connected to pod B ([[design-chat-service]]).
- Keep-alive: ping/pong frames every 30 s; idle timeouts at the LB must be raised (AWS ALB default 60 s).

### Fan-out across instances (the real design problem)
- Event is produced in some service → published to a topic → every API instance consumes it and pushes to its local connections. Kafka with one consumer group per instance (unique `group.id`) or Redis pub/sub are the usual choices; Kafka gives replay for `Last-Event-ID`, Redis is simpler ([[redis-patterns-and-persistence]], [[notifications-and-push]]).
- Alternatively, sticky sessions route a subscriber to the instance that has its connection; fragile under scaling and deploys ([[load-balancers-l4-vs-l7]]).

## Example
Raw protocol views, which are what you see in DevTools or `curl` when things go wrong.
```bash
# SSE: one response that never ends; each event is a block of lines
$ curl -N -H 'Accept: text/event-stream' https://api.beeline.kg/api/v1/provisioning/8999600000000000001/events
HTTP/2 200
content-type: text/event-stream
cache-control: no-cache
x-accel-buffering: no

: ping

id: 17
event: provisioning
data: {"iccid":"8999600000000000001","status":"DOWNLOADED"}

id: 18
event: provisioning
data: {"iccid":"8999600000000000001","status":"INSTALLED"}

# WebSocket: handshake is HTTP, then it is not HTTP any more
$ curl -si --http1.1 -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
     -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' https://api.beeline.kg/ws
HTTP/1.1 101 Switching Protocols
upgrade: websocket
connection: upgrade
sec-websocket-accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=
```
nginx in front of either must pass the upgrade and stop buffering:
```nginx
location /ws {
    proxy_pass http://api;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}
location /api/v1/provisioning/ {
    proxy_pass http://api;
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
```

## Pitfalls
- Choosing WebSocket for a one-way feed: you inherit reconnect logic, custom auth, LB upgrade config and a message protocol for nothing SSE would not give you.
- SSE through a proxy with buffering on: the client receives events in 4 KB batches minutes late and the team blames Kafka.
- Forgetting heartbeats: idle connections are cut by NAT/LB after 60–300 s and the browser reconnect storm hits all at once ([[tcp-handshake-and-connection-states]]).
- Unbounded emitter maps: a client that disconnects without the server noticing leaks an emitter per reconnect. Register `onTimeout`/`onError`/`onCompletion` and cap connections per user.
- Tokens in WebSocket query strings end up in access logs; prefer a cookie or a first authenticated message.
- Related: [[spring-reactive-webclient-sse]] for the Spring implementation, [[http-1-1-vs-2-vs-3]] for why SSE on h2 no longer competes for the browser's connection limit.
