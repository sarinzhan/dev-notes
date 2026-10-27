---
title: IP addressing, ports and NAT
tags: [networking, ip, nat, docker, kubernetes]
learned: 2026-10-27
---
## TL;DR
An IPv4 address plus a CIDR mask says which hosts are on the same subnet and which need a router. Private ranges (10/8, 172.16/12, 192.168/16) never appear on the public internet; NAT rewrites them to a public address on the way out and remembers the mapping to route replies back. Ports (0–65535) multiplex many sockets on one address. Containers add one more layer of all this: every Docker network and every Kubernetes pod network is a private subnet with NAT (or a CNI route) in front, and "the port is open in the container but not reachable" is almost always a binding or publishing mistake, not a firewall.

## Key points
### Addresses and subnets
- IPv4: 32 bits, dotted decimal. `10.20.5.17/24` means the first 24 bits are the network (`10.20.5.0`), the host part is 8 bits (254 usable hosts; `.0` is the network, `.255` the broadcast).
- Same subnet → deliver directly via ARP; different subnet → send to the **default gateway** (`ip route`). A host with no route to a destination fails at once; with a route through a dead gateway it times out.
- Private ranges (RFC 1918): `10.0.0.0/8`, `172.16.0.0/12` (Docker's default bridge uses `172.17.0.0/16`), `192.168.0.0/16`. Also `127.0.0.0/8` loopback, `169.254.0.0/16` link-local (cloud metadata lives at `169.254.169.254`), `100.64.0.0/10` carrier-grade NAT.
- IPv6: 128 bits, hex groups, `::` compresses zeros. No NAT needed (every host gets a global address), ARP replaced by NDP, `/64` per subnet. Dual-stack is the norm; a JVM prefers IPv6 when available (`-Djava.net.preferIPv4Stack=true` to force v4).
- Special addresses to recognise: `0.0.0.0` "all interfaces" when binding, `::` the same for IPv6, `localhost` resolves to both `127.0.0.1` and `::1`.

### Ports
| Range | Name | Notes |
|---|---|---|
| 0–1023 | well-known | binding needs root or `CAP_NET_BIND_SERVICE`; 22, 53, 80, 443 |
| 1024–49151 | registered | 5432 Postgres, 6379 Redis, 9092 Kafka, 8080 Tomcat, 4317/4318 OTLP |
| 32768–60999 | ephemeral (Linux) | assigned to outgoing connections; exhaustion in [[tcp-handshake-and-connection-states]] |

- A socket is identified by `(protocol, local ip, local port, remote ip, remote port)`, so one server port serves thousands of clients; TCP and UDP port spaces are independent.
- `bind()` to `127.0.0.1:8080` accepts only local connections; to `0.0.0.0:8080` accepts from every interface. In Spring Boot that is `server.address`. Inside a container, `127.0.0.1` is the container's own loopback, so a service bound there is unreachable from the host or other containers: the number one "port is open but connection refused" cause.
- `SO_REUSEADDR` lets a restarted server bind while old connections sit in `TIME_WAIT` (JVM sets it by default on server sockets).

### NAT
- **SNAT / masquerade**: outgoing packets from a private subnet get the gateway's public source address and a new source port; the gateway keeps a connection-tracking table to map replies back. Many hosts share one public IP; this is what your home router, the office firewall and Docker's bridge do.
- **DNAT / port forwarding**: incoming packets to `public:443` are rewritten to `10.20.5.17:8443`. `docker run -p 8080:8080` installs exactly this iptables rule ([[linux-networking]]).
- Consequences: a server behind NAT cannot be reached unless a mapping exists; idle connections disappear when the conntrack entry expires (often 5 minutes for UDP, ~1 hour or less for idle TCP), hence keep-alive; the server sees the NAT's address, not the client's, so proxies add `X-Forwarded-For` ([[reverse-proxy-nginx-basics]]).
- Conntrack table full (`nf_conntrack: table full, dropping packet`) silently drops new connections on busy NAT hosts and Kubernetes nodes; raise `nf_conntrack_max`.

### Containers and Kubernetes
- Docker bridge network: each container gets `172.17.0.x`; containers on a user-defined network resolve each other by name through the embedded DNS at `127.0.0.11`; outbound traffic is masqueraded; `-p` is DNAT ([[docker-fundamentals]]).
- Kubernetes: every pod gets its own IP routable inside the cluster (no NAT pod-to-pod; the CNI does routing or an overlay). A `Service` has a virtual **ClusterIP** that kube-proxy implements with DNAT rules or IPVS; `NodePort` opens a port on every node; `LoadBalancer` asks the cloud for an external IP in front of that ([[kubernetes-basics]], [[dns-resolution]]).
- `hostNetwork: true` / `--network host` skips all of it: the container shares the host's stack and ports; useful for packet capture, dangerous for port conflicts.

## Example
Tracing why a Spring Boot app in a container answers `curl localhost:8080` from inside but not from the host.
```bash
# inside the container: the app bound to loopback only
$ ss -ltnp
State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      100        127.0.0.1:8080       0.0.0.0:*     users:(("java",pid=1,fd=42))

# on the host: DNAT rule is there, but it forwards to 172.17.0.3:8080 where nothing listens on that address
$ sudo iptables -t nat -L DOCKER -n
DNAT  tcp  --  0.0.0.0/0  0.0.0.0/0  tcp dpt:8080 to:172.17.0.3:8080
$ curl -sS --max-time 2 http://localhost:8080/actuator/health
curl: (56) Recv failure: Connection reset by peer
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
- Overlapping subnets: a Docker network on `172.17.0.0/16` and a corporate VPN on the same range make the VPN hosts unreachable from containers. Set `default-address-pools` in `daemon.json` or choose `10.x` ranges for Compose networks.
- Hard-coding pod IPs or container IPs; they change on every restart. Use DNS names and Services.
- Assuming the client IP you see is real; behind NAT or a load balancer it is the NAT address, and `X-Forwarded-For` can be spoofed unless the proxy overwrites it.
- Running out of conntrack entries on a Kubernetes node under a connection storm looks like random packet loss.
- Related: [[osi-and-tcp-ip-models]] for the layer picture, [[sockets-and-connection-pooling]] for what a socket is from the application side.
