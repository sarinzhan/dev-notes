---
title: DNS resolution
tags: [networking, dns, kubernetes, java, caching]
learned: 2026-10-27
---
## TL;DR
DNS turns a name into addresses through a chain of caches: the application's own cache (the JVM has one), the OS stub resolver (`/etc/resolv.conf`, `systemd-resolved`), a recursive resolver that walks root → TLD → authoritative servers, and the authoritative server that owns the zone. Every answer carries a TTL that bounds how stale it may be. In Kubernetes, CoreDNS answers `service.namespace.svc.cluster.local` with the ClusterIP (or pod IPs for headless services), and the pod's `search` list plus `ndots:5` makes short names work at the cost of extra queries. Most DNS incidents in a Java service are caching: the JVM or a connection pool still holds an address that stopped being valid.

## Key points
### Resolution path
1. Application asks `getaddrinfo()` / `InetAddress.getByName()`; the JVM checks its own cache first.
2. Stub resolver on the host: `/etc/hosts` and `/etc/resolv.conf` (`nameserver`, `search`, `options ndots:N`), ordered by `/etc/nsswitch.conf`. On modern Linux `127.0.0.53` is `systemd-resolved`; in Docker it is `127.0.0.11`, the embedded DNS ([[linux-networking]]).
3. **Recursive resolver** (ISP, `1.1.1.1`, CoreDNS, corporate DNS): answers from cache or walks the hierarchy: root servers → `.kg` TLD servers → the zone's **authoritative** servers (`NS` records), caching each step.
4. Authoritative answer with TTL; every cache along the path keeps it up to that TTL.
- Transport: UDP 53 with 512-byte classic limit (EDNS0 raises it); falls back to TCP for large answers (big `TXT`, DNSSEC) and zone transfers. DoT/DoH encrypt the stub→resolver hop ([[tcp-vs-udp]]).

### Records you actually use
| Record | Maps | Backend relevance |
|---|---|---|
| `A` / `AAAA` | name → IPv4 / IPv6 | the common case; multiple `A` records = DNS round robin |
| `CNAME` | name → another name | `api.beeline.kg` → `lb-123.cloud.example`; cannot coexist with other records at the apex |
| `NS` | zone → authoritative servers | delegation |
| `SRV` | service → host + port + priority/weight | Consul, Kubernetes headless services expose ports this way |
| `TXT` | arbitrary text | ACME challenges, SPF/DKIM, domain verification |
| `MX` | mail servers | only if you send mail |
| `PTR` | IP → name (reverse) | slow `ssh`/Postgres logins when reverse DNS is broken |
| `HTTPS`/`SVCB` | service parameters incl. ALPN `h3` | HTTP/3 discovery ([[http-1-1-vs-2-vs-3]]) |

### TTL and caching layers
- Authoritative TTL is a maximum; each cache counts it down. Lowering the TTL before a migration must happen one old-TTL in advance, because caches still hold the old value with the old TTL.
- Negative caching (`NXDOMAIN`) is cached too, governed by the SOA `minimum`; a name queried before it was created stays "not found" for minutes.
- **JVM cache**: successful lookups 30 s by default (`networkaddress.cache.ttl`), failures 10 s (`networkaddress.cache.negative.ttl`); with a `SecurityManager` it used to be forever, which is where the "Java caches DNS forever" folklore comes from. Set `-Dnetworkaddress.cache.ttl=10` or in `java.security` for services behind changing IPs.
- Client libraries cache above the JVM: a connection pool holds sockets to the old IP until `maxLifetime` ([[sockets-and-connection-pooling]]); Netty's `DnsNameResolver` (used by Reactor Netty `WebClient`) has its own cache honouring TTL; the Kafka client re-resolves bootstrap servers per reconnect.

### DNS in Kubernetes
- CoreDNS serves the cluster zone. Names: `<svc>.<ns>.svc.cluster.local` → ClusterIP; a **headless** service (`clusterIP: None`) returns the pod IPs directly, which is what client-side load balancing (gRPC `round_robin`, Kafka, Cassandra) needs ([[grpc-and-protobuf]]). `<pod-name>.<svc>` names exist for StatefulSets.
- Pod `/etc/resolv.conf`: `nameserver 10.96.0.10`, `search esim.svc.cluster.local svc.cluster.local cluster.local`, `options ndots:5`. A name with fewer than 5 dots (`api.beeline.kg` has 2) is first tried with every search suffix: 3–4 NXDOMAIN round trips before the real query. Use a trailing dot (`api.beeline.kg.`) or set `dnsConfig.options ndots:2` for services that call many external hosts.
- Service IP is stable while pods change behind it; DNS-based failover inside the cluster is a kube-proxy/Endpoints problem, not a TTL one ([[kubernetes-basics]], [[ip-addressing-and-nat]]).
- CoreDNS overload (conntrack, UDP drops) looks like random 5-second stalls: the resolver timeout. `NodeLocal DNSCache` fixes it with a per-node cache.

### DNS as a load-balancing and failover tool
- Round robin `A` records give coarse distribution with no health checks; clients cache and connection pools pin, so skew is normal. Geo/latency-based DNS (Route 53, Cloudflare) routes to the nearest region ([[multi-region-basics]]); failover DNS swaps records on health-check failure, bounded by TTL. For anything finer, use a real load balancer ([[load-balancers-l4-vs-l7]]).

## Example
Tracing a lookup the way a resolver does, then checking what the JVM will cache.
```bash
# full answer section, with TTLs and the CNAME chain
$ dig api.beeline.kg
;; ANSWER SECTION:
api.beeline.kg.        300   IN  CNAME  lb-7f3.eu-central.cloud.example.
lb-7f3.eu-central.cloud.example.  60  IN  A  52.18.7.90
lb-7f3.eu-central.cloud.example.  60  IN  A  52.18.7.91
;; Query time: 23 msec
;; SERVER: 127.0.0.53#53(127.0.0.53)      # answered by the local stub -> go to the recursive to see real TTLs

# authoritative answer, bypassing all caches
$ dig +short NS beeline.kg.
ns1.beeline.kg.
$ dig @ns1.beeline.kg. api.beeline.kg. +norecurse

# walk the delegation yourself (what a recursive resolver does on a cold cache)
$ dig +trace api.beeline.kg. | grep -E '^(kg\.|beeline\.kg\.|api)'

# inside a pod: why one external lookup costs five queries
$ kubectl exec -it esim-api-0 -- cat /etc/resolv.conf
search esim.svc.cluster.local svc.cluster.local cluster.local
nameserver 10.96.0.10
options ndots:5
$ kubectl exec -it esim-api-0 -- dig +short smdp.vendor.example.     # trailing dot: one query
```
Pinning the JVM's cache so pods see a database failover within seconds:
```yaml
# JAVA_TOOL_OPTIONS in the Deployment, or jvm args in the Dockerfile
JAVA_TOOL_OPTIONS: "-Dnetworkaddress.cache.ttl=10 -Dnetworkaddress.cache.negative.ttl=5"
```

## Pitfalls
- Lowering a TTL on the day of the migration: old caches keep the old value for the old TTL. Lower it a day before, raise it after.
- A `CNAME` at the zone apex (`beeline.kg`) is invalid; providers offer `ALIAS`/`ANAME` flattening.
- Services that resolve once at startup (`new InetSocketAddress(host, port)` kept in a field) never see a failover; resolve per connection.
- Kubernetes `ndots:5` plus a flaky CoreDNS turns every external call into multiple timeouts; always test name resolution from inside a pod, not from the node.
- Split-horizon DNS (different answers inside and outside the office) explains "works on VPN, fails from CI"; `dig @8.8.8.8` versus `dig @corp-dns` shows it.
- Related: [[linux-networking]] for resolver configuration on the host, [[network-debugging-toolkit]] for the `dig`/`drill`/`nslookup` recipes.
