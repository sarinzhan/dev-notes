---
title: TCP congestion control, slow start and congestion avoidance
tags: [networking, tcp, transport, congestion-control]
learned: 2026-10-29
---
## TL;DR
Flow control protects the receiver; congestion control protects the **network**. The sender keeps a second window, the **congestion window** (`cwnd`), that nobody advertises: the sender infers it from ACKs and loss. Bytes in flight are limited by `min(cwnd, rwnd)`. A new connection starts with `cwnd` = 10 segments and doubles it every RTT (**slow start**) until it hits `ssthresh` or loses a packet; then it grows by about one segment per RTT (**congestion avoidance**) and cuts the window on loss. Linux uses CUBIC by default, BBR is an option. Consequence for backends: fresh or idle connections are slow, so reuse matters.

## Key points
### Two windows
| | Flow control | Congestion control |
|---|---|---|
| Protects | the receiver's buffer | routers and links in between |
| Window | `rwnd`, advertised in every ACK ([[tcp-flow-control]]) | `cwnd`, kept privately by the sender |
| Shrinks when | the application reads slowly | loss, ECN marks, rising delay (BBR) |
- Effective window = `min(cwnd, rwnd)`. Throughput ≈ `window / RTT`.
- Both are counted in bytes, usually reasoned about in MSS-sized segments ([[mss-mtu-pmtud]]).

### Slow start
- Initial window (`initcwnd`) is **10 segments** (~14.6 KB) since RFC 6928 and Linux 2.6.39; before that it was 2–4.
- Every ACK grows `cwnd` by one segment, so the window **doubles each RTT**: 10, 20, 40, 80…
- Slow start ends when `cwnd` reaches the **slow start threshold** (`ssthresh`, initially very large) or on the first loss.
- Cost: on an 80 ms RTT, reaching a 1 MB window from 14.6 KB takes about 6 RTTs ≈ 0.5 s. A 300 KB response on a cold connection needs ~5 round trips on any link; hence warm pools and HTTP/2 multiplexing ([[connection-establishment-cost]]).

### Congestion avoidance (AIMD)
- Above `ssthresh`, `cwnd` grows by about **one MSS per RTT**: additive increase.
- On loss, `cwnd` is cut: **multiplicative decrease** (Reno halves it, CUBIC multiplies by 0.7). AIMD makes competing flows converge to a fair share.

### Detecting loss and recovering
- **Fast retransmit**: three duplicate ACKs for the same sequence number mean one segment is missing but later ones arrived. The sender retransmits it immediately without waiting for the timer.
- **Fast recovery**: after fast retransmit, `ssthresh = cwnd / 2` (Reno) and `cwnd` continues from `ssthresh`, not from 1. SACK tells the sender exactly which ranges are missing.
- **Retransmission timeout (RTO)**: no ACKs at all (minimum 200 ms on Linux, doubling on each retry). This is treated as severe: `ssthresh = cwnd / 2`, `cwnd` = 1 segment, slow start again.
- **ECN**: instead of dropping, a router marks packets "congestion experienced"; the receiver echoes the mark and the sender reduces `cwnd` with no loss and no retransmission. Needs support on both ends and on the path (`net.ipv4.tcp_ecn`).

### Algorithms
| Algorithm | Signal | Notes |
|---|---|---|
| Reno / NewReno | loss | classic AIMD, halves on loss; poor on high-BDP links |
| CUBIC | loss | Linux default since 2.6.19; window is a cubic function of time since last loss: climbs back fast, plateaus near the previous max, then probes |
| BBR (v1, v3) | measured bandwidth and min RTT | models the bottleneck instead of waiting for loss; much better on lossy or long paths; can be unfair to CUBIC flows |
- Congestion control is a **sender-only** decision: the server picks the algorithm for its uploads, the client for its own. `sysctl net.ipv4.tcp_congestion_control`; BBR works best with the `fq` qdisc.

### Slow start after idle
- If a connection stays idle longer than one RTO, Linux resets `cwnd` to the initial window (`net.ipv4.tcp_slow_start_after_idle = 1` by default), since its estimate may be stale.
- A pooled connection used every few seconds therefore pays slow start again on each burst. For latency-sensitive internal traffic on stable networks setting it to `0` keeps the window. Keep-alive and pooling ([[sockets-and-connection-pooling]], [[hikaricp-pool-sizing]]) still save the handshake either way.
- Linux caches `ssthresh` per destination, so a new connection to a recently lossy host may exit slow start early.

```anim
tcp-cwnd-growth
```

## Example
Inspecting and tuning congestion control on a Linux host.
```bash
# which algorithm is used and which are available
sysctl net.ipv4.tcp_congestion_control net.ipv4.tcp_available_congestion_control
# per-connection state: cwnd, ssthresh, rtt, retransmits
ss -ti dst :5432
#   cubic wscale:7,7 rto:204 rtt:1.2/0.4 mss:1448 cwnd:10 ssthresh:7 retrans:0/3 ...

# keep cwnd on idle pooled connections (latency-sensitive internal traffic)
sysctl -w net.ipv4.tcp_slow_start_after_idle=0

# switch to BBR with the fq qdisc (kernel 4.9+)
sysctl -w net.core.default_qdisc=fq
sysctl -w net.ipv4.tcp_congestion_control=bbr

# larger initial window for one route (default is 10)
ip route change default via 10.0.0.1 dev eth0 initcwnd 20 initrwnd 20
```
In a container these sysctls are mostly per network namespace and can be set in the pod or Compose spec ([[docker-networking]]).

## Pitfalls
- Slow short responses on cold connections are slow start, not a slow network: count round trips, not bytes.
- Retransmits look like random latency spikes of 200 ms and more (RTO). Watch `retrans` in `ss -ti` and `TcpRetransSegs` in `nstat` ([[network-debugging-toolkit]]).
- BBR on a shared link can starve CUBIC neighbours; test before a fleet-wide rollout.
- Packet loss from an overloaded UDP flow or a tiny router buffer cuts every TCP flow's window, not just one ([[udp-in-depth]]).
- Related: [[head-of-line-blocking]] for what loss does to multiplexed streams, [[tcp-vs-udp]] for the bigger picture.
