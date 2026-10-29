---
title: MSS, MTU and Path MTU discovery
tags: [networking, tcp, mtu, ip, performance]
learned: 2026-10-29
order: 1
---
## TL;DR
The **MTU** is the largest IP packet a link can carry (1500 bytes on Ethernet, 9000 with jumbo frames). TCP's **MSS** is the largest payload it puts in one segment: MTU − 40 bytes of IPv4 and TCP headers, so 1460 on Ethernet. Each side announces its MSS in the SYN, but the smallest MTU on the *path* is what counts, and TCP finds it with **Path MTU discovery**: send with the Don't Fragment bit, shrink when a router answers with ICMP "fragmentation needed". When a firewall drops that ICMP, large packets vanish silently (a **PMTUD black hole**): the handshake works, small requests work, big responses hang. Tunnels (VPN, VXLAN, Docker overlay, WireGuard) add headers and lower the usable MTU, which is the usual way to hit this.

## Key points
### MTU and MSS
- **MTU** (maximum transmission unit) is a property of a link layer, counted from the start of the IP header. Ethernet: 1500. Jumbo frames: 9000, used inside data centres and storage networks; every device on the L2 segment must agree.
- **MSS** (maximum segment size) counts only TCP payload. IPv4 header 20 + TCP header 20 = 40 bytes, so MSS = 1460 at MTU 1500. IPv6 has a 40-byte header, so MSS = 1440. TCP options (timestamps take 12 bytes) shrink the real payload per segment further, typically to 1448.
- Header overhead is ~3% at full-size segments and ~30% for 100-byte writes sent one per packet, the motivation for [[nagle-and-delayed-ack|Nagle's algorithm]]. Jumbo frames cut per-packet CPU work for bulk transfer (replication, backups), not latency.

### MSS negotiation
- Each side puts its own MSS option in the SYN / SYN-ACK, derived from its outgoing interface MTU. Each sender then uses the smaller of its own and the peer's value ([[tcp-handshake-and-connection-states]]).
- A smaller link in the middle is invisible to this; that is what PMTUD is for.

### Fragmentation vs Don't Fragment
- IPv4 routers may **fragment** a packet that is larger than the next link's MTU. If any fragment is lost the whole packet is lost, and later fragments carry no port numbers (bad for firewalls and load balancers), so fragmentation is avoided ([[ip-packet-structure]]).
- TCP on modern stacks sets the **DF** (Don't Fragment) bit on every packet. A router that cannot forward a DF packet drops it and sends ICMP type 3 code 4, "fragmentation needed", with the next-hop MTU ([[icmp-ping-traceroute]]).
- IPv6 routers never fragment; only the sender can. The equivalent message is ICMPv6 "Packet Too Big", and blocking it breaks IPv6 outright.
- UDP has no MSS: an application that sends a 4000-byte datagram gets IP fragmentation, or with DF a send error. DNS over UDP and QUIC keep datagrams around 1200–1450 bytes for this reason ([[udp-in-depth]]).

### Path MTU discovery
- The sender starts with the MSS-derived size, sets DF, and on "fragmentation needed" lowers its path MTU for that destination (cached in the route table, visible in `ip route get`) and retransmits smaller segments.
- **Black hole**: a firewall or security group drops all ICMP "for security". The big packet is dropped, the sender never learns why, retransmits the same size until it gives up. Symptoms: TCP connects, TLS handshake may or may not complete (the certificate chain is the first big packet), `GET /health` works, a 50 KB JSON response hangs, SSH logs in and freezes on `ls -la`.
- **PLPMTUD** (RFC 4821, `net.ipv4.tcp_mtu_probing=1`) probes the size with real TCP segments instead of trusting ICMP, which survives black holes. QUIC does the same by design.
- **MSS clamping**: a router or VPN gateway rewrites the MSS option in passing SYNs down to what the tunnel can carry, so endpoints never send too big. `iptables ... -j TCPMSS --clamp-mss-to-pmtu` is the classic rule; most home routers do it on PPPoE (MTU 1492).

### Tunnels lower the MTU
| Encapsulation | Overhead | Inner MTU on a 1500 link |
|---|---|---|
| PPPoE | 8 | 1492 |
| GRE | 24 | 1476 |
| IPsec (ESP, tunnel mode) | 50–70 | ~1400–1440 |
| WireGuard | 60 (IPv4) / 80 (IPv6) | 1420 (its default) |
| VXLAN (Docker overlay, Flannel, many CNIs) | 50 | 1450 |

- A container whose interface says MTU 1500 on a VXLAN overlay sends packets that do not fit. Docker overlay networks set 1450 by default, but a custom bridge on a host whose uplink is already a VPN may not; set `com.docker.network.driver.mtu` or the daemon `mtu` option ([[docker-networking]]). Kubernetes CNIs (Calico, Flannel, Cilium) have their own MTU settings ([[kubernetes-basics]]).

## Example
Finding the real path MTU and checking a connection:
```bash
# Largest payload that passes with DF set: 1472 + 8 (ICMP) + 20 (IP) = 1500
$ ping -M do -s 1472 -c 2 db.internal
ping: local error: message too long, mtu=1450        # our own interface is smaller (VXLAN)
$ ping -M do -s 1422 -c 2 db.internal
2 packets transmitted, 2 received                     # 1422 + 28 = 1450 fits

$ tracepath -n db.internal                             # reports pmtu per hop, no root needed
 1?: [LOCALHOST]   pmtu 1450
 3:  10.20.0.7     0.912ms reached
     Resume: pmtu 1450 hops 3 back 3

$ ss -ti dst 10.20.0.7 | grep -o 'mss:[0-9]* pmtu:[0-9]*'
mss:1398 pmtu:1450

# Docker Compose network on a host behind WireGuard
#   networks:
#     backend:
#       driver_opts:
#         com.docker.network.driver.mtu: "1380"
```

## Pitfalls
- "Blocking ICMP is more secure" breaks PMTUD. Allow at least ICMP type 3 code 4 (and ICMPv6 type 2) in security groups and host firewalls.
- Small requests working while large ones hang is the signature of an MTU problem, not of a slow backend. Test with `ping -M do -s` before reading application logs.
- Jumbo frames on one host and 1500 on the switch or peer produce drops; the MSS option will look fine because each side only reports its own interface.
- Related: [[tcp-segment-structure]] for the header fields behind the 40 bytes, [[network-debugging-toolkit]] and [[tcpdump]] for catching the ICMP messages on the wire.
