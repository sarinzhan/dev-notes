---
title: Load balancers, L4 vs L7
tags: [networking, load-balancing, l4, l7, health-checks]
learned: 2026-10-27
---
## TL;DR
An L4 load balancer forwards TCP/UDP flows by IP and port: fast, protocol-agnostic, one backend per connection, no idea what HTTP is. An L7 load balancer terminates the connection, parses HTTP, and routes each request by path, host or header, so it can also retry, rewrite, cache, terminate TLS and spread HTTP/2 streams across backends. Health checks and the balancing algorithm decide how traffic drains from a failing pod; sticky sessions are the thing you add when the application is not stateless and regret later. The system-design view (global balancing, where it sits in an architecture) is in [[load-balancing]]; this note is the networking mechanics.

## Key points
### L4 vs L7
| | L4 (transport) | L7 (application) |
|---|---|---|
| Sees | IP, port, TCP flags; TLS SNI at most | full HTTP request: method, path, headers, cookies, gRPC service |
| Unit of balancing | connection (or UDP flow) | request (or HTTP/2 stream) |
| TLS | passthrough (backend terminates) or NAT | terminates, optionally re-encrypts to backend |
| Client IP for backend | preserved with DSR/NAT tricks or PROXY protocol | lost; `X-Forwarded-For` / `Forwarded` added ([[reverse-proxy-nginx-basics]]) |
| Features | very high throughput, any protocol (Postgres, Kafka, SSH) | path routing, header-based canaries, retries, rate limits, auth, caching, WebSocket/gRPC awareness |
| Failure handling | connection-level: a dead backend = reset connections | request-level: retry idempotent requests on another backend |
| Examples | AWS NLB, Linux IPVS/kube-proxy, HAProxy `mode tcp`, MetalLB | nginx, HAProxy `mode http`, Envoy, AWS ALB, Kubernetes Ingress/Gateway API, API gateways ([[api-gateway-and-service-discovery]]) |

- Long-lived connections (HTTP/2, gRPC, WebSocket, DB) make L4 balancing lumpy: one client's connection sits on one backend forever. L7 balances per stream; for gRPC that is the difference between even load and one hot pod ([[grpc-and-protobuf]]).
- Databases and Kafka are balanced at L4 or not at all (clients do their own routing); HTTP APIs at L7.

### Algorithms
| Algorithm | Behaviour | Use when |
|---|---|---|
| Round robin | next backend each time, optional weights | backends uniform, requests cheap and similar |
| Least connections | pick the backend with fewest in-flight | mixed request durations (default for most L7 proxies) |
| Least response time / EWMA | fastest recent backend | heterogeneous nodes, latency-sensitive |
| Random (power of two choices) | pick two at random, take the less loaded | large fleets, avoids herd on one "least" backend |
| IP hash / consistent hash | same client or key → same backend | cache locality, sticky without cookies ([[consistent-hashing]]) |
| Maglev / ring hash | consistent hashing with minimal disruption on changes | L4 at scale, service meshes |

### Health checks and draining
- **Active**: the balancer calls `/actuator/health/readiness` every N seconds; unhealthy after K failures, healthy after M successes. Readiness, not liveness: a pod that is alive but warming up should not get traffic ([[kubernetes-basics]]).
- **Passive (outlier detection)**: eject a backend after consecutive 5xx or connection errors for a cooling period; catches failures faster than the active interval.
- **Draining**: on deploy, mark the backend not-ready, stop new connections, let in-flight requests finish (`server.shutdown=graceful`, `spring.lifecycle.timeout-per-shutdown-phase`), then stop. With HTTP/2, send `GOAWAY`. Without this step every rolling deploy produces a burst of 502s.
- Check the dependency chain carefully: a readiness check that fails when the DB is down takes the whole fleet out at once; fail readiness only for conditions local to the instance.

### Sticky sessions
- Cookie-based (`SERVERID`, ALB `AWSALB`) or source-IP hashing routes a user to the same backend. Needed for in-memory HTTP sessions, SSE/WebSocket state ([[websockets-sse-and-long-polling]]), local caches.
- Costs: uneven load, scaling and deploys drop sessions, and the balancer becomes a hidden part of your state. Prefer external session storage (Spring Session + Redis) or tokens ([[cookies-sessions-and-cors]], [[horizontal-vs-vertical-scaling]]).

### Client IP and PROXY protocol
- Behind L7 the backend sees the balancer's IP; the original is in `X-Forwarded-For` (leftmost = client, rightmost = last trusted proxy). Spring Boot: `server.forward-headers-strategy=framework` (or `native` for Tomcat's `RemoteIpValve`) so `request.getRemoteAddr()`, `isSecure()` and redirect URLs are correct.
- Behind L4 with NAT the client IP is lost too; **PROXY protocol** (HAProxy, NLB, nginx `proxy_protocol`) prepends a header with the original addresses to the TCP stream, which the backend must be configured to read.

### Timeouts in the chain
- Balancer idle timeout (ALB 60 s, NLB 350 s) must be ≥ the application's keep-alive timeout, otherwise the balancer reuses a connection the app just closed and the client gets a 502 ([[tcp-handshake-and-connection-states]]).
- Request timeout at the balancer should be slightly larger than the application's own, so the app's error is returned rather than the balancer's generic 504 ([[failure-modes-and-timeouts]]).

## Example
Health check, drain and forwarded headers on the Spring side, plus an HAProxy snippet showing the two modes side by side.
```yaml
# application.yml
server:
  shutdown: graceful                      # finish in-flight requests on SIGTERM
  forward-headers-strategy: framework     # trust X-Forwarded-* from the balancer
spring:
  lifecycle:
    timeout-per-shutdown-phase: 20s
management:
  endpoint:
    health:
      probes:
        enabled: true                     # /actuator/health/readiness and /liveness
      group:
        readiness:
          include: readinessState, db     # local only; do not include remote services
```
```haproxy
# L4: Postgres through the balancer; one backend per connection, TCP health check only
listen postgres
    bind *:5432
    mode tcp
    balance leastconn
    option tcp-check
    server pg1 10.20.5.10:5432 check inter 2s fall 3 rise 2
    server pg2 10.20.5.11:5432 check backup

# L7: the API; per-request routing, HTTP health check, retries for idempotent requests, canary by header
frontend https
    bind *:443 ssl crt /etc/haproxy/api.pem alpn h2,http/1.1
    mode http
    http-request set-header X-Forwarded-Proto https
    http-request set-header X-Forwarded-For %[src]
    use_backend api_canary if { req.hdr(X-Canary) -m str on }
    default_backend api

backend api
    mode http
    balance leastconn
    option httpchk GET /actuator/health/readiness
    http-check expect status 200
    retry-on conn-failure 503
    http-request disable-l7-retry if METH_POST      # never auto-retry non-idempotent requests
    server api1 10.20.6.10:8080 check inter 3s fall 2 rise 3
    server api2 10.20.6.11:8080 check inter 3s fall 2 rise 3
```

## Pitfalls
- Readiness that depends on remote services: one Kafka hiccup and every pod leaves the pool simultaneously.
- Rolling deploys without graceful shutdown or with `terminationGracePeriodSeconds` shorter than the longest request: 502/504 bursts on every release.
- gRPC or HTTP/2 through an L4 balancer: a single backend takes all streams; use L7 or client-side balancing over headless DNS ([[dns-resolution]]).
- Trusting `X-Forwarded-For` from the internet; the first hop must overwrite it, and the app must know how many hops are trusted.
- Retrying POSTs at the balancer "to improve availability": duplicates without an idempotency key ([[idempotency]]).
- Related: [[load-balancing]] for the architecture-level view, [[reverse-proxy-nginx-basics]] for nginx as the L7 implementation you will most often configure by hand.
