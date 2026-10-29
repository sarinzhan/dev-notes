---
title: Network routing: tables, longest-prefix match and the default gateway
tags: [networking, routing, ip, nat, linux]
learned: 2026-10-29
order: 5
---
## TL;DR
Every host and router decides each packet's next hop with a routing table: a list of prefixes, each pointing to an interface and optionally a gateway. The most specific matching prefix wins (longest-prefix match), and `0.0.0.0/0`, the default route, catches everything else. Routing is hop-by-hop: each router only knows the next step, never the whole path. A packet from a private address reaches the internet only because some router on the way performs NAT; the reply finds its way back through that router's translation table.

## Key points
### The routing table
- Each entry: destination prefix, `dev` (outgoing interface), optional `via` (next-hop gateway), `src` (preferred source address), `metric` (tie-breaker), `proto` (who installed it: kernel, dhcp, static, bgp).
- **Connected routes** appear automatically when an address is assigned: `10.0.1.10/24` on `eth0` installs `10.0.1.0/24 dev eth0 scope link`. No gateway: destinations there are reached directly via [[arp]].
- **Default route** `default via 10.0.1.1`: anything not matched elsewhere goes to that gateway. A typical server has exactly one connected route and one default route.
- A host with no matching route fails immediately (`Network is unreachable`); a host whose route points to a dead gateway fails slowly (ARP for the gateway fails, then `No route to host` or a connect timeout).
- Linux has several tables and policy rules (`ip rule`) for source-based routing, used by VPN clients, Kubernetes CNIs and multi-homed hosts; `ip route get` shows the final decision after all of them.

### Longest-prefix match
- Among all entries that contain the destination, the one with the longest mask wins; only on a tie does the metric decide.
- Given `0.0.0.0/0 via 10.0.1.1`, `10.0.0.0/8 via 10.0.1.254` (VPN) and `10.0.1.0/24 dev eth0`, the destination `10.0.1.20` matches all three and uses `/24` (direct); `10.8.3.4` uses `/8` (VPN); `8.8.8.8` uses the default.
- This is why a VPN that pushes `10.0.0.0/8` can steal traffic from a Docker network on `10.x` if the Docker route is less specific, and why overlapping subnets cause "works from my laptop, not from the container" problems ([[ip-addressing-and-nat]]).

### Hop-by-hop forwarding
- At each router: strip the frame, look up the destination IP, decrement TTL, ARP for the next hop, send a new frame ([[host-to-host-communication]]). Source and destination IPs stay the same unless NAT is configured.
- Routers learn routes statically or via routing protocols: OSPF/IS-IS inside one organisation, BGP between organisations (the internet is roughly 1 million BGP prefixes stitched together by autonomous systems).
- Return traffic is routed independently and can take a different path (asymmetric routing). Stateful firewalls and NAT on only one of the paths then drop replies.

### Worked example: across two subnets
Two subnets joined by router R (`eth0 10.0.1.1`, `eth1 10.0.2.1`). App host A `10.0.1.10/24`, database B `10.0.2.15/24`.
1. A connects to `10.0.2.15:5432`. Table on A: `10.0.1.0/24 dev eth0` does not match; `default via 10.0.1.1` does. Next hop: R.
2. A ARPs for `10.0.1.1`, sends the frame to R's MAC with IP destination `10.0.2.15`.
3. R's table: `10.0.2.0/24 dev eth1` matches (connected). R decrements TTL, ARPs for `10.0.2.15` on `eth1` and forwards.
4. B replies to `10.0.1.10`; its own default route sends the reply via `10.0.2.1`; R delivers it on `eth0`.
5. If B had a wrong gateway, A's SYN arrives and B's SYN-ACK leaves into nowhere: from A this looks like a connect timeout, though `tcpdump` on B shows the SYN arriving.

### Private IPs and NAT on the way out: airline Wi-Fi
- A passenger's laptop joins the cabin Wi-Fi and gets `10.20.0.57/22` by DHCP, with gateway `10.20.0.1` (the aircraft's server). Hundreds of other laptops on the plane share the same private range, and so do thousands of other planes; private addresses are not unique and never routed on the internet.
- Laptop → `10.20.0.1`: the onboard router has a default route via the satellite link and performs **SNAT**: source `10.20.0.57:51000` becomes its own address, for example `100.72.4.9:23011`, and the mapping is recorded in its NAT table.
- The provider's ground station may NAT again (carrier-grade NAT) to a public IP `198.51.100.20:40500`. The web server sees only that address, shared by many planes.
- The reply arrives at `198.51.100.20:40500`; each NAT looks up its table, rewrites the destination back step by step, and the laptop receives a packet addressed to `10.20.0.57:51000`.
- Consequences: nobody on the internet can open a connection to the laptop (no mapping exists until it talks first); idle connections die when a NAT entry expires; one public IP represents many users, so IP-based rate limits and bans hit everyone on the plane. NAT details: [[ip-addressing-and-nat]].

## Example
Reading and testing routes on a Linux host or inside a container:
```bash
$ ip route
default via 10.0.1.1 dev eth0 proto dhcp src 10.0.1.10 metric 100
10.0.0.0/8 via 10.0.1.254 dev eth0 proto static           # corporate VPN gateway
10.0.1.0/24 dev eth0 proto kernel scope link src 10.0.1.10
172.18.0.0/16 dev br-3f2a proto kernel scope link src 172.18.0.1   # a Docker network

# which entry wins for a destination (after policy rules and longest-prefix match)
$ ip route get 10.0.1.20
10.0.1.20 dev eth0 src 10.0.1.10 uid 1000
$ ip route get 10.8.3.4
10.8.3.4 via 10.0.1.254 dev eth0 src 10.0.1.10 uid 1000
$ ip route get 8.8.8.8
8.8.8.8 via 10.0.1.1 dev eth0 src 10.0.1.10 uid 1000

# add a more specific route for a partner network, then remove it
$ sudo ip route add 192.0.2.0/24 via 10.0.1.254 dev eth0
$ sudo ip route del 192.0.2.0/24
```

## Pitfalls
- Overlapping ranges: a Compose network or Kubernetes pod CIDR on the same `10.x` or `172.x` prefix as the office VPN makes some internal hosts unreachable; the more specific route quietly wins.
- Two default routes (Wi-Fi and Ethernet, or a VPN that adds its own) with similar metrics: traffic flips between interfaces and long-lived connections reset.
- Asymmetric routing through a stateful firewall: the firewall sees only SYN-ACKs (or only SYNs) and drops them as invalid.
- Debugging with `ping` alone; use `ip route get` first to see which path the kernel will actually use, then [[icmp-ping-traceroute]] to see where it stops.
- Related: [[ip-packet-structure]] for TTL, [[docker-networking]] for how Docker adds routes and NAT rules, [[linux-networking]] for the full packet path through netfilter.
