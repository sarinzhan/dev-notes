---
title: ICMP, ping and traceroute
tags: [networking, icmp, ping, traceroute, debugging]
learned: 2026-10-29
order: 4
---
## TL;DR
ICMP is the control and error channel of IP: routers and hosts use it to say "unreachable", "TTL expired" or "too big, fragment needed". It has no ports and carries no application data. `ping` sends ICMP Echo Requests and times the Echo Replies; `traceroute` sends probes with TTL 1, 2, 3… and lists the routers that answer with Time Exceeded. Blocking all ICMP looks like hardening but breaks path MTU discovery and makes outages harder to diagnose; filter by type instead.

## Key points
### ICMP basics
- Carried directly in IP (protocol number 1; ICMPv6 is next header 58). Each message has a **type**, a **code** and a checksum; error messages include the IP header and first 8 bytes of the packet that caused them, so the sender can match the error to a socket (those 8 bytes contain the TCP/UDP ports).
- ICMP errors are never sent about other ICMP errors, about broadcasts, or about non-first fragments, to avoid storms.
- Routers rate-limit ICMP generation in their control plane, so replies from routers are slower and less reliable than forwarded traffic.

| Type / code | Name | Who sends it, when |
|---|---|---|
| 0 | Echo Reply | answer to ping |
| 8 | Echo Request | ping |
| 3 / 0 | Net Unreachable | router has no route |
| 3 / 1 | Host Unreachable | last router could not ARP the host |
| 3 / 3 | Port Unreachable | host got UDP for a closed port (TCP answers RST instead) |
| 3 / 4 | Fragmentation Needed | packet bigger than MTU with DF set; carries the next-hop MTU |
| 3 / 13 | Administratively Prohibited | firewall rejected (instead of silently dropping) |
| 5 | Redirect | "use a better gateway on your subnet"; usually ignored by servers |
| 11 / 0 | Time Exceeded | TTL hit 0 in transit |

- In IPv6 ICMPv6 is mandatory: Packet Too Big (type 2) replaces fragmentation by routers, and Neighbor Discovery replaces ARP ([[arp]]).

### ping
- Sends Echo Requests with an identifier and sequence number; measures RTT per reply and reports loss. Linux sends one per second; `-c` count, `-i` interval, `-s` payload size, `-M do` to set DF (MTU probing; see [[ip-packet-structure]]).
- The `ttl=` in a reply is the remaining TTL on arrival: 64 − hops for a Linux target.
- What a successful ping proves: L3 path both ways and the host's kernel are alive. It does not prove the port is open, the app is healthy or the firewall allows TCP. What a failed ping proves: little, since ICMP is often filtered (cloud security groups block it by default).
- For service checks use L4/L7 probes: `nc -zv host 5432`, `curl` on a health endpoint, or Java's `InetAddress.isReachable`, which tries ICMP only when the process has the privilege and otherwise falls back to a TCP connection to port 7.

### traceroute
- Sends a probe with TTL 1. The first router decrements it to 0, drops it and returns Time Exceeded from its own address: hop 1 found. Then TTL 2, 3, … until the destination answers.
- Probe type: Linux `traceroute` uses UDP to ports 33434+ by default (the destination answers Port Unreachable); `-I` uses ICMP echo (Windows `tracert` does this); `-T -p 443` uses TCP SYN and gets through firewalls that allow only the service port.
- Three probes per hop, so three RTTs per line. `* * *` means no reply within the timeout: the router does not send Time Exceeded, or rate-limits it. If later hops answer, the path is fine.
- RTT on a middle hop includes that router's slow-path ICMP generation; a hop with high latency followed by low-latency hops is not the bottleneck. Real latency increases persist to the end.
- Return paths can differ from forward paths, and ECMP load balancing can send each probe on a different route; `mtr` and `tracepath` (which also reports the path MTU) give a steadier picture.
- Switches and L2 devices never appear: they do not touch TTL ([[host-to-host-communication]]).

```anim
traceroute-ttl
```

### Why blocking all ICMP hurts
- **PMTUD breaks**: TCP sets DF and relies on Fragmentation Needed to learn that a tunnel or VPN has a smaller MTU. If a firewall drops it, large segments disappear silently. Symptom: the TCP handshake and small requests work, big responses or TLS handshakes with long certificate chains hang. This is the "PMTUD black hole" ([[mss-mtu-pmtud]]).
- Unreachables are lost, so clients wait for timeouts instead of failing fast.
- Diagnostics vanish: no ping, no traceroute during an incident.
- Sensible policy: allow Echo Request/Reply (rate-limited), Destination Unreachable (especially code 4), Time Exceeded, and on IPv6 Packet Too Big and Neighbor Discovery. Drop Redirect and the obsolete types.

## Example
A quick bottom-up check from an app host to a database that "times out":
```bash
$ ping -c 3 -W 1 10.0.2.15
PING 10.0.2.15 (10.0.2.15) 56(84) bytes of data.
64 bytes from 10.0.2.15: icmp_seq=1 ttl=63 time=0.52 ms   # ttl 63: one router in between
64 bytes from 10.0.2.15: icmp_seq=2 ttl=63 time=0.48 ms
64 bytes from 10.0.2.15: icmp_seq=3 ttl=63 time=0.50 ms
--- 10.0.2.15 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 2003ms

# L3 is fine; trace the actual service port with TCP probes
$ sudo traceroute -n -T -p 5432 10.0.2.15
 1  10.0.1.1   0.41 ms  0.37 ms  0.35 ms
 2  * * *
 3  * * *
# the router answers but nothing gets past it on 5432: a firewall drops SYNs to the DB port
$ tracepath -n 10.0.2.15    # also prints pmtu along the path
```

## Pitfalls
- Concluding "the server is down" from a failed ping. Many hosts and every default cloud security group drop ICMP; test the real port.
- Reading `* * *` in the middle of a trace as packet loss. Only stars that continue to the end, or loss that persists in `mtr` to the last hop, mean trouble.
- Blocking ICMP type 3 code 4 (or ICMPv6 type 2) on a firewall or security group in front of a VPN, overlay network or Kubernetes cluster ([[docker-networking]]).
- Pinging in a loop from monitoring with `-i 0.01`: routers deprioritise ICMP, and you measure their CPU, not the network.
- Related: [[network-routing]] for the paths traceroute reveals, [[network-debugging-toolkit]] for the next tools after ping, [[ip-packet-structure]] for the TTL and DF fields.
