---
title: IP packet structure and fragmentation
tags: [networking, ip, ipv4, ipv6, fragmentation]
learned: 2026-10-29
order: 2
---
## TL;DR
An IPv4 packet is a 20–60 byte header plus a payload, up to 65,535 bytes total, though in practice it is limited by the link MTU (1500 on Ethernet). The header carries the source and destination addresses, a TTL that stops routing loops, a protocol number that says what is inside (TCP, UDP, ICMP), and fields for fragmentation. Fragmentation lets a router split a packet that is too big for the next link, but it is slow and fragile, so modern stacks set the Don't Fragment bit and discover the path MTU instead. IPv6 simplifies the header to a fixed 40 bytes and removes fragmentation from routers entirely.

## Key points
### IPv4 header fields
| Field | Size | What it does |
|---|---|---|
| Version | 4 bits | `4` |
| IHL | 4 bits | header length in 32-bit words: 5 (20 bytes) to 15 (60 bytes); more than 5 means options |
| DSCP / ECN | 6 + 2 bits | was "Type of Service". DSCP marks priority classes (VoIP gets EF = 46); ECN lets routers signal congestion without dropping |
| Total length | 16 bits | header + payload in bytes, max 65,535 |
| Identification | 16 bits | same value on all fragments of one original packet |
| Flags | 3 bits | reserved, **DF** (Don't Fragment), **MF** (More Fragments) |
| Fragment offset | 13 bits | where this fragment's data starts, in 8-byte units |
| TTL | 8 bits | decremented by every router; at 0 the packet is dropped and ICMP Time Exceeded is sent back ([[icmp-ping-traceroute]]). Linux starts at 64, Windows at 128 |
| Protocol | 8 bits | 1 ICMP, 6 TCP, 17 UDP, 47 GRE, 50 ESP (IPsec) |
| Header checksum | 16 bits | covers only the header; recomputed at each hop because TTL changes |
| Source / destination | 32 + 32 bits | IP addresses; rewritten only by NAT ([[ip-addressing-and-nat]]) |
| Options | 0–40 bytes | record route, timestamps; rarely used, often dropped by firewalls |

- The header does not contain ports. Those are in the TCP/UDP header inside the payload ([[tcp-segment-structure]], [[udp-in-depth]]); a router that reads ports (L4 load balancer, NAT, firewall) is looking past IP.
- The checksum does not protect the payload; TCP and UDP have their own checksums, and Ethernet has a CRC per frame.
- TTL is a hop count, not a time. Its real job is to kill packets caught in a routing loop; traceroute abuses it to discover the path.

### Fragmentation
- Each link has an MTU: the largest IP packet it can carry (1500 on Ethernet, often 1450 or less inside VXLAN or VPN tunnels, 9001 with jumbo frames in AWS).
- If a packet is larger than the next link's MTU and DF is clear, the router splits it: every fragment gets a copy of the header with the same Identification, its own offset, and MF = 1 except on the last. Only the destination reassembles.
- Example: a 4000-byte packet (3980 bytes of data) over MTU 1500 becomes fragments of 1480 + 1480 + 1020 data bytes, offsets 0, 185, 370.
- Why everyone avoids it: losing one fragment loses the whole packet; reassembly costs memory on the receiver (and was a classic DoS target); fragments after the first carry no ports, so firewalls, NAT and load balancers cannot classify them.
- If DF is set and the packet is too big, the router drops it and returns ICMP "Fragmentation Needed" (type 3, code 4) with the next-hop MTU. TCP uses this for path MTU discovery and lowers its segment size ([[mss-mtu-pmtud]]). Linux sets DF on TCP by default.
- UDP is where fragmentation still bites: a large DNS response or a big syslog/StatsD datagram over 1472 bytes payload (1500 − 20 IP − 8 UDP) gets fragmented or dropped.

### IPv6 differences
- Fixed 40-byte header: version, traffic class, flow label, payload length, next header, hop limit, 128-bit source and destination.
- No header checksum (L2 and L4 already check), no fragmentation fields in the base header, options moved to chained extension headers (`next header`).
- Routers never fragment. The sender must do path MTU discovery; a too-big packet triggers ICMPv6 "Packet Too Big". The minimum MTU every IPv6 link must support is 1280.
- Hop limit is TTL renamed. Flow label lets routers keep one flow on one path without reading L4 headers.

## Example
Seeing the header fields and forcing fragmentation behaviour from a shell:
```bash
# verbose capture shows TTL, id, flags, total length and protocol for each packet
$ sudo tcpdump -nn -v -i eth0 icmp -c 2
IP (tos 0x0, ttl 64, id 40211, offset 0, flags [DF], proto ICMP (1), length 84)
    10.0.1.10 > 10.0.1.20: ICMP echo request, id 7, seq 1, length 64
IP (tos 0x0, ttl 64, id 5512, offset 0, flags [none], proto ICMP (1), length 84)
    10.0.1.20 > 10.0.1.10: ICMP echo reply, id 7, seq 1, length 64

# 1472 bytes payload + 8 ICMP + 20 IP = 1500: fits with DF set
$ ping -c 1 -M do -s 1472 10.0.1.20
1480 bytes from 10.0.1.20: icmp_seq=1 ttl=64 time=0.41 ms
# one byte more with DF: refused locally instead of being fragmented
$ ping -c 1 -M do -s 1473 10.0.1.20
ping: local error: message too long, mtu=1500
# without DF the kernel fragments it; the capture shows two pieces with the same id
$ ping -c 1 -M dont -s 3000 10.0.1.20
$ sudo tcpdump -nn -v -i eth0 'ip[6:2] & 0x3fff != 0'   # only fragments (MF set or offset > 0)
```

## Pitfalls
- Blocking all ICMP on a firewall. "Fragmentation Needed" never arrives, DF packets vanish silently, and connections hang after the handshake when the first full-size segment is sent ([[mss-mtu-pmtud]]).
- MTU mismatch in overlay networks: Docker Swarm overlay, Kubernetes VXLAN or a VPN adds 50+ bytes of encapsulation. If the inner MTU is still 1500, large responses stall while small requests work ([[docker-networking]]).
- Reading TTL in a capture as distance without knowing the start value: TTL 57 on arrival from a Linux host means about 7 hops, from Windows it would be 128 − 57.
- Assuming UDP datagrams up to 64 KB are fine. They fragment, and one lost fragment loses the whole message; keep UDP payloads under about 1200 bytes when crossing the internet.
- Related: [[ip-addressing-and-nat]] for what the address fields mean, [[osi-and-tcp-ip-models]] for where this header sits in the stack, [[tcpdump]] for capture filters on header bytes.
