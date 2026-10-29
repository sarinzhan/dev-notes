---
title: TCP flow control and the receive window
tags: [networking, tcp, transport, flow-control]
learned: 2026-10-29
---
## TL;DR
Flow control stops a fast sender from overrunning a slow receiver. Every TCP segment carries the receiver's **receive window** (`rwnd`): how many more bytes it can buffer right now. The sender keeps at most that many unacknowledged bytes in flight, sliding the window forward as ACKs arrive. When the receiving application stops calling `read()`, its socket receive buffer fills, the advertised window drops to **zero**, and the sender stops and sends window probes until the window reopens. That is how a slow consumer back-pressures a fast producer with no application code.

## Key points
### The sliding window
- The sender's byte stream splits into four regions: **sent and acknowledged** (can be freed), **sent, not yet acknowledged** (in flight), **not sent but allowed** (the usable window) and **not allowed yet** (beyond the window).
- Usable window = `rwnd − bytes in flight` (and in reality `min(rwnd, cwnd) − in flight`, see [[tcp-congestion-control]]).
- Each ACK does two things: moves the left edge (bytes are acknowledged) and re-advertises the window, which moves the right edge. If the application reads slowly, the left edge moves but the right edge does not: the window **shrinks**.
- The window field lives in the TCP header ([[tcp-segment-structure]]) and appears as `win` in [[tcpdump]] output.

### Window scaling
- The header field is 16 bits, so the window caps at 65,535 bytes: on a 100 ms path that is ≈ 5 Mbit/s, however fast the link.
- The **window scale option** (RFC 7323) is negotiated in the SYN and SYN-ACK only: each side announces a shift count (0–14), and the real window is `field << shift`, up to about 1 GB. If a middlebox strips the option from the handshake, the connection is stuck at 64 KB forever.

### Bandwidth-delay product
- To keep a link full, the window must cover all bytes that fit "in the pipe": **BDP = bandwidth × RTT**. 1 Gbit/s × 50 ms = 6.25 MB. A smaller window means throughput = `window / RTT`.
- Inside a data centre (RTT ~0.2 ms) small buffers suffice; cross-region replication needs multi-megabyte windows ([[latency-numbers]]).

### Window and the socket receive buffer
- The advertised window is the free space in the connection's **receive buffer** in the kernel ([[socket-management]]). Bytes the kernel ACKed but the application has not read occupy it (`Recv-Q` in `ss`).
- Linux **autotunes** the buffer between the min and max of `net.ipv4.tcp_rmem` (`4096 131072 6291456` by default on recent kernels) based on the measured RTT and read rate. Part of the buffer is reserved for kernel overhead (`tcp_adv_win_scale`), so the window is smaller than the buffer.
- Setting `SO_RCVBUF` explicitly (Java `setReceiveBufferSize`) **disables autotuning** for that socket and caps the window scale chosen at handshake.

### Zero window and probes
- When the buffer is full the receiver advertises `win 0`. The sender stops and starts a **persist timer**, sending **window probes** with exponential backoff, so a lost window update cannot deadlock the connection.
- When the application reads, the receiver sends a **window update**. To avoid *silly window syndrome* (advertising a few bytes at a time, then getting tiny segments), the receiver waits until at least one MSS or half the buffer is free ([[mss-mtu-pmtud]]); [[nagle-and-delayed-ack]] covers the sender side.
- A zero window is not an error: the connection can sit there for hours. Only application timeouts end it, so set socket read timeouts on clients and `statement_timeout` on the database ([[failure-modes-and-timeouts]]).

### Back-pressure end to end
- A slow consumer fills its receive buffer → zero window → the sender's **send buffer** fills → the sender's `write()` blocks (or returns `EAGAIN` on a non-blocking socket) → the producing thread stops. This chain is TCP's built-in [[backpressure-and-flow-control|back-pressure]].
- Concrete case: a JDBC client streams a big result set and processes rows slowly. Once its window closes, the Postgres backend blocks in `send()` while holding its snapshot and locks. `pg_stat_activity` shows it with `wait_event = ClientWrite` ([[network-access-to-databases]]).
- The same happens with HTTP responses streamed to slow mobile clients.

```anim
tcp-sliding-window
```

## Example
Streaming a large Postgres result set: TCP provides back-pressure and heap stays flat, but the query stays open while processing lasts.
```java
@Transactional(readOnly = true)                       // pgJDBC only streams inside a transaction
public void exportActivations(PrintWriter out) {
    jdbcTemplate.setFetchSize(1_000);                  // fetch in batches, not the whole result into heap (dedicated JdbcTemplate bean)
    jdbcTemplate.query("""
            SELECT iccid, msisdn, activated_at
            FROM esim_activation
            WHERE activated_at >= now() - interval '30 days'
            """,
        rs -> {
            // if this is slow (e.g. writing to a slow HTTP client), the socket receive buffer fills,
            // rwnd goes to 0, and the Postgres backend blocks in send(): wait_event = ClientWrite
            out.println(rs.getString("iccid") + ";" + rs.getString("msisdn"));
        });
}
```
Check it live with `ss -tmi dst :5432`: a growing `Recv-Q` on the client and a full `Send-Q` on the database host mean the reader is the bottleneck.

## Pitfalls
- Hard-coding `SO_RCVBUF`/`SO_SNDBUF` "for performance" usually makes things worse: it turns off autotuning. Raise `net.ipv4.tcp_rmem`/`tcp_wmem` max (and `net.core.rmem_max`) instead when high-BDP links are really underused.
- Long-running streaming queries over a slow consumer hold a Postgres snapshot open and block vacuum ([[postgres-vacuum-and-bloat]]). Buffer to a file or use keyset pagination for exports.
- Throughput stuck at ~64 KB per RTT means a middlebox stripped window scaling.
- Related: [[tcp-congestion-control]] for the sender-side window, [[tcp-vs-udp]] for the overall picture, [[sockets-and-connection-pooling]] for buffer behaviour of pooled connections.
