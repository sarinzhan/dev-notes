---
title: Head-of-line blocking
tags: [networking, tcp, http2, quic, performance]
learned: 2026-10-29
order: 4
---
## TL;DR
Head-of-line (HOL) blocking is when the first item in an ordered queue holds up everything behind it, even items that are ready. HTTP/1.1 has it at the application level: responses on one connection must come back in request order, so a slow response blocks the next one. HTTP/2 fixes that with multiplexed streams, but moves the problem down into TCP: TCP delivers one ordered byte stream, so a single lost segment stops `read()` for every stream until the retransmission arrives, even though later data for other streams is already sitting in the kernel. QUIC orders data per stream instead of per connection, so a loss stalls only the stream whose bytes were in the lost packet.

## Key points
### HTTP/1.1: ordering at the application level
- Without pipelining, one connection carries one request at a time; clients open ~6 connections per origin to get parallelism.
- With pipelining, a client sends several requests without waiting, but the server must return responses in the same order. A slow `/report` blocks a fast `/health` queued behind it. Buggy proxies and this ordering rule are why browsers never enabled pipelining ([[http-1-1-vs-2-vs-3]]).

### HTTP/2: the problem moves into TCP
- HTTP/2 splits each request and response into frames tagged with a stream id and interleaves them on one TCP connection. At the HTTP layer nothing waits for anything else.
- But TCP's contract is an ordered, reliable byte stream. The receiver's kernel tracks the next expected sequence number; segments that arrive after a gap are kept in the out-of-order queue and not delivered to the application ([[tcp-segment-structure]]).
- Scenario: segments 1–6 carry frames of streams A, B and C. Segment 2 (stream B) is lost. Segments 3–6 arrive and are ACKed with SACK blocks, yet `read()` returns nothing past segment 1. Streams A and C, whose later frames are complete in the buffer, wait for B's retransmission.
- How long: fast retransmit after three duplicate ACKs takes at least one extra RTT; if the loss is at the tail and no duplicate ACKs come, the sender waits for the retransmission timeout (200 ms minimum on Linux, often more).
- Loss also halves the congestion window ([[tcp-congestion-control]]). With six HTTP/1.1 connections only one of them slows down; with one HTTP/2 connection every stream does. Measurements show HTTP/2 losing to HTTP/1.1 at around 1–2% packet loss, typical of mobile networks.
- On clean data-centre networks loss is rare, which is why HTTP/2 and gRPC between services work well in practice ([[grpc-and-protobuf]]).

```anim
tcp-hol-blocking
```

### QUIC: per-stream ordering
- QUIC packets carry STREAM frames with (stream id, offset). Reassembly and in-order delivery happen per stream, so a lost packet creates a gap only in the streams it carried.
- Retransmission is of frames, not packets: lost data is re-sent in a new packet with a new packet number, which also removes TCP's ambiguity about which copy an ACK refers to.
- What is still shared: the congestion window and the connection-level flow-control limit. A loss still slows the whole connection's send rate, it just does not block delivery of other streams.
- Header compression had to change too: HPACK assumes headers are decoded in arrival order, so HTTP/3 uses QPACK, which lets the encoder avoid references that would make one stream wait for another.
- QUIC runs in user space over UDP, which is why this could be deployed without changing kernels and middleboxes ([[udp-in-depth]], [[tcp-vs-udp]]).

### The same pattern elsewhere
| Where | What blocks | Usual fix |
|---|---|---|
| HTTP/1.1 pipelining | slow response blocks later ones | more connections, HTTP/2 |
| HTTP/2 over TCP | lost segment blocks all streams | HTTP/3, or several connections on lossy links |
| TLS records | a record is decrypted only when complete | smaller records at connection start |
| Kafka partition | a slow or failing record blocks the partition for its consumer | retry topics, DLT ([[spring-kafka-consumer]]) |
| Connection pool | slow queries hold all connections | timeouts, separate pools ([[hikaricp-pool-sizing]]) |
| Load balancer queue | one slow backend fills a shared queue | least-connections, per-backend limits |

## Example
Reproducing TCP-level HOL on a Linux test box: add loss and delay, then compare one multiplexed connection with several HTTP/1.1 connections using `h2load` (from nghttp2).
```bash
# 50 ms delay and 2% random loss on the interface towards the test server
sudo tc qdisc add dev eth0 root netem delay 50ms loss 2%

# 100 streams multiplexed over 1 HTTP/2 connection
h2load -n 2000 -c 1 -m 100 https://test.internal/api/tariffs

# 6 HTTP/1.1 connections, one request at a time each
h2load -n 2000 -c 6 -m 1 --h1 https://test.internal/api/tariffs

# compare "time for request" p90/max: the single h2 connection shows long tails
sudo tc qdisc del dev eth0 root
```

## Pitfalls
- HTTP/2's "no HOL blocking" claim is about the HTTP layer only; on lossy links a single h2 connection can be slower than several HTTP/1.1 connections.
- One gRPC channel to a hot service puts all calls behind one TCP connection's loss recovery and one congestion window; large clients often open a few channels.
- QUIC removes cross-stream blocking, not loss: a stream still waits for its own lost data, and congestion control still slows everything.
- Blaming the server for periodic 200 ms+ latency spikes that are actually RTO-driven retransmissions; check retransmission counters (`nstat -az | grep -i retrans`) or a [[tcpdump]] capture.
- Related: [[connection-establishment-cost]] for why everything is pushed onto one connection in the first place, [[nagle-and-delayed-ack]] for another source of fixed-size delays.
