---
title: TCP vs UDP
tags: [networking, tcp, udp, transport]
learned: 2026-10-27
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
| Flow control | receiver advertises a window (`win` in captures) | none |
| Congestion control | slow start, congestion avoidance, CUBIC default on Linux, BBR optional | none; a misbehaving UDP sender can flood a link |
| Header | 20–60 bytes | 8 bytes |
| Head-of-line blocking | yes: a lost segment stalls everything behind it | no |
| Broadcast / multicast | no | yes |
| Typical use | HTTP, TLS, DB protocols, Kafka, SSH | DNS, DHCP, NTP, StatsD, syslog, VoIP, games, QUIC |

### Why TCP feels slow sometimes
- **Handshake cost**: one RTT before the first byte, plus 1 RTT for TLS 1.3. Connection pools exist to pay it once ([[sockets-and-connection-pooling]]).
- **Slow start**: a new connection begins with a small congestion window (10 segments on Linux) and doubles per RTT; a short-lived connection never reaches full bandwidth. Another argument for keep-alive.
- **Nagle + delayed ACK**: Nagle buffers small writes until the previous one is acknowledged; the receiver delays ACKs up to 40 ms. Combined, request/response protocols with small messages see 40 ms stalls. Most clients set `TCP_NODELAY` (Netty and the JDK HTTP client do).
- **Head-of-line blocking**: HTTP/2 multiplexes streams on one TCP connection, so one lost packet stalls all streams; QUIC fixes it by putting streams inside UDP ([[http-1-1-vs-2-vs-3]]).

### When UDP is the right choice
- The data is stale as soon as it is late: live audio/video, game state, metrics samples. Retransmitting a 2-second-old frame is useless.
- Tiny request/response where a handshake would cost more than the payload: DNS ([[dns-resolution]]), NTP.
- You want to design your own reliability: QUIC, WireGuard, RTP. This is a lot of work; do it only when TCP's semantics are wrong for you.
- You need multicast or broadcast (service discovery on a LAN, DHCP).

### Reliability is not the same as "no data loss at the application"
- TCP guarantees delivery to the peer's kernel buffer, not that the application read and processed it. If the server crashes after ACK, the data is gone. Application-level acknowledgements (Kafka `acks=all`, HTTP 2xx, idempotent retries) are still necessary ([[delivery-guarantees]], [[idempotency]]).
- TCP has no message boundaries: `read()` can return half a message or two messages. Every protocol on TCP defines framing (HTTP `Content-Length`/chunked, Kafka length-prefixed frames, Postgres message type + length). With UDP each datagram is a message, but it can be lost or duplicated.

### Interview questions folded in
- "How does TCP detect loss?" Retransmission timeout (RTO, computed from RTT samples) or three duplicate ACKs for the same sequence number (fast retransmit). Modern stacks also use SACK to retransmit only the missing ranges.
- "Flow vs congestion control?" Flow control protects the receiver (window advertised by the receiver). Congestion control protects the network (window inferred by the sender from loss and delay). The effective window is the minimum of the two.
- "Is UDP ever reliable?" Only if the application makes it so. QUIC is reliable, ordered per stream, encrypted, and runs on UDP.

## Example
Sending the same tiny payload both ways from Java, which shows the API-level difference: a stream you write into versus packets you send.
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
The StatsD case is the canonical "UDP is fine" decision: losing 0.1% of counter increments is acceptable, and a metrics outage must never block the request thread.

## Pitfalls
- Assuming one `write()` becomes one `read()` on the other side over TCP. It does not; implement framing or use a protocol that has it.
- Long-lived idle TCP connections silently die through NAT gateways and firewalls (typically after 5–60 minutes of silence). Enable keep-alive or application pings ([[tcp-handshake-and-connection-states]]).
- UDP datagrams larger than the path MTU get fragmented at IP level, and one lost fragment loses the whole datagram; keep payloads under ~1400 bytes.
- `SO_RCVBUF`/`SO_SNDBUF` too small for high-latency links caps throughput at `window / RTT`; Linux autotunes TCP buffers, but UDP receive buffers overflow silently (`UdpInErrors` in `nstat`).
- Related: [[osi-and-tcp-ip-models]] for where transport sits, [[socket-management]] for how a server handles thousands of these sockets.
