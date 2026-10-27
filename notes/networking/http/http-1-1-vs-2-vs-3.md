---
title: HTTP/1.1 vs HTTP/2 vs HTTP/3
tags: [networking, http2, http3, quic, multiplexing]
learned: 2026-10-27
---
## TL;DR
HTTP/1.1 sends one request at a time per TCP connection, so clients open many connections and still queue. HTTP/2 keeps the same semantics but frames everything in binary and multiplexes many streams on one TCP connection with header compression; its remaining problem is TCP-level head-of-line blocking, where one lost packet stalls every stream. HTTP/3 moves the whole thing onto QUIC (UDP), where streams are independent, the handshake is merged with TLS 1.3, and connections survive IP changes. For a Java backend: HTTP/2 is what gRPC requires and what the JDK `HttpClient` speaks by default; HTTP/3 mostly matters at the edge (CDN, browser), rarely between services.

## Key points
### Comparison
| | HTTP/1.1 (1997) | HTTP/2 (2015) | HTTP/3 (2022) |
|---|---|---|---|
| Transport | TCP | TCP (TLS in practice) | QUIC over UDP, TLS 1.3 built in |
| Format | text | binary frames | binary frames (QUIC streams) |
| Requests per connection | 1 at a time (pipelining unused) | many streams, multiplexed | many streams, independent |
| Head-of-line blocking | at HTTP level: slow response blocks the next | at TCP level: lost packet blocks all streams | none across streams |
| Header compression | none (repeated cookies every request) | HPACK | QPACK |
| Handshake to first byte | TCP 1 RTT + TLS 1 RTT | same | 1 RTT total, 0-RTT on resumption |
| Server push | no | yes (deprecated, removed from browsers) | yes (unused) |
| Prioritisation | client opens 6 connections and hopes | stream priorities/dependencies | simpler priority scheme |
| Connection migration | no | no | yes (connection ID, survives Wi-Fi → 4G) |
| Middlebox friendliness | everything supports it | needs TLS (browsers refuse h2c) | UDP/443 is blocked by some corporate firewalls; falls back to h2 |

### HTTP/2 in detail
- One TCP connection per origin; **streams** carry request/response pairs, each split into **frames** (`HEADERS`, `DATA`, `SETTINGS`, `PING`, `RST_STREAM`, `GOAWAY`, `WINDOW_UPDATE`). Frames from different streams interleave.
- Per-stream and per-connection **flow control** windows on top of TCP's; a slow consumer of one stream does not stall the others unless the connection window is exhausted (tune `initialWindowSize` in gRPC/Netty for big payloads).
- **HPACK**: static table of common headers plus a dynamic table per connection; the second request with the same `authorization: Bearer ...` costs a few bytes. This is why cookie bloat hurts far less on h2.
- Negotiation: **ALPN** extension in the TLS handshake (`h2`), or `Upgrade: h2c` in cleartext (used by gRPC inside a cluster when the mesh does TLS). Browsers only speak h2 over TLS.
- `GOAWAY` is the graceful shutdown: server says "no new streams after id N", clients finish in-flight ones and reconnect. Correct handling matters for rolling deployments ([[kubernetes-basics]]).
- Semantics are unchanged: methods, status codes, headers. Pseudo-headers `:method`, `:path`, `:authority`, `:scheme` replace the request line; header names are lowercase.

### HTTP/3 and QUIC
- QUIC is a user-space transport: reliability, congestion control and streams implemented on UDP, so it ships with the application (browser, CDN edge) instead of waiting for kernel upgrades.
- TLS 1.3 is part of the handshake, not layered on top: 1 RTT for a new connection, 0-RTT for a resumed one (replayable, so only for idempotent requests).
- Stream-level loss recovery: a lost packet delays only the stream whose data it carried.
- Discovery: the server advertises `Alt-Svc: h3=":443"`; the client tries UDP next time and falls back to TCP if blocked. DNS `HTTPS` records can announce it earlier.
- Java status: no HTTP/3 in the JDK `HttpClient` as of Java 25; Netty has an incubator QUIC module; in practice h3 terminates at nginx/Caddy/Envoy or the CDN ([[cdn]]) and your service sees h2 or h1.1.

### What it means for a backend service
- Load balancers often speak h2 to clients and h1.1 to upstreams; check if your gRPC traffic is being downgraded ([[load-balancers-l4-vs-l7]]).
- With HTTP/2, "connection pool size" matters less (one connection carries 100+ concurrent streams, `SETTINGS_MAX_CONCURRENT_STREAMS`), but a single connection means a single TCP window and a single CPU core on the server for that client; gRPC clients to a hot service sometimes open several channels on purpose ([[grpc-and-protobuf]]).
- h2 on one connection makes client-side load balancing by DNS round-robin ineffective: all streams go to the one IP the connection was opened to ([[dns-resolution]]).
- Spring Boot: `server.http2.enabled=true` works with Tomcat (needs TLS or h2c), Jetty, Undertow and Netty (WebFlux); Spring's `RestClient` on the JDK client uses h2 when the server offers it ([[rest-clients]]).

## Example
Checking what a server actually negotiates. The output tells you the protocol version, ALPN result and whether h3 is advertised.
```bash
$ curl -sI --http2 https://api.beeline.kg/actuator/health -w '\nprotocol=%{http_version}\n'
HTTP/2 200
content-type: application/vnd.spring-boot.actuator.v3+json
alt-svc: h3=":443"; ma=86400          # server would accept HTTP/3 on UDP 443
protocol=2

$ curl -sI --http3-only https://api.beeline.kg/actuator/health -w 'protocol=%{http_version}\n'
protocol=3                           # only works with a curl built against a QUIC library

$ openssl s_client -connect api.beeline.kg:443 -alpn h2,http/1.1 </dev/null 2>/dev/null | grep ALPN
ALPN protocol: h2
```
Enabling h2 in Spring Boot behind TLS:
```yaml
server:
  http2:
    enabled: true
  ssl:
    bundle: server            # Spring Boot 3.1+ SSL bundles
```

## Pitfalls
- Enabling h2 does not make a slow backend faster; it removes connection-level queuing on the client, nothing else.
- One h2 connection to one upstream pins all traffic to one pod behind an L4 balancer; use an L7 balancer or client-side balancing for gRPC.
- A corporate firewall blocking UDP/443 silently downgrades h3 to h2; expect the fallback and do not debug the "slow" path as a bug.
- 0-RTT data in HTTP/3 (and TLS 1.3 early data) can be replayed; servers must reject non-idempotent requests in early data.
- Header names are lowercase in h2/h3; code that does `headers.get("X-Request-ID")` with a case-sensitive map breaks when the proxy upgrades.
- Related: [[tcp-vs-udp]] for why QUIC needed UDP, [[tls-handshake-and-certificates]] for ALPN and the TLS 1.3 handshake it rides on.
