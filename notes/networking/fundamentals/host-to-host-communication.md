---
title: Host-to-host communication: MAC, IP, ports, switches and routers
tags: [networking, fundamentals, mac, routing, client-server]
learned: 2026-10-29
order: 2
---
## TL;DR
Every message between two machines needs three addresses: a MAC address to cross one link, an IP address to cross networks, and a port to reach the right process. If the destination IP is in your own subnet, the host sends the frame straight to it; otherwise it sends the frame to the default gateway's MAC while the IP header still names the final destination. Switches forward frames by MAC and never touch IP; routers strip the frame, decrement TTL, pick the next hop and build a new frame. IP addresses and ports stay the same end to end (unless NAT rewrites them); MAC addresses change at every router.

## Key points
### Client-server in one paragraph
- A **server** is a process that owns a listening socket on a known IP and port and waits; a **client** is a process that opens a connection to it from an ephemeral port. Your Spring Boot service is a server for its HTTP callers and a client of Postgres, Kafka and every REST API it calls ([[sockets-and-connection-pooling]]).
- The split exists so that expensive or shared work (data, business rules) lives in one place and many thin clients reuse it. The price is that every call now crosses a network with latency, loss and partial failure ([[failure-modes-and-timeouts]]).

### Three addresses, three jobs
| Address | Layer | Scope | Who assigns | Example |
|---|---|---|---|---|
| MAC | L2 | one link / broadcast domain | burned into the NIC (or generated for virtual NICs) | `02:42:ac:11:00:02` |
| IP | L3 | whole internet (or private network) | DHCP, static config, cloud, Docker | `10.0.1.10` |
| Port | L4 | one host | the OS (ephemeral) or the app (`bind`) | `51514 → 443` |

- An analogy that holds: the IP is the building, the port is the apartment, the MAC is the address on the courier's envelope for the current leg of the trip only.
- Why not route by MAC? MACs are flat and carry no location, so a router would need a table entry for every device on earth. IP addresses are hierarchical: one route for `10.0.2.0/24` covers 254 hosts ([[ip-addressing-and-nat]]).

### Same subnet vs via a gateway
- The sender compares the destination IP with its own address and mask (in practice, the kernel looks up the routing table; see [[network-routing]]).
- **Same subnet**: resolve the destination's MAC with [[arp]], send the frame directly. One switch hop at most.
- **Different subnet**: resolve the **gateway's** MAC and send the frame there. The IP header still says the real destination. The gateway repeats the decision on its side.
- A host with a wrong mask gets this decision wrong: it tries to ARP for a remote host (no answer) or sends local traffic via the router.

### Switch vs router
| | Switch | Router |
|---|---|---|
| Layer | L2 | L3 |
| Looks at | destination MAC | destination IP |
| Table | MAC table, learned from source MACs of incoming frames | routing table, configured or learned via OSPF/BGP |
| Unknown destination | floods to all ports | drops (or sends to default route) |
| Changes the packet | nothing | new L2 header, TTL − 1, IP checksum; NAT if configured |
| Boundary | one broadcast domain | separates broadcast domains |

- A "layer 3 switch" is a switch with routing in hardware; it behaves as a router between VLANs.
- A Docker bridge (`docker0`) is a software switch; the host itself is the router for its containers ([[docker-networking]]).

### What happens at each hop
1. **Sender**: app writes to a socket; the kernel adds a TCP header (ports), an IP header (IPs, TTL 64) and an Ethernet header (its MAC → next hop's MAC). Structure of these headers: [[ip-packet-structure]], [[tcp-segment-structure]].
2. **Switch**: learns the source MAC on the incoming port, forwards out of the port for the destination MAC. Nothing else changes.
3. **Router**: accepts the frame because the destination MAC is its own, removes the Ethernet header, looks up the destination IP, decrements TTL (drops and sends ICMP Time Exceeded at 0; see [[icmp-ping-traceroute]]), ARPs for the next hop and builds a new frame.
4. **Receiver**: NIC accepts the frame for its MAC, the kernel checks the IP is local, reads the protocol field (6 = TCP, 17 = UDP) and delivers to the socket matching the 4-tuple.

```anim
host-to-host-packet
```

## Example
Watch the MAC change while the IP stays on a host that talks to an external API. Run two captures at once: one on the app host, one on the router or NAT host (or the Docker host's bridge).
```bash
# on the app host: dst MAC is the gateway's, dst IP is the remote API
$ ip route get 93.184.216.34
93.184.216.34 via 10.0.1.1 dev eth0 src 10.0.1.10 uid 1000
$ ip neigh show 10.0.1.1
10.0.1.1 dev eth0 lladdr 52:54:00:00:01:01 REACHABLE
$ sudo tcpdump -nn -e -v -i eth0 host 93.184.216.34 -c 1
aa:aa:aa:00:00:0a > 52:54:00:00:01:01, ethertype IPv4, length 74:
  (ttl 64, id 0, proto TCP (6)) 10.0.1.10.51514 > 93.184.216.34.443: Flags [S]

# on the router, outbound side: new MACs, TTL 63, and here NAT also rewrote the source
$ sudo tcpdump -nn -e -v -i wan0 host 93.184.216.34 -c 1
52:54:00:00:02:01 > 00:1c:73:aa:10:01, ethertype IPv4, length 74:
  (ttl 63, id 0, proto TCP (6)) 198.51.100.7.40122 > 93.184.216.34.443: Flags [S]
```

## Pitfalls
- Expecting `ping` to an IP on the same subnet to need the gateway. If ARP fails (wrong VLAN, firewall on the host), local traffic fails even though the gateway is fine.
- Forgetting that a switch floods frames for unknown MACs: a capture on any port shows some of other hosts' traffic until the MAC table learns.
- Reading MAC addresses in a capture as the real endpoints. On a routed path they are the neighbours on that one link; the endpoints are in the IP header.
- Assuming the source IP is the client's. Any NAT or L7 proxy on the path replaces it ([[ip-addressing-and-nat]], [[reverse-proxy-nginx-basics]]).
- Related: [[osi-and-tcp-ip-models]] for the layer vocabulary, [[network-routing]] for how the next hop is chosen, [[tcpdump]] for reading captures.
