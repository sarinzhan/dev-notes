---
title: TCP segment structure
tags: [networking, tcp, transport, packets]
learned: 2026-10-29
---
## TL;DR
A TCP segment is a 20–60 byte header plus data, carried inside an IP packet. The header holds ports, a 32-bit **sequence number** (which byte of the stream this segment starts at), a 32-bit **acknowledgement number** (the next byte the sender expects), flags such as SYN/ACK/FIN/RST, a receive **window**, a checksum, and options negotiated in the SYN (MSS, window scale, SACK, timestamps). Every TCP feature, from ordering to flow control, is arithmetic on these fields.

## Key points
### Header fields
| Field | Bits | Meaning |
|---|---|---|
| Source / destination port | 16 + 16 | with the IPs from [[ip-packet-structure]], form the 4-tuple that identifies the connection |
| Sequence number | 32 | number of the first data byte in this segment; in a SYN it is the random initial sequence number (ISN) |
| Acknowledgement number | 32 | next byte expected from the peer, i.e. "I have everything before this"; valid when ACK is set |
| Data offset | 4 | header length in 32-bit words: 5 (20 bytes) to 15 (60 bytes) |
| Reserved | 4 | must be zero |
| Flags | 8 | CWR, ECE, URG, ACK, PSH, RST, SYN, FIN |
| Window | 16 | how many bytes the sender of this segment can still receive ([[tcp-flow-control]]) |
| Checksum | 16 | over a pseudo-header (IPs, protocol, length), header and data; mandatory |
| Urgent pointer | 16 | end of urgent data when the URG flag is set; historical, unused |
| Options | 0–320 | MSS, window scale, SACK, timestamps, padded with NOP |

### Flags
- **SYN**: synchronise sequence numbers, only in the first two handshake segments ([[tcp-handshake-and-connection-states]]).
- **ACK**: the acknowledgement number is valid. Set on every segment after the first SYN.
- **FIN**: "I will send no more data". The other direction stays open until it sends its own FIN.
- **RST**: abort the connection right now. Sent for a port with no listener ("connection refused") or data on a closed socket.
- **PSH**: deliver to the application without waiting to fill the buffer. Linux sets it on the last segment of each `write()`; shows as `P` in tcpdump.
- **ECE / CWR**: Explicit Congestion Notification. Routers mark packets instead of dropping them, the receiver echoes ECE, the sender reduces its window and answers CWR ([[tcp-congestion-control]]).

### Options
- **MSS** (SYN only): largest payload this side accepts, 1460 on Ethernet (1500 − 20 IP − 20 TCP) ([[mss-mtu-pmtud]]).
- **Window scale** (SYN only): shift count 0–14 applied to the 16-bit window, so windows can reach ~1 GiB instead of 64 KiB. `win 502` with `wscale 7` means 502 × 128 = 64,256 bytes.
- **SACK permitted / SACK blocks**: the receiver reports which ranges it has beyond a gap, so the sender retransmits only the hole.
- **Timestamps**: 10 bytes, used for RTT measurement and PAWS (protection against wrapped sequence numbers). At 10 Gbit/s the 32-bit sequence space wraps in about 3.4 seconds.
- A Linux SYN has a 40-byte header (20 bytes of options); data segments with timestamps have 32 bytes.

### Sequence and acknowledgement arithmetic
Take client ISN 1000 and server ISN 5000.
| # | Direction | Flags | seq | ack | Data |
|---|---|---|---|---|---|
| 1 | C → S | SYN | 1000 | — | 0 |
| 2 | S → C | SYN, ACK | 5000 | 1001 | 0 |
| 3 | C → S | ACK | 1001 | 5001 | 0 |
| 4 | C → S | PSH, ACK | 1001 | 5001 | 100 bytes |
| 5 | S → C | PSH, ACK | 5001 | 1101 | 300 bytes |
| 6 | C → S | FIN, ACK | 1101 | 5301 | 0 |
| 7 | S → C | ACK | 5301 | 1102 | 0 |

- SYN and FIN each consume one sequence number even without data; that is why the ACK of an ISN is ISN + 1.
- The next seq is always `seq + data length`. The ACK number is cumulative: `ack 1101` confirms bytes 1001–1100.
- Segment 5 acknowledges the client's data and carries the response in one packet (piggybacking). Delayed ACK waits up to ~40 ms hoping for such a reply ([[nagle-and-delayed-ack]]).
- ISNs are random (RFC 6528), so an off-path attacker cannot guess them and inject data or RST into your connection.

```anim
tcp-segment-flow
```

## Example
The same exchange as tcpdump prints it against a Postgres server. After the SYN, tcpdump shows **relative** numbers starting from 1 (use `-S` for absolute ones).
```bash
$ sudo tcpdump -ni eth0 'tcp port 5432'
IP 10.0.0.5.51000 > 10.0.0.9.5432: Flags [S], seq 2830113744, win 64240, options [mss 1460,sackOK,TS val 4011 ecr 0,nop,wscale 7], length 0
IP 10.0.0.9.5432 > 10.0.0.5.51000: Flags [S.], seq 1729370016, ack 2830113745, win 65160, options [mss 1460,sackOK,TS val 9120 ecr 4011,nop,wscale 7], length 0
IP 10.0.0.5.51000 > 10.0.0.9.5432: Flags [.], ack 1, win 502, length 0
IP 10.0.0.5.51000 > 10.0.0.9.5432: Flags [P.], seq 1:101, ack 1, win 502, length 100
IP 10.0.0.9.5432 > 10.0.0.5.51000: Flags [P.], seq 1:301, ack 101, win 510, length 300
IP 10.0.0.5.51000 > 10.0.0.9.5432: Flags [F.], seq 101, ack 301, win 501, length 0
IP 10.0.0.9.5432 > 10.0.0.5.51000: Flags [.], ack 102, win 510, length 0
# [S.] = SYN+ACK, [.] = ACK only, [P.] = PSH+ACK, [F.] = FIN+ACK
```
More recipes: [[tcpdump]].

## Pitfalls
- `tcpdump` on the sending host often reports "bad cksum" on outgoing segments. The NIC computes checksums (checksum offload) after capture; it is not corruption.
- Comparing seq numbers across two captures with relative numbering: each capture starts at its own "1". Use `-S` when matching client and server traces.
- Middleboxes that strip window scaling or SACK silently cap throughput; check the SYN options on both ends.
- Expecting segments to match your `write()` calls. The kernel cuts the stream by MSS and merges small writes; TSO/GRO even show 64 KB "segments" in captures.
- Related: [[tcp-vs-udp]] for the overview, [[udp-in-depth]] for the 8-byte alternative, [[socket-management]] for the buffers these numbers index into.
