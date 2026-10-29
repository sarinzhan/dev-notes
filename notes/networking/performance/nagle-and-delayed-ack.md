---
title: Nagle's algorithm and delayed ACK
tags: [networking, tcp, latency, sockets, performance]
learned: 2026-10-29
order: 2
---
## TL;DR
**Nagle's algorithm** (sender side) holds back a small write while earlier data is still unacknowledged, so a chatty application does not flood the network with tiny packets. **Delayed ACK** (receiver side) holds back the acknowledgement for up to 40 ms on Linux (200 ms on Windows), hoping to piggyback it on a response. Each is sensible alone; together they deadlock for one timer period on the **write-write-read** pattern: the sender waits for an ACK before sending the second small piece, the receiver waits for that piece before replying, so it sits on the ACK until the timer fires. The fix is `TCP_NODELAY`, which latency-sensitive software (Netty, gRPC, the PostgreSQL JDBC driver, Kafka clients, curl, nginx) turns on by default, plus writing each message in one call.

## Key points
### Nagle's algorithm (RFC 896, 1984)
- Rule: if there is unacknowledged data in flight, a new segment smaller than one MSS is not sent; it is buffered until either an ACK arrives or enough data accumulates for a full segment ([[mss-mtu-pmtud]]).
- Purpose: a terminal sending one keystroke per packet pays 40 bytes of IP and TCP headers for 1 byte of data. Nagle coalesces those bytes into one packet per round trip.
- Consequence: at most one small segment is outstanding at a time. On a 50 ms RTT link, a stream of small writes goes out at most once per 50 ms.
- It does not delay the first small write on an idle connection; only a small write that follows unacknowledged data waits.

### Delayed ACK (RFC 1122)
- The receiver does not ACK every segment immediately. It waits for a second full-size segment, outgoing data to piggyback on, or a timer. RFC 1122 caps the delay at 500 ms; Linux uses 40 ms minimum (adaptive up to 200 ms), Windows 200 ms by default.
- Purpose: halve the number of pure ACK packets and let request–response protocols send the ACK inside the response.
- Linux starts each connection in **quickack mode** (ACKs the first segments immediately to help slow start) and switches to delayed mode once traffic looks interactive.

### The write-write-read stall
1. The client writes a small request header. Nothing is in flight, so Nagle sends it at once.
2. The client writes the small body. The header is not yet ACKed, so Nagle holds the body.
3. The server gets the header, cannot answer without the body, has nothing to send, so delayed ACK holds the ACK.
4. After 40 ms the server's ACK timer fires. The ACK releases the body, the server replies.
- Result: a 0.3 ms LAN request takes 40.3 ms. Under load it shows up as a p50 that looks like a fixed number (40 ms, 200 ms) independent of payload or server CPU, the giveaway.
- The same happens with any protocol that sends a message in two `write()` calls: HTTP header and body, a length prefix and a frame, a TLS record split across writes.

```anim
nagle-delayed-ack-stall
```

### TCP_NODELAY and related options
| Option | Side | Effect |
|---|---|---|
| `TCP_NODELAY` | sender | disables Nagle; every `write()` goes out as soon as the window allows |
| `TCP_QUICKACK` | receiver (Linux) | ACK immediately; not sticky, the kernel may drop back to delayed mode, so it is re-set after each read |
| `TCP_CORK` | sender (Linux) | the opposite of NODELAY: hold all partial segments until uncorked (or 200 ms), used to glue a header to a `sendfile()` body |
| `TCP_NOPUSH` | sender (BSD/macOS) | the BSD equivalent of `TCP_CORK` |

- Who sets `TCP_NODELAY` by default: Netty (and so Reactor Netty, WebFlux, gRPC Java), Tomcat's connectors, the PostgreSQL JDBC driver (`tcpNoDelay=true`), MySQL Connector/J, Kafka's producer, consumer and broker sockets, curl (since 2016), nginx on keep-alive connections (`tcp_nodelay on`). A plain `java.net.Socket` leaves Nagle on, so set it yourself on raw sockets.
- In Java: `socket.setTcpNoDelay(true)` or `StandardSocketOptions.TCP_NODELAY`; `jdk.net.ExtendedSocketOptions.TCP_QUICKACK` exists since Java 10 on Linux; Netty's epoll transport exposes `EpollChannelOption.TCP_CORK` and `TCP_QUICKACK`.
- NODELAY moves the batching responsibility to you: build the whole message in a buffer and write it once (a `BufferedOutputStream` flushed per message, one Netty `writeAndFlush` per response). Otherwise you trade a 40 ms stall for many tiny packets.

## Example
A hand-rolled binary protocol client on a raw socket, done right:
```java
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

public final class LedgerClient implements AutoCloseable {
    private final Socket socket = new Socket();
    private final DataOutputStream out;
    private final DataInputStream in;

    public LedgerClient(String host, int port) throws Exception {
        socket.setTcpNoDelay(true);                       // no Nagle: we batch ourselves
        socket.connect(new InetSocketAddress(host, port), 2_000);
        socket.setSoTimeout(5_000);
        out = new DataOutputStream(new BufferedOutputStream(socket.getOutputStream(), 8192));
        in = new DataInputStream(socket.getInputStream());
    }

    public long balance(String msisdn) throws Exception {
        byte[] body = msisdn.getBytes(StandardCharsets.UTF_8);
        out.writeInt(body.length);   // length prefix and body land in the same buffer...
        out.write(body);
        out.flush();                 // ...and leave in one write(), one segment
        return in.readLong();
    }

    @Override public void close() throws Exception { socket.close(); }
}
```

## Pitfalls
- A stable 40 ms or 200 ms latency floor on small requests is Nagle meeting delayed ACK until proven otherwise. Check with `ss -ti` or a [[tcpdump]] capture: look for a ~40 ms gap before the second data segment.
- Turning on `TCP_NODELAY` while writing a response field by field (unbuffered `OutputStream`, `write()` per header line) floods the network with tiny packets and raises CPU; always pair it with buffering.
- `TCP_QUICKACK` set once is not permanent on Linux; code that sets it only after `connect()` sees the stall come back.
- `TCP_CORK` without uncorking leaves the last partial segment waiting for the 200 ms ceiling.
- Related: [[socket-management]] for send buffers and segmenting, [[tcp-flow-control]] and [[tcp-congestion-control]] for the other things that decide when bytes leave, [[connection-establishment-cost]] for latency added before the first byte.
