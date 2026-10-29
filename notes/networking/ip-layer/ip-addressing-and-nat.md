---
title: IP addressing and NAT
tags: [networking, ip, nat, docker, kubernetes]
learned: 2026-10-27
aliases: [ip-addressing-ports-nat]
order: 1
---
## TL;DR
An IPv4 address plus a CIDR mask says which hosts are on the same subnet and which need a router. Private ranges (10/8, 172.16/12, 192.168/16) never appear on the public internet; NAT rewrites them to a public address on the way out, records the mapping in a connection-tracking table, and uses it to send replies back. NAT is why billions of devices share a few public IPv4 addresses, and also why nobody can connect *to* a host behind it without extra work. Containers add one more layer: every Docker network and every Kubernetes pod network is a private subnet with NAT (or a CNI route) in front, and "the port is open in the container but not reachable" is almost always a binding or publishing mistake, not a firewall.

## Key points
### Addresses and subnets
- IPv4: 32 bits, dotted decimal. `10.20.5.17/24` means the first 24 bits are the network (`10.20.5.0`), the host part is 8 bits (254 usable hosts; `.0` is the network, `.255` the broadcast).
- Usable hosts = 2^(32 − prefix) − 2. Common sizes:

| Prefix | Mask | Addresses | Typical use |
|---|---|---|---|
| /32 | 255.255.255.255 | 1 | one host in a firewall rule or route |
| /30, /31 | 255.255.255.252 | 4 / 2 | point-to-point router links |
| /24 | 255.255.255.0 | 256 | classic LAN, one Docker Compose network |
| /20 | 255.255.240.0 | 4096 | default AWS subnet size in a default VPC |
| /16 | 255.255.0.0 | 65,536 | a VPC, Docker's default bridge `172.17.0.0/16` |

- Splitting: `10.0.0.0/16` holds 256 `/24`s or 16 `/20`s. Two prefixes overlap if one contains the other; overlap between networks that must talk (VPC peering, VPN, Docker vs corporate LAN) is the most common addressing mistake.
- Same subnet means direct delivery, otherwise the packet goes to a gateway; how the kernel decides is in [[network-routing]].
- Cloud providers reserve some addresses in every subnet (AWS: the first four and the last), so a `/28` gives 11 usable IPs, not 14.

### Special ranges
| Range | Meaning |
|---|---|
| `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` | private (RFC 1918), reusable by anyone, never routed on the internet |
| `100.64.0.0/10` | carrier-grade NAT, shared between ISP and customer |
| `127.0.0.0/8` | loopback; `localhost` resolves to `127.0.0.1` and `::1` |
| `169.254.0.0/16` | link-local; cloud metadata at `169.254.169.254` |
| `0.0.0.0` / `::` | "all interfaces" when binding a socket |
| `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24` | documentation examples |

- IPv6: 128 bits, hex groups, `::` compresses zeros. Every host can have a global address, so NAT is unnecessary; ARP is replaced by NDP ([[arp]]), `/64` per subnet. Dual-stack is the norm; a JVM prefers IPv4 by default unless `-Djava.net.preferIPv6Addresses=true`, and `-Djava.net.preferIPv4Stack=true` disables IPv6 completely.
- Ports, the L4 address that multiplexes sockets on one IP, are covered in [[tcp-vs-udp]].

### How NAT works
- **SNAT / masquerade** (outbound): the NAT router replaces the private source `10.0.0.5:51000` with its public address and a free port, e.g. `203.0.113.7:40001`. This port rewriting (PAT, or NAPT) is what lets thousands of hosts share one address: the 16-bit port becomes the per-connection key. Masquerade is SNAT that picks the outgoing interface's current address, for dynamic IPs.
- **DNAT / port forwarding** (inbound): packets to `public:443` are rewritten to `10.0.0.17:8443`. `docker run -p 8080:8080`, a Kubernetes `NodePort`, and a cloud load balancer in front of private instances are all DNAT ([[load-balancers-l4-vs-l7]]).
- **The NAT table**: one entry per flow, keyed by the 5-tuple on both sides:

| Inside (orig.) | Outside (translated) | Remote | State | Expires |
|---|---|---|---|---|
| `10.0.0.5:51000` | `203.0.113.7:40001` | `93.184.216.34:443` TCP | ESTABLISHED | 5 days (Linux default) |
| `10.0.0.9:51000` | `203.0.113.7:40002` | `93.184.216.34:443` TCP | ESTABLISHED | 5 days |
| `10.0.0.5:53124` | `203.0.113.7:40003` | `8.8.8.8:53` UDP | — | 30–180 s |

  A reply to `203.0.113.7:40002` is looked up, rewritten to `10.0.0.9:51000`, and routed inside. A packet that matches no entry is dropped.
- On Linux the table is **conntrack** (netfilter connection tracking); iptables/nftables NAT rules only run for the first packet of a flow, later packets follow the conntrack entry. `conntrack -L` lists it.
- Timeouts matter: cloud NAT gateways and firewalls expire idle TCP far sooner than Linux (AWS NAT gateway: 350 s). An idle pooled connection to a database across a NAT is silently dropped, and the next query hangs until TCP retransmissions give up. Fix with TCP keepalive or pool `maxLifetime`/`keepaliveTime` shorter than the timeout ([[hikaricp-pool-sizing]], [[network-access-to-databases]]).
- Port exhaustion: one public IP has about 64,000 ports per destination IP and port. A service hammering one external API through one NAT IP can run out (AWS reports `ErrorPortAllocation`); add NAT IPs or reuse connections ([[sockets-and-connection-pooling]]).

### NAT traversal and CG-NAT
- Behind NAT a host cannot accept incoming connections until it sends something out first. Peer-to-peer apps (WebRTC calls, games, VPN meshes like Tailscale) work around this:
  - **STUN**: ask a public server "what address and port do you see me as?".
  - **Hole punching**: both peers send UDP to each other's discovered address at the same time; each NAT sees outgoing traffic and creates a mapping that lets the other side's packets in.
  - **TURN**: when hole punching fails (symmetric NAT, which picks a new external port per destination), relay all traffic through a public server. **ICE** tries direct, then STUN, then TURN.
- **CG-NAT** (carrier-grade): mobile operators and many ISPs put customers on `100.64.0.0/10` and NAT again at the provider. The home router's "public" IP is not public; port forwarding on it does nothing, and one public IP is shared by hundreds of subscribers, so IP-based rate limiting or blocking on your API hits all of them.
- The real fix is IPv6, where end-to-end addressing returns and NAT is not needed; firewalls still block unsolicited inbound traffic.

### Containers and Kubernetes
- Docker bridge network: each container gets an address like `172.17.0.x`; containers on a user-defined network resolve each other by name through the embedded DNS at `127.0.0.11`; outbound traffic is masqueraded; `-p` is DNAT ([[docker-networking]], [[docker-fundamentals]]).
- Kubernetes: every pod gets its own IP routable inside the cluster (no NAT pod-to-pod; the CNI does routing or an overlay). A `Service` has a virtual **ClusterIP** that kube-proxy implements with DNAT rules or IPVS; `NodePort` opens a port on every node; `LoadBalancer` asks the cloud for an external IP in front of that ([[kubernetes-basics]], [[dns-resolution]]). Traffic from pods to outside the cluster is SNATed to the node IP.
- `hostNetwork: true` / `--network host` skips all of it: the container shares the host's stack and ports; useful for packet capture, dangerous for port conflicts.

## Example
Tracing why a Spring Boot app in a container answers `curl localhost:8080` from inside but not from the host, then looking at the NAT state.
```bash
# inside the container: the app bound to loopback only
$ ss -ltnp
State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      100        127.0.0.1:8080       0.0.0.0:*     users:(("java",pid=1,fd=42))

# on the host: the DNAT rule is there, but it forwards to 172.17.0.3:8080 where nothing listens on that address
$ sudo iptables -t nat -L DOCKER -n
DNAT  tcp  --  0.0.0.0/0  0.0.0.0/0  tcp dpt:8080 to:172.17.0.3:8080
$ curl -sS --max-time 2 http://localhost:8080/actuator/health
curl: (56) Recv failure: Connection reset by peer

# after fixing server.address, a client from the LAN connects; conntrack shows both sides of the DNAT
$ sudo conntrack -L -p tcp --dport 8080
tcp 6 431999 ESTABLISHED src=192.168.1.50 dst=10.0.1.10 sport=40612 dport=8080 src=172.17.0.3 dst=192.168.1.50 sport=8080 dport=40612 [ASSURED]
```
Fix in `application.yml` (or leave the default, which is already all interfaces, and remove the override somebody added for "security"):
```yaml
server:
  address: 0.0.0.0      # bind on every interface inside the container
  port: 8080
```
Then restrict exposure at the Docker level instead: `-p 127.0.0.1:8080:8080` publishes only to the host's loopback.

## Pitfalls
- Binding to `localhost` inside a container, then blaming the firewall. Check `ss -ltn` inside first.
- Overlapping subnets: a Docker network on `172.17.0.0/16` and a corporate VPN on the same range make the VPN hosts unreachable from containers. Set `default-address-pools` in `daemon.json` or choose dedicated `10.x` ranges for Compose networks.
- Hard-coding pod IPs or container IPs; they change on every restart. Use DNS names and Services.
- Assuming the client IP you see is real; behind NAT or a load balancer it is the NAT address, and `X-Forwarded-For` can be spoofed unless the proxy overwrites it ([[reverse-proxy-nginx-basics]]).
- Conntrack table full (`nf_conntrack: table full, dropping packet`) on a busy NAT host or Kubernetes node looks like random packet loss; raise `nf_conntrack_max` and shorten timeouts.
- Related: [[network-routing]] for how packets reach the NAT box, [[osi-and-tcp-ip-models]] for the layer picture, [[ip-packet-structure]] for the fields NAT rewrites.
