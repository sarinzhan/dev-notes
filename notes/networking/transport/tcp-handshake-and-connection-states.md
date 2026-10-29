---
title: TCP handshake and connection states
tags: [networking, tcp, time-wait, keep-alive]
learned: 2026-10-27
---
## TL;DR
A TCP connection opens with SYN, SYN-ACK, ACK (one round trip) and closes with a FIN and an ACK in each direction. Along the way each side walks through a fixed state machine (`LISTEN`, `SYN-SENT`, `ESTABLISHED`, `FIN-WAIT`, `CLOSE-WAIT`, `TIME-WAIT` and others), and `ss -tan` shows where every socket is. The side that closes first parks the 4-tuple in `TIME_WAIT` for 60 seconds on Linux, which is why a client opening a connection per request runs out of ports. Keep-alive probes detect half-open connections whose peer vanished without a FIN.

## Key points
### Opening: three-way handshake
1. Client sends `SYN` with a random initial sequence number (ISN) and options: MSS, window scale, SACK permitted, timestamps ([[tcp-segment-structure]]).
2. Server replies `SYN-ACK`: its own ISN, acknowledges client ISN + 1. The connection sits in the server's **SYN queue** (`SYN_RECV`).
3. Client sends `ACK`; connection moves to the **accept queue** (`ESTABLISHED`) until the application calls `accept()`.
- Data flows only after step 3 (except TCP Fast Open, rarely enabled). Cost: one RTT; TLS adds another ([[tls-handshake-and-certificates]], [[connection-establishment-cost]]).
- Backlog, accept-queue overflow, SYN floods and SYN cookies: [[socket-management]].

### Closing: four-way handshake
- The **active closer** (whoever calls `close()` first) sends `FIN`; the passive side ACKs it, later sends its own `FIN` once its application calls `close()`, and the active side ACKs that. The middle two segments often merge into one `FIN, ACK`.
- Between the two FINs the connection is **half-closed**: the passive side may still send data (`shutdown(SHUT_WR)` does this on purpose).
- `RST` is the abrupt alternative: sent when nothing listens on the port ("connection refused"), when data arrives for a closed socket, or when `SO_LINGER` is zero. No `TIME_WAIT` on either side.

### The full state machine
| State in `ss` | Side | Entered when | Leaves when | Many of them means |
|---|---|---|---|---|
| `CLOSED` | both | start / end | `listen()` or `connect()` | — |
| `LISTEN` | server | `listen()` | `close()`; each SYN spawns a child socket | normal |
| `SYN-SENT` | client | sent SYN | SYN-ACK → `ESTAB` | firewall drops, host down, wrong route |
| `SYN-RECV` | server | got SYN, sent SYN-ACK | ACK → `ESTAB` | SYN flood, or client ACKs dropped |
| `ESTAB` | both | handshake done | `close()` → `FIN-WAIT-1`; FIN → `CLOSE-WAIT` | normal; count them to size pools |
| `FIN-WAIT-1` | active | sent FIN | ACK → `FIN-WAIT-2`; FIN → `CLOSING` | peer unreachable while closing |
| `FIN-WAIT-2` | active | our FIN ACKed | peer's FIN → `TIME-WAIT` | peer app slow to close; orphans bounded by `tcp_fin_timeout` (60 s) |
| `CLOSE-WAIT` | passive | got FIN | **our app calls `close()`** → `LAST-ACK` | connection leak in your code |
| `CLOSING` | both | simultaneous close: FIN crossed ours | ACK → `TIME-WAIT` | rare |
| `LAST-ACK` | passive | sent FIN after `CLOSE-WAIT` | ACK → `CLOSED` | final ACKs lost |
| `TIME-WAIT` | active | sent last ACK | 2×MSL timer → `CLOSED` | many short-lived connections closed by this side |

### Who ends in `TIME_WAIT` and why 2×MSL
- Only the **active closer**. For a client without keep-alive that is the client; for a server that closes idle keep-alive connections, or a proxy closing upstream connections per request, it is the server or proxy.
- **MSL** (maximum segment lifetime) is how long a segment may survive in the network. Two of them cover a round trip: if our last ACK is lost, the peer's retransmitted FIN can still arrive and be answered; and every delayed duplicate from both directions has expired before the same 4-tuple is reused, so it cannot corrupt a new connection.
- RFC 793 set MSL at 2 minutes; Linux hard-codes the whole `TIME_WAIT` at 60 seconds (`TCP_TIMEWAIT_LEN`), not tunable.
- An entry is small (no buffers), so tens of thousands are harmless on a server. The real cost is on the client: each pins an ephemeral port (`net.ipv4.ip_local_port_range`, 32768–60999, ~28k) toward that destination, capping new connections to one upstream at ~470 per second.
- Fixes, in order: pooling / keep-alive ([[sockets-and-connection-pooling]]), `net.ipv4.tcp_tw_reuse=1` (safe for outgoing connections), a wider port range. `tcp_tw_recycle` was removed in Linux 4.12.

### Keep-alive and half-open connections
- **Half-open**: one side disappeared (power loss, pod killed, NAT entry expired) without FIN or RST. The other side learns only when a write goes unacknowledged, after ~15 minutes of retransmits (`tcp_retries2`). A reader blocked in `read()` can wait forever.
- **TCP keep-alive** (`SO_KEEPALIVE`): after `tcp_keepalive_time` idle (7200 s default, far too long), probe every `tcp_keepalive_intvl`, give up after `tcp_keepalive_probes`. Use tens of seconds for pooled DB connections and long-lived Kafka/gRPC links (HikariCP `keepaliveTime`, Postgres JDBC `tcpKeepAlive=true`, [[hikaricp-pool-sizing]]). Not to be confused with HTTP keep-alive, which reuses a connection for several requests ([[http-fundamentals]]).
- The only true defence is a timeout on every blocking call: connect, read, and pool max-lifetime ([[failure-modes-and-timeouts]]).

## Example
Diagnosing a leaking Spring Boot client. `CLOSE-WAIT` growing on our side is always our bug: the peer closed, we never did.
```bash
$ ss -tan | awk 'NR>1 {print $1}' | sort | uniq -c
   812 CLOSE-WAIT                     # grows ~1/s: one leaked socket per call
    42 ESTAB
    10 LISTEN
   318 TIME-WAIT                      # separate problem: short-lived outgoing connections

$ ss -tan state close-wait '( dport = :443 )' | head -3
Recv-Q Send-Q Local Address:Port   Peer Address:Port
     1      0 10.20.5.2:41722      52.18.7.90:443
     1      0 10.20.5.2:41730      52.18.7.90:443
```
Typical cause in Java: a `HttpResponse<InputStream>` or streaming `RestClient` body never closed, or an exception thrown before the body is consumed, so the pooled connection is never returned. Each such socket stays in `CLOSE-WAIT` until the JVM exits.

## Pitfalls
- A load balancer or NAT with an idle timeout shorter than your pool's idle time produces "connection reset by peer" on the first request after a quiet period. Keep pool `maxLifetime`/`idleTimeout` below it (AWS NLB: 350 s; many firewalls: 300 s).
- `SO_LINGER=0` to "avoid TIME_WAIT" sends RST and can discard unsent data; the peer sees "connection reset" instead of a clean close.
- Container networking adds a NAT layer (`docker-proxy` or iptables DNAT); a half-open connection to a restarted container is only noticed on the next write ([[docker-networking]]).
- Related: [[tcp-vs-udp]] for why the handshake exists, [[tcpdump]] and [[linux-networking]] for watching these states live.
