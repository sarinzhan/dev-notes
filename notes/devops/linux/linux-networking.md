---
title: Linux networking stack
tags: [linux, networking, tcp, iproute2, dns]
learned: 2026-10-22
---
## TL;DR
A packet leaving your application goes socket -> kernel TCP/UDP -> IP routing -> netfilter (firewall) -> network interface driver -> wire, and arrives in reverse. To operate a server you need the layer model (ARP, IP, ICMP, TCP/UDP), the `iproute2` tools (`ip a`, `ip r`, `ss`), the firewall (`nftables`, or `iptables` on older systems) and how DNS and DHCP give a host its names and addresses.

## Key points
### Path of a packet
1. Application calls `write()`/`send()` on a socket (a file descriptor); for the JVM this is `SocketChannel` or the blocking `Socket` ([[java-nio]], [[java-io-streams]]).
2. Transport layer: TCP segments the data, assigns sequence numbers, handles retransmission and flow/congestion control; UDP just adds ports and a checksum.
3. Network layer: IP picks the next hop from the **routing table** (`ip r`), sets TTL, fragments if needed.
4. Netfilter hooks: the firewall and NAT rules see the packet here (`nftables`/`iptables`); Docker's port publishing is a DNAT rule ([[docker-fundamentals]]).
5. Link layer: ARP resolves the next hop's IP to a MAC address (`ip neigh`), the driver puts the frame into the NIC's ring buffer, DMA sends it.
6. Receive side: NIC interrupt -> driver -> IP -> TCP matches the 4-tuple (src ip, src port, dst ip, dst port) to a socket -> data lands in the socket receive buffer -> the application's `read()` returns.

### Protocols you must be able to explain
- **ARP**: maps IPv4 address to MAC on the local segment; broadcast "who has 10.0.0.1?". Stale entries explain "the server moved but nobody can reach it".
- **IP**: addressing and routing; CIDR (`10.20.0.0/16`), default gateway, private ranges (10/8, 172.16/12, 192.168/16). IPv6 has no ARP (uses NDP) and no NAT in the normal case.
- **ICMP**: control messages; `ping` (echo), `traceroute` (TTL expiry), "destination unreachable", path MTU discovery. Blocking all ICMP breaks PMTU and makes large packets vanish.
- **TCP**: connection-oriented, ordered, reliable. Three-way handshake (SYN, SYN-ACK, ACK), four-way close, `TIME_WAIT` on the side that closes first (keeps the 4-tuple for 60 s). Nagle, delayed ACK, keepalive. Socket states show up in `ss -t`.
- **UDP**: connectionless datagrams; DNS, DHCP, QUIC/HTTP3, metrics (StatsD), syslog.
- **DNS**: name -> address. Resolver config in `/etc/resolv.conf` (often `systemd-resolved` at 127.0.0.53); `/etc/hosts` first per `/etc/nsswitch.conf`. Record types A/AAAA, CNAME, SRV, TXT; TTL controls caching. The JVM caches successful lookups forever by default when a `SecurityManager` is set, otherwise 30 s (`networkaddress.cache.ttl`); set it low when service IPs change (Kubernetes, Swarm VIPs, [[docker-swarm]]).
- **DHCP**: a host broadcasts DISCOVER, the server OFFERs an address, lease, gateway and DNS servers; the host REQUESTs and gets an ACK. Servers usually use static addresses or reservations instead.

### iproute2 (replaces ifconfig/route/netstat)
| Task | Command |
|---|---|
| Interfaces and addresses | `ip a` (`ip addr show`), `ip -br a` |
| Link state, MTU, up/down | `ip link`, `ip link set eth0 up` |
| Routing table | `ip r`, `ip route get 8.8.8.8` (which interface/gateway will be used) |
| ARP cache | `ip neigh` |
| Sockets and listeners | `ss -ltnp` (listening TCP with process), `ss -s` (summary), `ss -tan state established` |
| Network namespaces | `ip netns list`, `ip netns exec <ns> ss -ltn` |
| Live traffic | `tcpdump -i eth0 -nn port 5432`, `tcpdump -w cap.pcap` and open in Wireshark |
| Reachability | `ping`, `traceroute`/`mtr`, `nc -zv host 443`, `curl -v`, `dig +short api.example.com` |

### Firewall
- **nftables** is the current framework (default since Debian 10, RHEL 8); `iptables` is a compatibility front end over it (`iptables-nft`). Concepts: tables -> chains (hooked at `input`, `forward`, `output`, `prerouting`, `postrouting`) -> rules with verdicts (`accept`, `drop`, `reject`).
- A stateful rule `ct state established,related accept` lets replies through so you only need to open inbound ports.
- `ufw` (Ubuntu) and `firewalld` (RHEL) are wrappers. Docker inserts its own `DOCKER` chains and bypasses `ufw` for published ports; publish on `127.0.0.1:port` if a service must stay local.

## Example
```bash
# "The app cannot reach Postgres" triage, in order
ip -br a                                  # does the host have the address and is the link UP
ip route get 10.20.5.10                   # which interface/gateway will be used
dig +short db.internal                    # does the name resolve, and to what
nc -zv db.internal 5432                   # TCP handshake only: open, refused, or timeout
ss -tanp | grep 5432                      # what connections the JVM already holds (pool size check)
sudo tcpdump -nn -i eth0 host 10.20.5.10 and port 5432 -c 20   # SYN with no SYN-ACK = firewall/route

# Minimal nftables policy for an API host
sudo nft -f - <<'EOF'
table inet filter {
  chain input {
    type filter hook input priority 0; policy drop;
    ct state established,related accept
    iif lo accept
    ip protocol icmp accept
    tcp dport { 22, 443 } accept
    tcp dport 8080 ip saddr 10.20.0.0/16 accept   # app port only from the internal network
  }
}
EOF
```

## Pitfalls
- "Connection refused" means the host answered with RST (nothing listening, or listening on 127.0.0.1 only); "timeout" means a firewall or route dropped it. They point to different fixes.
- `TIME_WAIT` piles up when a client opens a new connection per request; use a connection pool ([[hikaricp-pool-sizing]], [[rest-clients]]).
- `netstat` is deprecated and often missing from minimal images; learn `ss`. Inside a container `ss` may be absent too; use `nsenter` from the host ([[linux-architecture-and-kernel]]).
- Published Docker ports are reachable from outside even with `ufw` enabled.
- Related: [[linux-commands]], [[docker-swarm]], [[rest-and-http]].
