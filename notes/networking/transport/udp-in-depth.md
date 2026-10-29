---
title: UDP in depth
tags: [networking, udp, transport, ddos]
learned: 2026-10-29
---
## TL;DR
UDP adds exactly two things on top of IP: ports (so the kernel can hand a packet to the right socket) and an optional checksum. The header is 8 bytes; there is no connection, no ordering, no retransmission, no flow or congestion control. Each `sendto()` is one datagram and each `recvfrom()` returns exactly one datagram, so message boundaries are preserved, which TCP does not do. That minimalism makes UDP fast, stateless and cheap to serve, and it also makes it easy to spoof and abuse for amplification attacks.

## Key points
### The datagram header
| Field | Bits | Meaning |
|---|---|---|
| Source port | 16 | sender's port, where replies go; may be 0 over IPv4 if no reply is expected |
| Destination port | 16 | which socket on the receiver gets the datagram |
| Length | 16 | header + payload in bytes, minimum 8; caps the payload at 65,507 bytes over IPv4 (65,535 − 8 − 20) |
| Checksum | 16 | covers a pseudo-header (source/destination IP, protocol, length), the header and the payload; optional over IPv4 (0 = not computed), mandatory over IPv6; a bad checksum means a silent drop |

- The IP header ([[ip-packet-structure]]) carries the addresses; UDP only adds ports. The pair "IP + port" on both ends is what lets many applications share one host: the kernel **demultiplexes** each arriving datagram by destination port (and, for connected sockets, by source too).

### What you get and what you do not
- **Message boundaries**: send 3 datagrams of 100 bytes, read 3 datagrams of 100 bytes (if they all arrive). A buffer smaller than the datagram truncates it; the rest is lost.
- **No connection**: one socket serves every client. The server calls `recvfrom()`, learns the peer address from the result, and replies with `sendto()`. No `listen()`, no `accept()`, no per-client state in the kernel ([[building-tcp-udp-servers]]).
- **No ordering, no delivery guarantee, no duplicate suppression**: datagrams can arrive late, reordered, twice, or never.
- **No flow control**: if the application reads slower than datagrams arrive, the socket receive buffer fills and the kernel drops new ones (`UdpRcvbufErrors` in `nstat`).
- **No congestion control**: a UDP sender keeps blasting at full speed on a congested link. Protocols on UDP (QUIC, WebRTC) must implement their own.
- **Connected UDP**: calling `connect()` on a UDP socket only fixes the default peer. No packets are exchanged, but the kernel filters out datagrams from other sources and reports ICMP "port unreachable" as `ECONNREFUSED` on the next call ([[icmp-ping-traceroute]]).

### Pros and cons
| Pros | Cons |
|---|---|
| no handshake: the first packet already carries data | loss, reordering and duplicates are the application's problem |
| 8-byte header, small CPU and memory cost per packet | no flow or congestion control; easy to overload receiver or network |
| stateless server scales to millions of clients on one socket | NAT mappings for UDP expire fast (often 30–120 s idle), so long-lived flows need keep-alive packets |
| no head-of-line blocking ([[head-of-line-blocking]]) | many corporate firewalls block UDP except DNS; QUIC falls back to TCP |
| broadcast and multicast | source address is trivially spoofed: no handshake proves who sent it |

### Where UDP is used
- **DNS**: a query and an answer fit in one datagram each ([[dns-resolution]]). Large answers set the TC (truncated) bit and the client retries over TCP.
- **QUIC / HTTP/3**: reliability, per-stream ordering, congestion control and TLS 1.3 rebuilt in user space ([[http-1-1-vs-2-vs-3]]).
- **Real-time media, VoIP** (RTP, WebRTC), games: a late frame is worthless, retransmitting it only adds latency.
- **Metrics and logs**: StatsD (port 8125), syslog. Losing 0.1% of counter increments is fine; blocking a request thread on a dead metrics server is not ([[otel-metric-types]]).
- DHCP, NTP, WireGuard, VXLAN overlays in container networks ([[docker-networking]]).

### Spoofing and amplification
- Because there is no handshake, an attacker can put the **victim's IP** in the source field. Every reply goes to the victim.
- **Amplification**: pick a service whose answer is much larger than the request. DNS `ANY` queries (up to ~50×), NTP `monlist` (~500×), and exposed memcached over UDP (up to ~50,000×) turned small botnets into terabit floods.
- Defences: ISPs drop packets whose source cannot come from their network (ingress filtering, BCP 38); servers rate-limit responses (DNS RRL) and never expose memcached/Redis to the internet. QUIC forbids a server from sending more than 3× the bytes it received before the client's address is validated.
- TCP is immune to this exact trick: a spoofed SYN gets a SYN-ACK of about the same size, and no data flows without the real client completing the handshake.

## Example
A DNS lookup on the wire (one datagram each way, no handshake) and the drop counters to check when a UDP service "loses" data.
```bash
$ sudo tcpdump -ni any -c 2 udp port 53
IP 10.0.0.5.53412 > 10.0.0.2.53: 4660+ A? example.com. (29)
IP 10.0.0.2.53 > 10.0.0.5.53412: 4660 1/0/0 A 93.184.215.14 (45)
# UDP header of the query, 8 bytes: d0a4 0035 0025 xxxx
#   d0a4 = src port 53412, 0035 = dst port 53, 0025 = length 37 (8 header + 29 DNS), then checksum

$ nstat -az | grep -E 'UdpInDatagrams|UdpRcvbufErrors|UdpInErrors'
UdpInDatagrams     1823344    0.0
UdpRcvbufErrors       5121    0.0   # receive buffer full: the app read too slowly
UdpInErrors           5121    0.0
```

## Pitfalls
- Sending datagrams larger than the path MTU: IP fragments them, and losing any fragment loses the datagram. Keep payloads around 1200–1400 bytes ([[mss-mtu-pmtud]]).
- A small `SO_RCVBUF` on a busy StatsD or syslog receiver drops bursts silently. Raise it (and `net.core.rmem_max`) and watch `UdpRcvbufErrors`.
- Treating "no error from `send()`" as delivery. It only means the datagram reached the local kernel.
- In Docker, `-p 8125:8125` maps only TCP. UDP needs `-p 8125:8125/udp`.
- Related: [[tcp-vs-udp]] for the side-by-side comparison, [[socket-management]] for how the kernel queues datagrams on a socket.
