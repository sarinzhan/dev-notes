---
title: TCP handshake and connection states
tags: [networking, tcp, time-wait, keep-alive]
learned: 2026-10-27
---
## TL;DR
A TCP connection is opened with SYN, SYN-ACK, ACK (one round trip) and closed with FIN/ACK in each direction. The side that closes first parks the 4-tuple in `TIME_WAIT` for 60 seconds on Linux, which is why a client that opens a new connection per request runs out of ports. Keep-alive probes detect dead peers on idle connections; without them a half-open connection (one side crashed, the other still thinks it is connected) can hang a thread until a write finally fails. `ss -tan` shows all these states and is the first thing to run when "connections pile up".

## Key points
### Opening: three-way handshake
1. Client sends `SYN` with a random initial sequence number (ISN) and options: MSS, window scale, SACK permitted, timestamps.
2. Server replies `SYN-ACK`: its own ISN, acknowledges client ISN + 1. The connection sits in the server's **SYN queue** (`SYN_RECV`).
3. Client sends `ACK`; connection moves to the **accept queue** (`ESTABLISHED`) until the application calls `accept()`.
- Data can only flow after step 3 (except TCP Fast Open, rarely enabled). Cost: one RTT. TLS adds another ([[tls-handshake-and-certificates]]).
- What the server kernel does with these queues (backlog, accept-queue overflow, SYN floods and SYN cookies) is in [[socket-management]].

### Closing: four-way handshake
1. Active closer sends `FIN` → `FIN_WAIT_1`.
2. Passive side ACKs → active side in `FIN_WAIT_2`, passive side in `CLOSE_WAIT` until its application calls `close()`.
3. Passive side sends its own `FIN` → `LAST_ACK`.
4. Active closer ACKs and enters `TIME_WAIT` for 2×MSL (60 s on Linux), then `CLOSED`.
- `TIME_WAIT` exists so that late segments of the old connection cannot be mistaken for a new connection on the same 4-tuple, and so the final ACK can be retransmitted if lost.
- `RST` is the abrupt alternative: sent when nothing listens on the port ("connection refused"), when data arrives for a closed socket, or when `SO_LINGER` is zero. No `TIME_WAIT` on the sender of RST.

### States you meet in production
| State in `ss` | Meaning | Many of them means |
|---|---|---|
| `LISTEN` | server socket waiting | normal |
| `SYN-SENT` | client sent SYN, no reply yet | firewall drops, host down, wrong route |
| `SYN-RECV` | server got SYN, waiting for ACK | SYN flood, or the client's ACK is being dropped |
| `ESTAB` | data can flow | normal; count them to size pools |
| `CLOSE-WAIT` | peer closed, **our application has not called close()** | connection leak in your code (response body never closed, pool not returning) |
| `FIN-WAIT-2` | we closed, waiting for peer's FIN | peer's app is slow to close; bounded by `tcp_fin_timeout` |
| `TIME-WAIT` | we closed first, 60 s guard | client without connection pooling, or proxy closing toward upstream per request |

### Keep-alive and half-open connections
- **Half-open**: one side disappeared (power loss, pod killed, NAT entry expired) without sending FIN or RST. The other side has no idea until it writes and the write is not acknowledged (then retransmits for ~15 minutes before giving up by default, `tcp_retries2`). A reader blocked in `read()` can wait forever.
- **TCP keep-alive** (`SO_KEEPALIVE`): after `tcp_keepalive_time` idle (7200 s default, far too long), send probes every `tcp_keepalive_intvl`, give up after `tcp_keepalive_probes`. Set it to tens of seconds for pooled DB connections and long-lived Kafka/gRPC links. HikariCP has `keepaliveTime`, the JDBC Postgres driver has `tcpKeepAlive=true` ([[hikaricp-pool-sizing]]).
- **HTTP keep-alive** is a different thing: reusing one TCP connection for several requests ([[http-fundamentals]]). gRPC and HTTP/2 add their own `PING` frames.
- The only true defence is a timeout on every blocking network call ([[failure-modes-and-timeouts]]): connect timeout, read timeout, and for pools a validation/max-lifetime.

### Ephemeral ports and `TIME_WAIT` exhaustion
- A client connection takes a port from the ephemeral range (`net.ipv4.ip_local_port_range`, ~28k ports by default, 32768–60999). Each 4-tuple in `TIME_WAIT` holds its port against the same destination for 60 s, so ~470 new connections per second to one upstream is the ceiling without reuse.
- Fixes, in order: connection pooling / keep-alive ([[sockets-and-connection-pooling]]), `net.ipv4.tcp_tw_reuse=1` (safe for outgoing connections), widening the port range. `tcp_tw_recycle` was removed in Linux 4.12; do not look for it.

## Example
Diagnosing a leaking Spring Boot client. `CLOSE-WAIT` growing on our side toward an upstream is always our bug: the peer closed, we never did.
```bash
$ ss -tan state close-wait '( dport = :443 )' | head
Recv-Q Send-Q Local Address:Port   Peer Address:Port
     1      0 10.20.5.2:41722      52.18.7.90:443
     1      0 10.20.5.2:41730      52.18.7.90:443
...
$ ss -tan state close-wait | wc -l
812                                   # grows ~1/s: one leaked socket per call

$ ss -tan | awk 'NR>1 {print $1}' | sort | uniq -c
   812 CLOSE-WAIT
    42 ESTAB
    10 LISTEN
   318 TIME-WAIT                      # separate problem: short-lived outgoing connections
```
Typical cause in Java: reading `HttpResponse<InputStream>` or a `RestClient` streaming body without closing it, or catching an exception before the body is consumed so the pooled connection is never returned. Each such socket stays in `CLOSE-WAIT` until the JVM exits.

## Pitfalls
- Lowering `TIME_WAIT` duration is not a tunable on Linux (it is fixed at 60 s); the real fix is reusing connections.
- A load balancer or NAT with an idle timeout shorter than your pool's idle time produces "connection reset by peer" on the first request after a quiet period. Set pool `maxLifetime`/`idleTimeout` below the infrastructure timeout (AWS NLB: 350 s; many firewalls: 300 s).
- `SO_LINGER=0` to "avoid TIME_WAIT" sends RST and can discard unsent data on the peer; the peer sees "connection reset" instead of a clean close.
- Container networking adds a NAT layer (`docker-proxy` or iptables DNAT); a half-open connection to a restarted container is only noticed on the next write ([[docker-fundamentals]]).
- Related: [[tcp-vs-udp]] for why the handshake exists, [[linux-networking]] for the `ss`/`tcpdump` toolbox around it.
