---
title: OSI and TCP/IP models with real examples
tags: [networking, osi, tcp-ip, fundamentals]
learned: 2026-10-27
---
## TL;DR
The OSI model is a seven-layer teaching vocabulary; the TCP/IP model is the four-layer stack that actually runs. For a backend developer the useful mapping is: HTTP/gRPC/DNS are application protocols, TLS sits between them and TCP, TCP/UDP give you ports and (for TCP) reliability, IP routes packets between networks, Ethernet/Wi-Fi move frames on one link. Every "layer N" phrase in a job interview (L4 vs L7 load balancer, L3 switch, L2 network) refers to the OSI numbering, so learn the numbers even though nobody implements OSI.

## Key points
### The two models side by side
| OSI | TCP/IP | What travels | Addressing | Real examples |
|---|---|---|---|---|
| 7 Application | Application | message | URL, hostname | HTTP, gRPC, DNS, SMTP, Kafka protocol, Postgres wire protocol |
| 6 Presentation | Application | message | — | TLS encryption (often placed here), JSON/Protobuf encoding, gzip |
| 5 Session | Application | message | — | TLS session resumption, HTTP/2 streams, WebSocket connection |
| 4 Transport | Transport | segment / datagram | port | TCP, UDP, QUIC |
| 3 Network | Internet | packet | IP address | IPv4, IPv6, ICMP, routers |
| 2 Data link | Link | frame | MAC address | Ethernet, Wi-Fi, ARP, switches, VLANs |
| 1 Physical | Link | bits | — | cables, radio, fibre |

- Layers 5–7 of OSI collapse into one TCP/IP application layer; in practice everybody says "L7" for anything above TCP.
- **Encapsulation**: each layer wraps the one above with its own header. An HTTP request becomes `Ethernet[ IP[ TCP[ TLS[ HTTP ] ] ] ]`. Receiving reverses it; this is why a packet capture shows nested headers ([[network-debugging-toolkit]]).
- A layer talks to its peer on the other host through the layers below, and only knows the interface of the layer directly below it. That is why you can swap TCP for QUIC under HTTP ([[http-1-1-vs-2-vs-3]]) without changing the application.

### Which layer am I debugging?
- **L2**: wrong VLAN, ARP cache stale after a server moved, MTU mismatch. Symptoms: host unreachable on the same subnet, large packets vanish.
- **L3**: no route, wrong gateway, firewall dropping ICMP, NAT problems ([[ip-addressing-ports-nat]]). Symptoms: `ping` fails, `traceroute` stops.
- **L4**: port closed, SYN dropped by a firewall, connection reset, exhausted ephemeral ports ([[tcp-handshake-and-connection-states]]). Symptoms: "connection refused" vs "timeout".
- **L5–6**: TLS handshake failure, certificate not trusted, wrong SNI ([[tls-handshake-and-certificates]]).
- **L7**: 4xx/5xx, wrong `Host` header, CORS, slow upstream ([[http-fundamentals]]).
- Working bottom-up with one tool per layer is the fastest triage; the Linux side of that walk is in [[linux-networking]].

### Where common infrastructure sits
| Device / component | Layer it works at | Why it matters |
|---|---|---|
| Switch | L2 (MAC) | Cannot route between subnets |
| Router, Kubernetes CNI, VPN | L3 (IP) | Decides next hop; NAT lives here |
| L4 load balancer, `iptables`, kube-proxy | L4 (IP + port) | Forwards TCP flows without reading HTTP ([[load-balancers-l4-vs-l7]]) |
| nginx, Envoy, API gateway, CDN | L7 (HTTP) | Can route by path/header, terminate TLS, cache ([[reverse-proxy-nginx-basics]], [[cdn]]) |
| Service mesh sidecar | L4 + L7 | mTLS and retries per request ([[mtls-and-service-to-service-auth]]) |

### Interview-style questions folded in
- "What is the difference between TCP and IP?" IP delivers packets host-to-host with no guarantee; TCP, on top, delivers an ordered byte stream process-to-process using ports ([[tcp-vs-udp]]).
- "Where does TLS belong?" Between transport and application; OSI purists say presentation/session. The practical answer: it wraps the TCP byte stream and the application does not see the difference.
- "Why do we still mention OSI?" Vocabulary. "L7 routing" and "L4 proxy" are meaningful only through OSI numbering.

## Example
One request from a Spring Boot service to Postgres, layer by layer, as seen in a capture. The `-e` flag prints link-layer addresses, so each line shows all four TCP/IP layers at once:
```bash
sudo tcpdump -nn -e -i eth0 port 5432 -c 3
# L2 src MAC -> dst MAC      L3 src IP -> dst IP        L4 TCP flags/ports
02:42:ac:11:00:02 > 02:42:ac:11:00:03, ethertype IPv4 (0x0800), length 74:
  10.20.5.2.52914 > 10.20.5.10.5432: Flags [S], seq 1831221184, win 64240, options [mss 1460,...]
02:42:ac:11:00:03 > 02:42:ac:11:00:02, ethertype IPv4 (0x0800), length 74:
  10.20.5.10.5432 > 10.20.5.2.52914: Flags [S.], seq 2993241, ack 1831221185, win 65160, ...
02:42:ac:11:00:02 > 02:42:ac:11:00:03, ethertype IPv4 (0x0800), length 66:
  10.20.5.2.52914 > 10.20.5.10.5432: Flags [.], ack 1, win 502, ...
# After this the L7 payload (Postgres StartupMessage, then SSLRequest) flows inside TCP.
```
Reading it: MAC addresses are the Docker bridge interfaces (L2), `10.20.5.2 -> 10.20.5.10` is routing (L3), `52914 -> 5432` are the ephemeral and server ports (L4), and `[S]`, `[S.]`, `[.]` is the three-way handshake.

## Pitfalls
- Treating OSI as how the kernel is built. Linux has sockets, TCP/UDP, IP, netfilter and drivers; there is no "session layer" code anywhere.
- Saying "L7 load balancer" when the product only forwards TCP (many cloud "network" load balancers are L4). The difference decides whether you get path routing, header-based canaries and TLS termination.
- Forgetting that NAT and firewalls rewrite or drop at L3/L4 before your HTTP code ever runs; a `504` from a gateway often means a dropped SYN two layers down.
- Thinking HTTP/3 "replaces TCP with UDP"; QUIC reimplements reliability inside UDP, the layering still holds.
- Related: [[tcp-vs-udp]] for the transport layer in depth, [[linux-networking]] for the packet path through the kernel.
