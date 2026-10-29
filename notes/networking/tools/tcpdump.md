---
title: Capturing traffic with tcpdump
tags: [networking, tcpdump, troubleshooting, pcap, bpf]
learned: 2026-10-29
---
## TL;DR
`tcpdump` prints, or saves to a `.pcap` file, the packets that cross an interface, filtered in the kernel by a BPF expression such as `host 10.20.5.10 and tcp port 5432`. It answers questions that logs cannot: did the SYN leave, did anything come back, who sent the RST, which DNS server was asked and what it answered. Capture with `-nn` and a tight filter, write to a file with `-w` for anything longer than a few lines, and open the file in Wireshark. In containers, run it inside the container's network namespace rather than guessing from the host.

## Key points
### Command shape
`tcpdump [options] [filter]`, run as root (or with `CAP_NET_RAW`).
| Option | Meaning |
|---|---|
| `-i eth0` / `-i any` | interface; `any` captures all of them (Linux "cooked" header, no MAC addresses) |
| `-nn` | no DNS lookups for addresses, no names for ports; faster and unambiguous |
| `-c 50` | stop after 50 packets |
| `-v`, `-vv` | more header detail: TTL, IP ID, total length, checksums |
| `-e` | show link-layer header (MACs), needed for ARP debugging on a real NIC |
| `-A` / `-X` | payload as ASCII / as hex + ASCII; readable only for plain-text protocols |
| `-w file.pcap` / `-r file.pcap` | write raw packets / read them back (filters work on `-r` too) |
| `-C 100 -W 5` | rotate files every 100 MB, keep 5: a ring buffer for long captures |
| `-U`, `-l` | flush each packet (`-w -` piping) / line-buffer text output (piping to `grep`) |

### BPF filter primitives
- Addresses: `host 10.0.0.5`, `src host ...`, `dst host ...`, `net 10.20.0.0/16`.
- Ports and protocols: `port 443`, `tcp port 5432`, `udp port 53`, `portrange 8000-8100`, `icmp`, `arp`, `ip6`.
- Combine with `and`, `or`, `not` and parentheses; quote the whole filter so the shell leaves it alone.
- TCP flags: `'tcp[tcpflags] & tcp-syn != 0'` (any SYN), `'tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn'` (only new connection attempts), `'tcp[tcpflags] & tcp-rst != 0'` (resets).
- ICMP types: `'icmp[icmptype] == icmp-echo'`, `'icmp[icmptype] == icmp-unreach'` (look for "frag needed" when debugging [[mss-mtu-pmtud]]).
- The filter is compiled to BPF and runs in the kernel, so unmatched packets never reach user space.

### Reading TCP output
Each line: time, `IP src.port > dst.port`, `Flags [..]`, sequence/ack numbers, window, options, payload length.
- Flags: `[S]` SYN, `[S.]` SYN-ACK, `[.]` ACK, `[P.]` data with PSH, `[F.]` FIN, `[R]` / `[R.]` reset.
- The handshake is `[S]`, `[S.]`, `[.]` ([[tcp-handshake-and-connection-states]]); the SYN's options show the MSS, window scale and SACK the client offers ([[tcp-segment-structure]], [[tcp-flow-control]]).
- After the handshake tcpdump shows relative sequence numbers (`seq 1:518`, `ack 1`); `-S` prints absolute ones.
- Patterns: SYNs repeating at 1 s, 2 s, 4 s with no answer = filtered port; `[S]` answered by `[R.]` = nothing listening; `win 0` from the receiver = it is not reading ([[tcp-flow-control]]); the same `seq` sent twice = retransmission ([[tcp-congestion-control]]).

### Reading UDP, ICMP and ARP output
- DNS query: `10.20.6.10.48211 > 10.20.0.2.53: 31837+ A? api.example.com. (33)`: id 31837, `+` = recursion desired, record type, name, payload length. The reply carries the same id, `1/0/0` (answers/authority/additional) and the records ([[dns-resolution]], [[udp-in-depth]]).
- `ICMP echo request` / `echo reply` with `id` and `seq`; `ICMP time exceeded in-transit` is what traceroute collects ([[icmp-ping-traceroute]]).
- `ARP, Request who-has 10.20.6.1 tell 10.20.6.10` and `Reply 10.20.6.1 is-at 52:54:00:..`; unanswered requests mean the neighbour is down or on another L2 segment ([[arp]], [[host-to-host-communication]]).

### Wireshark
- Capture with `-w`, copy the file, open locally: follow TCP streams, decode HTTP/2, Postgres and Kafka, filter with `tcp.analysis.retransmission` or `dns.flags.rcode != 0`.
- Live remote capture without a file: `ssh host 'sudo tcpdump -i eth0 -U -w - port 8080' | wireshark -k -i -`.
- TLS payloads stay encrypted unless you supply a key log file (`SSLKEYLOGFILE`) ([[tls-handshake-and-certificates]]).

### Containers
- From the host, `-i docker0` or `-i br-<id>` shows traffic between containers on that bridge, and the host-side `veth` shows one container's traffic ([[docker-networking]]).
- Inside the container's namespace with the host's tcpdump: `nsenter -t <pid> -n tcpdump -i eth0 ...`; the application image needs no tools.
- Or a sidecar sharing the namespace: `docker run --rm --net container:api nicolaka/netshoot tcpdump ...`; in Kubernetes, `kubectl debug` ([[network-debugging-toolkit]]).

## Example
Why does the API time out on Postgres? Capture inside the API container, then read the handshake and a DNS lookup.
```bash
PID=$(docker inspect -f '{{.State.Pid}}' esim-api)
sudo nsenter -t "$PID" -n tcpdump -i eth0 -nn -c 20 'tcp port 5432 or udp port 53'

# the lookup of "postgres" went to the embedded DNS on lo, so eth0 shows only the TCP part
# 09:14:02.103 IP 172.18.0.5.51514 > 172.18.0.3.5432: Flags [S], seq 3913254091, win 64240, options [mss 1460,sackOK,TS val 1022 ecr 0,nop,wscale 7], length 0
# 09:14:02.103 IP 172.18.0.3.5432 > 172.18.0.5.51514: Flags [S.], seq 1783392040, ack 3913254092, win 65160, options [mss 1460,sackOK,TS val 877 ecr 1022,nop,wscale 7], length 0
# 09:14:02.103 IP 172.18.0.5.51514 > 172.18.0.3.5432: Flags [.], ack 1, win 502, length 0
# 09:14:02.104 IP 172.18.0.5.51514 > 172.18.0.3.5432: Flags [P.], seq 1:9, ack 1, win 502, length 8      # SSLRequest
# 09:14:02.104 IP 172.18.0.3.5432 > 172.18.0.5.51514: Flags [P.], seq 1:2, ack 9, win 509, length 1      # 'S': TLS accepted

# DNS on the host side: query and answer share id 31837
sudo tcpdump -i any -nn -c 2 'udp port 53'
# IP 10.20.6.10.48211 > 10.20.0.2.53: 31837+ A? billing.internal. (34)
# IP 10.20.0.2.53 > 10.20.6.10.48211: 31837 1/0/0 A 10.20.7.15 (50)

# Only connection attempts and resets, saved for Wireshark, rotating 5 x 100 MB
sudo tcpdump -i eth0 -nn -C 100 -W 5 -w /tmp/pg.pcap \
  'tcp port 5432 and (tcp[tcpflags] & (tcp-syn|tcp-rst) != 0)'
tcpdump -nn -r /tmp/pg.pcap0 'tcp[tcpflags] & tcp-rst != 0' | head
```

## Pitfalls
- Forgetting `-nn`: tcpdump does reverse DNS for every address, slows down, drops packets, and its own DNS queries show up in the capture.
- Capturing on the wrong interface: traffic to `localhost` is on `lo`, the container's embedded DNS on its own `lo`, bridge traffic on `docker0`/`br-*`; `-i any` on the host does not see inside other network namespaces' loopbacks.
- Checksum errors with `-vv` on outgoing packets are usually checksum offload: the NIC fills them in after capture.
- "packets dropped by kernel" at the end means the filter is too broad or the output too slow; tighten the filter and write with `-w`.
- Captures contain credentials, tokens and personal data in clear-text protocols; keep them short, filtered, and delete them after use.
- Related: [[network-debugging-toolkit]] for the layer-by-layer playbook around this tool, [[linux-networking]] for interfaces and namespaces, [[osi-and-tcp-ip-models]] for the headers you are reading.
