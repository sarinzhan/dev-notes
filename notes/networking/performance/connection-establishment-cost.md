---
title: Connection establishment cost
tags: [networking, tcp, tls, latency, performance]
learned: 2026-10-29
order: 3
---
## TL;DR
A new connection costs round trips before the first useful byte: 1 RTT for the TCP handshake, plus 2 RTT for a TLS 1.2 handshake or 1 RTT for TLS 1.3, and then the request itself. On a 60 ms cross-region link that is 180–240 ms of pure waiting, and the fresh connection then starts in **slow start**, so a large response needs several more round trips. The fix is to pay once and reuse: **connection pools** and **keep-alive**. Protocol tricks shave the rest: TLS resumption, TLS 1.3 **0-RTT**, **TCP Fast Open** (data in the SYN, rarely usable in practice) and **QUIC**, which merges transport and TLS into 1 RTT, 0 on resumption.

## Key points
### Counting round trips
| Setup | RTTs before the request leaves | Time to first response byte |
|---|---|---|
| Plain TCP | 1 | 2 RTT |
| TCP + TLS 1.2 (full) | 3 | 4 RTT |
| TCP + TLS 1.2 (session resumption) | 2 | 3 RTT |
| TCP + TLS 1.3 | 2 | 3 RTT |
| TCP + TLS 1.3 0-RTT (resumed) | 1 | 2 RTT |
| QUIC (HTTP/3) | 1 | 2 RTT |
| QUIC 0-RTT (resumed) | 0 | 1 RTT |
| Reused keep-alive connection | 0 | 1 RTT |

- Add a DNS lookup on a cold cache ([[dns-resolution]]). The TCP handshake details are in [[tcp-handshake-and-connection-states]]; the TLS messages in [[tls-handshake-and-certificates]].
- Within one data centre RTT is ~0.2–0.5 ms, so handshakes cost little latency but still cost CPU: TLS needs an ECDHE key exchange and a certificate signature check per full handshake ([[latency-numbers]]).
- Across regions or to mobile clients (RTT 50–200 ms) handshakes dominate small requests.

### Slow start on a fresh connection
- A new connection does not know the path's capacity. It starts with an initial congestion window of 10 segments (RFC 6928, ~14 KB) and roughly doubles it each RTT ([[tcp-congestion-control]]).
- A 100 KB response therefore takes about 3 RTTs of data after the handshake (14 + 29 + 58 KB). On a warm connection whose window already grew, it can go in one.
- Linux resets the window after an idle period (`net.ipv4.tcp_slow_start_after_idle=1`, idle longer than one RTO). A pooled connection that sat idle for seconds starts slow again; long-lived replication or gRPC links often set it to 0.

### Pooling and keep-alive: the real fix
- HTTP/1.1 keeps connections open by default (`Connection: keep-alive`); HTTP/2 goes further and multiplexes every request onto one connection ([[http-1-1-vs-2-vs-3]]).
- Database drivers and HTTP clients keep pools: HikariCP for JDBC ([[hikaricp-pool-sizing]]), Reactor Netty or Apache HttpClient pools for REST ([[rest-clients]]), Kafka clients keep one long-lived connection per broker ([[kafka-producer]]). See [[sockets-and-connection-pooling]] for pool mechanics.
- Keep idle timeouts aligned along the path: the client's pool must drop a connection before the load balancer or NAT does, or the next request hits a dead socket ([[load-balancers-l4-vs-l7]]).
- For Postgres, a server-side pooler (PgBouncer) also avoids the per-connection backend process fork, which costs more than the network handshake ([[network-access-to-databases]]).

### TLS shortcuts
- **Session resumption** (session tickets / PSK) skips the certificate exchange and signature: one fewer RTT on TLS 1.2, less CPU on both.
- **TLS 1.3 0-RTT** (early data): the client sends the request with its first flight using a key from the previous session. The early data can be **replayed** by an attacker, so servers accept it only for idempotent requests; many servers disable it.

### TCP Fast Open (RFC 7413)
- First connection: the client's SYN asks for a cookie; the server returns one (an encrypted token of the client IP) in the SYN-ACK.
- Later connections: the SYN carries the cookie and the first request bytes. The server validates the cookie and hands the data to the application before the handshake finishes, saving 1 RTT.
- Limits: the data in the SYN can be duplicated, so it must be idempotent; middleboxes drop or strip SYNs with data or unknown options; browsers tried it and turned it off. Linux supports it (`net.ipv4.tcp_fastopen=3` for client and server), but the JDK exposes no API for it. In practice it is rare outside controlled networks.

### QUIC
- QUIC runs over UDP and does the transport and TLS 1.3 handshake in one flight: 1 RTT for a new connection, 0-RTT on resumption with the same replay caveat. Connections also survive client IP changes ([[udp-in-depth]]).

## Example
Measuring the phases with curl; the second URL reuses the connection.
```bash
$ cat fmt.txt
dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total}\n

$ curl -s -o /dev/null -o /dev/null -w @fmt.txt \
    https://api.example.eu/v1/tariffs https://api.example.eu/v1/tariffs
dns=0.012 tcp=0.074 tls=0.139 ttfb=0.204 total=0.205   # new: TCP 1 RTT + TLS 1.3 1 RTT + request 1 RTT (~62 ms each)
dns=0.000 tcp=0.000 tls=0.000 ttfb=0.063 total=0.063   # reused: one RTT

$ curl -s -o /dev/null --tlsv1.2 --tls-max 1.2 -w @fmt.txt https://api.example.eu/v1/tariffs
dns=0.001 tcp=0.063 tls=0.189 ttfb=0.252 total=0.253   # TLS 1.2: two RTTs for the handshake
```

## Pitfalls
- Creating an HTTP client or `RestTemplate` per request (or `new Socket()` per call) throws the pool away and pays the full handshake every time.
- A pool sized too small for bursts opens fresh connections under load, exactly when the extra RTTs and slow start hurt most.
- Idle pooled connections silently killed by a NAT or LB cause the first request after a quiet period to fail or hang; set the pool's max idle time below the shortest middlebox timeout and enable validation.
- Enabling TLS 0-RTT without restricting it to safe methods opens replay of POSTs.
- Related: [[head-of-line-blocking]] for what happens when one connection carries everything, [[nagle-and-delayed-ack]] for delays after the connection is up.
