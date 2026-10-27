---
title: Load balancing — algorithms, health checks, stickiness and global balancing
tags: [system-design, load-balancing, availability, networking]
learned: 2026-10-27
---
## TL;DR
A load balancer spreads requests over N replicas so that the system scales horizontally and survives the loss of a replica. The design questions are: at which layer it operates (L4 forwards TCP connections, L7 understands HTTP and can route by path, header or cookie), which algorithm picks the replica (round-robin, least connections, weighted, hashing), how it discovers that a replica is unhealthy, and how traffic is balanced between regions (DNS and anycast). The mechanics of L4 vs L7 and the NGINX/Envoy specifics are in [[load-balancers-l4-vs-l7]]; this note is about choosing.

## Key points

### Where balancing happens
| Level | Mechanism | Granularity | Typical use |
|---|---|---|---|
| DNS | several A/AAAA records, weighted or geo answers, low TTL | per client resolver | global entry point, region selection ([[dns-resolution]]) |
| Anycast | same IP announced from many PoPs, BGP routes to the nearest | per network path | CDN edges, DDoS absorption ([[cdn]]) |
| L4 (transport) | NAT/forward TCP/UDP connections by 5-tuple hash | per connection | cloud NLB, high throughput, TLS passthrough, non-HTTP (gRPC, DB proxies) |
| L7 (application) | terminates HTTP/TLS, routes per request | per request | path/host routing, canaries, retries, auth, rate limits ([[reverse-proxy-nginx-basics]]) |
| Client-side | the client holds the replica list and picks | per request | service mesh sidecars, gRPC clients, Kafka clients ([[api-gateway-and-service-discovery]]) |

A real deployment stacks them: DNS/anycast → L4 at the edge → L7 ingress → (optionally) client-side inside the mesh. Kubernetes `Service` is L4 (iptables/IPVS), `Ingress`/Gateway API is L7 ([[kubernetes-basics]]).

### Algorithms
| Algorithm | Picks | Good for | Weak spot |
|---|---|---|---|
| Round-robin | next in list | identical replicas, short uniform requests | ignores slow replicas |
| Weighted round-robin | proportional to weight | mixed instance sizes, canary 5 % | static weights |
| Least connections | fewest in-flight | long or variable requests (uploads, streams) | needs shared state in the LB; sees connections, not CPU |
| Least response time / EWMA | lowest recent latency | heterogeneous backends | can herd to one fast replica |
| Random / power of two choices | pick 2 at random, take the less loaded | distributed client-side balancing, no central state | still needs a load signal |
| IP hash / cookie hash | same client → same replica | stickiness for in-memory state | hot clients (NAT, one big tenant) overload one replica |
| Consistent hashing / ring | key → replica with minimal reshuffle | caches, WebSocket servers, per-key affinity ([[consistent-hashing]]) | uneven load without virtual nodes |

Interview rule: default to least connections at L7, round-robin at L4, consistent hashing only when the replica holds per-key state (an in-process cache or an open WebSocket).

### Health checks and removal
- **Active**: the LB probes `/actuator/health/readiness` every few seconds; `N` failures remove the replica, `M` successes return it. Readiness must check what the pod needs (DB pool, Kafka), liveness only whether the process is alive, otherwise a DB outage restarts every pod ([[leases-heartbeats-and-health]]).
- **Passive (outlier detection)**: count 5xx and timeouts per replica, eject it for a cooling period. Catches "healthy but broken" replicas that still answer the probe.
- **Connection draining**: on scale-down or deploy, stop sending new requests, wait for in-flight ones (Kubernetes `preStop` sleep + `terminationGracePeriodSeconds`), then kill. Without it every deploy produces a burst of 502s ([[signals-and-process-lifecycle]]).
- **Slow start**: ramp a new replica's weight over 30–60 s so a cold JVM (JIT, empty pools) does not get a full share immediately.

### Stickiness and statelessness
- Stickiness (cookie or hash) is a workaround for state on the replica. The system-design answer is to remove the state: sessions in Redis or a signed token ([[horizontal-vs-vertical-scaling]], [[cookies-sessions-and-cors]]), per-key caches made shared ([[caching]]).
- Legitimate stickiness: WebSocket connections (the socket *is* state, [[design-chat-service]]), request coalescing in an in-process cache, gRPC streams. Pair it with consistent hashing so a replica change moves only 1/N of clients.

### Global load balancing
- Geo-DNS or latency-based DNS sends Bishkek users to the Frankfurt region and Jakarta users to Singapore; keep TTLs short (30–60 s) so failover is fast, but remember clients and resolvers cache beyond TTL ([[multi-region-basics]]).
- Anycast + edge L7 (a CDN or a global load balancer) gives one IP worldwide and failover without DNS changes, and absorbs volumetric DDoS at the edge.
- Health-aware DNS removes a dead region; the data layer must be ready for the traffic to land elsewhere ([[leader-follower-replication]]).

### Capacity and the LB itself
- An L7 proxy on 2 vCPU handles ~10–30 K rps with TLS termination; L4 scales to millions of connections because it does not parse. Terminate TLS once at L7, use mTLS inside the mesh ([[mtls-and-service-to-service-auth]]).
- The LB must itself be redundant: cloud LBs are managed pairs; self-hosted NGINX/HAProxy use keepalived + VRRP for a floating IP.
- Retries at the LB only for idempotent methods or requests with an [[idempotency|idempotency key]], with a budget (e.g. 10 % extra load) to avoid retry storms.

## Example
A Kubernetes ingress fragment (NGINX ingress controller) for the notification API: least-connections, passive outlier detection, retries only for idempotent requests, and sticky sessions only on the WebSocket path.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: notification-api
  annotations:
    nginx.ingress.kubernetes.io/load-balance: "ewma"              # latency-aware instead of plain round-robin
    nginx.ingress.kubernetes.io/proxy-next-upstream: "error timeout http_502 http_503"
    nginx.ingress.kubernetes.io/proxy-next-upstream-tries: "2"    # one retry, next replica
    nginx.ingress.kubernetes.io/proxy-next-upstream-timeout: "3"
    nginx.ingress.kubernetes.io/proxy-connect-timeout: "2"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "10"
    nginx.ingress.kubernetes.io/limit-rps: "200"                   # coarse per-client-IP limit at the edge
spec:
  rules:
    - host: api.example.kg
      http:
        paths:
          - path: /v1/notifications
            pathType: Prefix
            backend: { service: { name: notification-api, port: { number: 8080 } } }
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: notification-ws
  annotations:
    nginx.ingress.kubernetes.io/affinity: "cookie"                 # WebSocket servers hold connection state
    nginx.ingress.kubernetes.io/session-cookie-name: "ws-route"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
spec:
  rules:
    - host: api.example.kg
      http:
        paths:
          - path: /ws
            pathType: Prefix
            backend: { service: { name: notification-ws, port: { number: 8081 } } }
```

NGINX retries `POST` only when the connection failed before the request was sent, which is the safe subset; the application still needs `Idempotency-Key` for client-side retries ([[api-design-for-system-design]]).

## Pitfalls
- Readiness that checks nothing: the LB keeps sending traffic to a pod whose DB pool is exhausted; or readiness that checks everything, so one Redis blip removes every pod at once and the LB has no upstream.
- Retrying non-idempotent `POST`s at the proxy after a timeout: the request may have executed, and the user now has two orders.
- Sticky sessions hiding a scaling problem: one NAT-ed corporate client or one big tenant lands on a single replica and saturates it.
- Long-lived HTTP/2 or gRPC connections defeat L4 balancing: one connection carries thousands of requests to one replica. Balance at L7 or use client-side balancing with periodic reconnects.
- Short DNS TTLs do not give instant failover; browsers, JVMs (`networkaddress.cache.ttl`) and resolvers cache longer than you set.
- Related: [[load-balancers-l4-vs-l7]] for the proxy mechanics; [[circuit-breaker]] and [[failure-modes-and-timeouts]] for what the client does when the LB has no healthy upstream.
