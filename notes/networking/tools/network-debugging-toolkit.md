---
title: Network debugging toolkit
tags: [networking, troubleshooting, curl, tcpdump, dns]
learned: 2026-10-27
---
## TL;DR
When a call between services fails, debug bottom-up, one layer at a time: **name** (does DNS resolve to the right IP?), **route and port** (can a TCP connection be opened?), **TLS** (does the handshake succeed with the expected certificate?), **HTTP** (what status, headers and timings come back?), and only then the **application**. Each layer has one or two tools that answer its question in seconds. Guessing at the application layer while the real problem is DNS or a proxy certificate is the most common way to waste an afternoon.

## Key points
### Layer-by-layer playbook
| Question | Tool | What a failure looks like |
|---|---|---|
| Does the name resolve, and to what? | `dig +short api.example.com`, `dig @8.8.8.8 ...`, `getent hosts` | `NXDOMAIN`, an old IP after a migration, a different answer from the system resolver than from `dig` ([[dns-resolution]]) |
| Which interface and gateway would be used? | `ip route get 10.20.0.5` | Traffic to a VPN or Docker subnet leaves through the wrong interface ([[linux-networking]]) |
| Is the port reachable? | `nc -vz -w 3 host 5432` | `Connection refused` = host reachable, nothing listening; timeout = firewall or security group dropping packets |
| Where on the path does it stop? | `mtr -rwc 50 host`, `traceroute -T -p 443 host` | Loss that starts at one hop and continues to the end; loss on a single middle hop only is usually ICMP rate limiting, not a real problem |
| Does TLS work and which certificate is served? | `openssl s_client -connect host:443 -servername host` | Wrong issuer (a corporate proxy re-signing traffic), expired chain, missing intermediate, SNI mismatch ([[tls-handshake-and-certificates]]) |
| What does the server actually return, and where is the time spent? | `curl -v`, `curl -w` with timing variables | 502/504 from a proxy rather than the app, `time_connect` small but `time_starttransfer` large (slow backend) |
| What is on the wire? | `tcpdump -i any -nn 'host X and port 443' -w cap.pcap` + Wireshark | SYN without SYN-ACK, RST right after connect, retransmissions ([[tcpdump]]) |
| What connections does this host hold? | `ss -tanp`, `ss -s`, `ss -ltnp` | Thousands of `TIME_WAIT` (no keep-alive or pooling), many `CLOSE_WAIT` (app never closes sockets), nothing in `LISTEN` on the expected port ([[tcp-handshake-and-connection-states]]) |

### Reading `curl` timings
`curl -w` exposes cumulative timestamps: `time_namelookup` (DNS), `time_connect` (TCP established), `time_appconnect` (TLS done), `time_starttransfer` (first byte), `time_total`. Subtract neighbours to get each phase. A slow DNS phase points at the resolver, a slow connect at the network or a full accept queue, a slow TLS phase at an OCSP or proxy problem, and a slow time to first byte at the server itself.

`--resolve host:443:10.0.0.7` sends the request to a chosen IP while keeping the correct SNI and `Host` header. Use it to test one backend behind a load balancer ([[load-balancers-l4-vs-l7]]) or a new server before switching DNS.

### Refused vs timeout vs reset
- **Connection refused**: a RST came back immediately. The host is up, but nothing listens on that port, or the service binds only to `127.0.0.1` inside a container.
- **Timeout**: no answer at all. A firewall, security group or network policy drops the packets, or the route is wrong.
- **Connection reset mid-stream**: a proxy or load balancer closed an idle connection that the client pool still considered alive. The usual fix is an idle timeout in the pool shorter than the proxy's ([[sockets-and-connection-pooling]], [[hikaricp-pool-sizing]]).

### Inside containers and Kubernetes
Application images rarely ship `dig` or `tcpdump`. Attach a debug container to the pod's network namespace with `kubectl debug -it <pod> --image=nicolaka/netshoot --target=<container>` and run the same tools from inside the pod's view of the network ([[kubernetes-basics]]). Service names resolve only inside the cluster, and a `NetworkPolicy` shows up as a timeout, never as "refused".

### JVM-specific checks
- `-Djavax.net.debug=ssl:handshake` prints the TLS negotiation, including which certificates the JVM trusts.
- `PKIX path building failed` means the JVM truststore lacks the issuing CA. With a TLS-inspecting corporate proxy such as FortiGate, import the proxy's CA into the truststore (`keytool -importcert -cacerts ...`) or set `-Djavax.net.ssl.trustStore`. Never disable verification.
- Docker images based on minimal JREs have their own `cacerts`, separate from the host's trust store.

## Example
```bash
#!/usr/bin/env bash
# Usage: ./netcheck.sh billing.internal 443
# Walks DNS -> TCP -> TLS -> HTTP and stops at the first failing layer.
set -u
HOST=$1; PORT=${2:-443}

echo "== DNS"
IP=$(dig +short "$HOST" | tail -n1)
if [ -z "$IP" ]; then echo "no A record for $HOST"; exit 1; fi
echo "$HOST -> $IP via $(ip route get "$IP" | head -n1)"

echo "== TCP"
nc -vz -w 3 "$IP" "$PORT" || { echo "port closed or filtered"; exit 2; }

echo "== TLS"
echo | openssl s_client -connect "$IP:$PORT" -servername "$HOST" 2>/dev/null \
  | openssl x509 -noout -subject -issuer -enddate || { echo "TLS handshake failed"; exit 3; }

echo "== HTTP timings"
curl -sS -o /dev/null --resolve "$HOST:$PORT:$IP" \
  -w 'status=%{http_code} dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total}\n' \
  "https://$HOST:$PORT/actuator/health"
```

## Pitfalls
- `ping` proves little: ICMP is often blocked while TCP works, and the reverse. Test the actual port with `nc` or `curl`.
- `curl -k` hides certificate problems instead of diagnosing them, and the same bug then appears in the JVM, which verifies certificates.
- `dig` queries DNS directly and ignores `/etc/hosts` and the JVM's own DNS cache. Use `getent hosts` to see what the system resolver returns, and remember the JVM caches lookups (`networkaddress.cache.ttl`).
- Capture on the right interface: inside Docker or Kubernetes, `-i any` on the host can miss traffic that stays inside a namespace. Capture from the debug container instead.
- Packet captures can contain tokens and personal data. Keep them short, filtered, and out of tickets and chats.
- Related: [[linux-networking]], [[osi-and-tcp-ip-models]], [[reverse-proxy-nginx-basics]], [[distributed-tracing]] for following one request across services once the network itself is fine.
