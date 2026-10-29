---
title: TCP vs UDP
tags: [networking, tcp, udp, transport]
learned: 2026-10-27
order: 1
---
## TL;DR
TCP gives you a connection and an ordered, reliable byte stream: handshake, sequence numbers, acknowledgements, retransmission, flow control (receiver window) and congestion control (sender slows down on loss). UDP gives you independent datagrams with a port and a checksum, nothing else. Almost everything a backend developer writes rides on TCP (HTTP/1.1, HTTP/2, gRPC, Postgres, Kafka). UDP shows up for DNS, metrics, video and HTTP/3 (QUIC), where the application is happy to implement its own reliability or to drop late data.

## Key points
### Comparison
| Aspect | TCP | UDP |
|---|---|---|
| Connection | yes, three-way handshake before data ([[tcp-handshake-and-connection-states]]) | none; first packet is data |
| Unit | byte stream, segmented by the kernel | datagram, one `send()` = one packet (up to ~64 KB, in practice keep under MTU ~1400 B) |
| Ordering | guaranteed by sequence numbers | none; packets may arrive reordered |
| Reliability | ACKs, retransmission on timeout or triple duplicate ACK | none; loss is the application's problem |
| Flow control | receiver advertises a window ([[tcp-flow-control]]) | none |
| Congestion control | sender adapts to loss and delay ([[tcp-congestion-control]]) | none; a misbehaving UDP sender can flood a link |
| Header | 20–60 bytes ([[tcp-segment-structure]]) | 8 bytes ([[udp-in-depth]]) |
| Head-of-line blocking | yes: a lost segment stalls everything behind it | no |
| Broadcast / multicast | no | yes |
| Typical use | HTTP, TLS, DB protocols, Kafka, SSH | DNS, DHCP, NTP, StatsD, syslog, VoIP, games, QUIC |

### Ports
| Range | Name | Notes |
|---|---|---|
| 0–1023 | well-known | binding needs root or `CAP_NET_BIND_SERVICE`; 22, 53, 80, 443 |
| 1024–49151 | registered | 5432 Postgres, 6379 Redis, 9092 Kafka, 8080 Tomcat, 4317/4318 OTLP |
| 32768–60999 | ephemeral (Linux) | assigned to outgoing connections; exhaustion in [[tcp-handshake-and-connection-states]] |

- A socket is identified by `(protocol, local ip, local port, remote ip, remote port)`, so one server port serves thousands of clients; TCP and UDP port spaces are independent.
- `bind()` to `127.0.0.1:8080` accepts only local connections; to `0.0.0.0:8080` accepts from every interface. In Spring Boot that is `server.address`. Inside a container, `127.0.0.1` is the container's own loopback, so a service bound there is unreachable from the host or other containers: the number one "port is open but connection refused" cause ([[docker-networking]]).
- `SO_REUSEADDR` lets a restarted server bind while old connections sit in `TIME_WAIT` (JVM sets it by default on server sockets).

### TCP: pros and cons
- **Pros**: reliable, ordered delivery; the kernel handles retransmission, flow and congestion control; passes every firewall and NAT; universal tooling.
- **Cons**: one RTT of handshake before data, plus TLS; pools pay it once ([[connection-establishment-cost]], [[sockets-and-connection-pooling]]); a new connection starts slow; Nagle and delayed ACK can add 40 ms stalls ([[nagle-and-delayed-ack]]); a lost segment stalls everything behind it ([[head-of-line-blocking]]); per-connection kernel state (buffers, queues, `TIME_WAIT`) limits how many clients one box holds.

### UDP: pros and cons
- **Pros**: no handshake, 8-byte header, stateless server, no head-of-line blocking, multicast; the application decides what to do with loss.
- **Cons**: loss, reordering and duplicates are your problem; no congestion control; spoofable source enables amplification attacks; often blocked or quickly expired by firewalls and NAT. Pick it when data is stale once late (media, game state, metrics), when a handshake costs more than the payload (DNS, NTP, [[dns-resolution]]), or when you build your own transport (QUIC) — details in [[udp-in-depth]].

### Reliability is not the same as "no data loss at the application"
- TCP guarantees delivery to the peer's kernel buffer, not processing by the application; a crash after the ACK loses the data. Application-level acknowledgements (Kafka `acks=all`, HTTP 2xx, idempotent retries) are still necessary ([[delivery-guarantees]], [[idempotency]]).
- TCP has no message boundaries: `read()` can return half a message or two messages. Every protocol on TCP defines framing (HTTP `Content-Length`/chunked, Kafka length-prefixed frames, Postgres message type + length). With UDP each datagram is a message, but it can be lost or duplicated.

### Interview questions folded in
- "How does TCP detect loss?" Retransmission timeout or three duplicate ACKs (fast retransmit); SACK lets it resend only the missing ranges.
- "Flow vs congestion control?" Flow control protects the receiver, congestion control protects the network; the sender uses the smaller of the two windows.
- "Is UDP ever reliable?" Only if the application makes it so, as QUIC does.

## Example
Sending the same tiny payload both ways from Java: a stream you write into versus packets you send. StatsD is the canonical "UDP is fine" case: losing 0.1% of counter increments is acceptable, blocking the request thread on a metrics outage is not.
```java
// TCP: connect once (handshake), then write a stream; framing is your job
try (var socket = new Socket("metrics.internal", 9000);
     var out = new DataOutputStream(socket.getOutputStream())) {
    socket.setTcpNoDelay(true);               // avoid Nagle stalls on small writes
    byte[] msg = "esim.activations:1|c".getBytes(StandardCharsets.UTF_8);
    out.writeInt(msg.length);                  // length prefix = message boundary
    out.write(msg);
    out.flush();
}

// UDP: no connection; each send is one datagram, may be lost or reordered, never blocks on a dead peer
try (var channel = DatagramChannel.open()) {
    var target = new InetSocketAddress("statsd.internal", 8125);
    var buf = ByteBuffer.wrap("esim.activations:1|c".getBytes(StandardCharsets.UTF_8));
    channel.send(buf, target);                 // fire and forget; a StatsD counter can afford loss
}
```

## Pitfalls
- Assuming one `write()` becomes one `read()` on the other side over TCP. It does not; implement framing or use a protocol that has it.
- Long-lived idle TCP connections silently die through NAT gateways and firewalls (typically after 5–60 minutes of silence). Enable keep-alive or application pings ([[tcp-handshake-and-connection-states]]).
- UDP datagrams larger than the path MTU get fragmented, and one lost fragment loses the whole datagram ([[mss-mtu-pmtud]]).
- Buffers too small for high-latency links cap TCP throughput at `window / RTT`; UDP receive buffers overflow silently (`UdpRcvbufErrors` in `nstat`).
- Related: [[osi-and-tcp-ip-models]] for where transport sits, [[building-tcp-udp-servers]] for minimal servers of both kinds, [[socket-management]] for how a server handles thousands of these sockets.
