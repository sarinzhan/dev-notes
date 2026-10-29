---
title: ARP: resolving IP addresses to MAC addresses
tags: [networking, arp, ethernet, security]
learned: 2026-10-29
order: 3
---
## TL;DR
To put an IP packet on an Ethernet or Wi-Fi link, the sender needs the MAC address of the next hop. ARP finds it: a broadcast "who has 10.0.1.20?" and a unicast "10.0.1.20 is at bb:bb:…" reply, cached for a while in the kernel's neighbour table. For a destination outside the subnet the host ARPs for the default gateway, never for the remote IP. ARP has no authentication, so anyone on the same L2 segment can lie (ARP poisoning). IPv6 replaces ARP with Neighbor Discovery over ICMPv6.

## Key points
### Request and reply
- **Request**: Ethernet destination `ff:ff:ff:ff:ff:ff` (broadcast), EtherType `0x0806`, body "who has *target IP*, tell *sender IP* / *sender MAC*". The switch floods it to every port in the VLAN.
- **Reply**: unicast back to the requester's MAC: "*target IP* is at *target MAC*". Every other host drops the request.
- The target also learns the requester's mapping from the request itself, so the reply path needs no second ARP.
- ARP lives between L2 and L3: it is carried directly in Ethernet frames, not inside IP. It only works within one broadcast domain; routers do not forward it.

```anim
arp-resolution
```

### The ARP cache (neighbour table)
- Linux keeps entries in states: `REACHABLE` (confirmed recently), `STALE` (still used, revalidated on next send), `DELAY`/`PROBE` (checking), `FAILED` (no answer), `PERMANENT` (static).
- An entry stays `REACHABLE` for roughly 15–45 seconds (randomised `base_reachable_time`), then goes `STALE`; TCP traffic that gets ACKs confirms it without new ARP requests.
- While ARP is unresolved, the kernel queues a few packets for that neighbour; if ARP fails, the sender gets `EHOSTUNREACH` ("No route to host") after about three seconds, even on the same subnet.
- Large flat networks or Kubernetes nodes with thousands of pods can overflow the table (`neighbour table overflow` in `dmesg`); raise `net.ipv4.neigh.default.gc_thresh1/2/3`.

### Why you ARP for the gateway, not the remote host
- MAC addresses only mean something on one link. A web server in another country is not on your LAN, so nobody would answer an ARP for its IP.
- The kernel looks up the route ([[network-routing]]): `8.8.8.8 via 10.0.1.1`. It resolves the MAC of `10.0.1.1` and sends the frame there with destination IP `8.8.8.8`. The router then ARPs on its outbound link for its own next hop ([[host-to-host-communication]]).
- Consequence: on a host that talks only to remote services, the ARP cache contains just the gateway (and maybe a DNS server on the LAN).

### Gratuitous ARP
- An unsolicited announcement of your own mapping, sent as a broadcast request or reply where sender IP = target IP.
- Uses: detecting duplicate IPs at boot (if someone answers, the address is taken); updating everyone's cache after a NIC change; **failover** of a virtual IP. keepalived/VRRP, Pacemaker, Patroni with a VIP and cloud-less load balancer pairs move an IP to a standby machine and broadcast a gratuitous ARP so traffic follows within milliseconds.
- In public clouds this does not work: AWS, GCP and Azure ignore guest ARP announcements, so VIP failover goes through the provider's API (reassigning an Elastic IP or secondary IP).

### ARP poisoning (spoofing)
- An attacker on the same L2 segment sends forged replies, for example "the gateway's IP is at my MAC". Hosts accept unsolicited replies and update their cache, so traffic flows through the attacker: a man in the middle who can read, modify or drop it.
- Defences: dynamic ARP inspection and DHCP snooping on managed switches, port security, static ARP entries for critical neighbours, segmenting networks into small VLANs, and above all encrypting everything at L7 so a MITM sees only TLS ([[tls-handshake-and-certificates]], [[mtls-and-service-to-service-auth]]).

### IPv6: Neighbor Discovery
- NDP does ARP's job with ICMPv6: Neighbor Solicitation (sent to a solicited-node multicast address, not a broadcast) and Neighbor Advertisement. It also handles router discovery and address autoconfiguration (SLAAC).
- It has the same trust problem; RA guard and SEND exist but are rarely deployed.

## Example
Inspecting and exercising ARP on a Linux host:
```bash
# neighbour table: the gateway and one local peer
$ ip neigh show dev eth0
10.0.1.1 lladdr 52:54:00:00:01:01 REACHABLE
10.0.1.20 lladdr bb:bb:bb:00:00:14 STALE

# watch ARP on the wire while forcing a fresh lookup
$ sudo ip neigh flush dev eth0 to 10.0.1.20
$ sudo tcpdump -nn -e -i eth0 arp &
$ ping -c 1 10.0.1.20
aa:aa:aa:00:00:0a > ff:ff:ff:ff:ff:ff, ethertype ARP (0x0806), length 42:
  Request who-has 10.0.1.20 tell 10.0.1.10, length 28
bb:bb:bb:00:00:14 > aa:aa:aa:00:00:0a, ethertype ARP (0x0806), length 60:
  Reply 10.0.1.20 is-at bb:bb:bb:00:00:14, length 46

# check for a duplicate IP before assigning it (exit code 1 = somebody answered)
$ sudo arping -D -I eth0 -c 2 10.0.1.50
# announce a moved VIP to every neighbour (what keepalived does on failover)
$ sudo arping -U -I eth0 -c 3 10.0.1.100
```

## Pitfalls
- A server is replaced with a new one on the same IP, and some clients cannot reach it for a minute: their caches still hold the old MAC. Send a gratuitous ARP on startup (`arping -U`) or wait for entries to expire.
- Two hosts with the same IP: connections work, then break, then work, as the ARP cache flips between two MACs. `arping -D` before assigning static addresses.
- `No route to host` on a local address is usually failed ARP (host down, wrong VLAN, host firewall dropping ARP), not a routing problem.
- Trusting a shared L2 network (office Wi-Fi, a flat data centre VLAN) because "it is internal". ARP spoofing needs only a laptop on that segment.
- Related: [[host-to-host-communication]] for where ARP fits in the delivery of a packet, [[ip-addressing-and-nat]] for subnets that define who is "local", [[network-debugging-toolkit]] for the rest of the toolbox.
