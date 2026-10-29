---
title: Reverse proxy basics with nginx
tags: [networking, nginx, reverse-proxy, tls-termination, timeouts]
learned: 2026-10-27
---
## TL;DR
A reverse proxy sits in front of your services, accepts client connections, and opens its own, separate connections to upstreams. That hop gives you TLS termination, one public port for many services, path routing, caching, canaries, buffering that shields Tomcat threads from slow clients, and a place for rate limits and access logs. nginx is the one you will configure by hand most often; Envoy, Traefik, Caddy and HAProxy share the same concepts. Every "502 Bad Gateway" or "504 Gateway Timeout" you have seen came from this layer.

## Key points
### Forward vs reverse proxy
| | Forward proxy | Reverse proxy |
|---|---|---|
| Sits | next to clients, configured by them (`HTTP_PROXY`, browser, JVM `-Dhttps.proxyHost`) | next to servers; clients think it *is* the server |
| Hides | the clients (the target sees the proxy's IP) | the servers (topology, number of instances) |
| Use cases | egress allow-lists, logging outbound calls, shared caching (Squid, Maven mirror), anonymity, TLS inspection ([[tls-handshake-and-certificates]]) | load balancing, TLS termination, caching (`proxy_cache`), canary by weight or header, microservice ingress (routing by path), API gateway (auth, rate limits), central access logs |

- Load balancer, API gateway and ingress controller are reverse proxies with extra features ([[load-balancers-l4-vs-l7]], [[api-gateway-and-service-discovery]]).
- **Transparent proxy**: clients are not configured at all; the network redirects their traffic to the proxy (iptables `REDIRECT`/`TPROXY`, policy routing). Corporate web filters and service-mesh sidecars (Envoy in Istio intercepts every pod connection this way) work like this. Without TLS interception it sees only IPs, ports and the SNI of HTTPS traffic.

### Two TCP connections, not one
- A proxy terminates the client's connection and opens its own to the upstream: two handshakes, two TLS sessions, two sets of buffers, timeouts and keepalive settings. Client HTTP/2 can become upstream HTTP/1.1. An L4 balancer in NAT mode, by contrast, rewrites packets of a single connection.
- Consequences: the upstream sees the proxy's IP (hence `X-Forwarded-For`); a client disconnect does not cancel the upstream request; each upstream connection uses an ephemeral port on the proxy, so without keepalive a busy proxy runs out of ports to one `IP:port` (about 28 000 by default) and piles up `TIME_WAIT` ([[connection-establishment-cost]], [[sockets-and-connection-pooling]]).

### nginx building blocks
| Directive | What it does | Typical value |
|---|---|---|
| `upstream api { server ...; }` | pool of backends; `least_conn`, `ip_hash`, `keepalive N` | `keepalive 32` to reuse upstream connections |
| `proxy_pass http://api;` | forward the request; trailing slash on `proxy_pass` rewrites the path prefix | watch the slash |
| `proxy_set_header Host $host;` | by default nginx sends the upstream name as `Host` | always set it |
| `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` | append client IP | plus `X-Forwarded-Proto $scheme`, `X-Real-IP $remote_addr` |
| `proxy_http_version 1.1;` + `Connection ""` | required for upstream keepalive and WebSocket | default is 1.0 |
| `proxy_connect_timeout` | TCP connect to upstream | 2–5 s |
| `proxy_read_timeout` | wait between two reads from upstream (not total) | 30–60 s; hours for SSE/WebSocket |
| `client_max_body_size` | request body limit; `413` beyond | `10m` for uploads |
| `proxy_buffering on` | read the whole upstream response, then send to the client at its pace | on for APIs, off for streams |
| `proxy_next_upstream error timeout http_502` | retry on another backend | add `non_idempotent` only if you really mean it |
| `limit_req zone=api burst=20 nodelay;` | leaky-bucket rate limit per key | first line of defence ([[rate-limiting]]) |

### Buffering: why it exists and when to turn it off
- With buffering on, nginx takes the full response from Tomcat quickly and feeds a slow mobile client over seconds; the Tomcat thread is freed immediately. Without it, the servlet thread is tied to the client's bandwidth. Default on, keep it for normal APIs.
- Turn it off (`proxy_buffering off` or `X-Accel-Buffering: no` header from the app) for SSE, chunked streaming responses, LLM token streams ([[websockets-sse-and-long-polling]]). Request bodies similarly: `proxy_request_buffering off` for large uploads you want to stream to the app.

### TLS termination
- nginx holds the certificate and private key (`ssl_certificate`, `ssl_certificate_key`), speaks HTTPS and HTTP/2 to the client, and usually plain HTTP to the upstream on a private network. The app must then build URLs with `X-Forwarded-Proto` (Spring: `server.forward-headers-strategy`).
- Re-encrypting to the upstream (`proxy_pass https://...`, `proxy_ssl_verify on`) when the network is not trusted; passthrough at L4 (`stream {}` block with `ssl_preread` for SNI routing) when the backend must see the client certificate ([[mtls-and-service-to-service-auth]]).
- OCSP stapling, `ssl_protocols TLSv1.2 TLSv1.3` and HSTS belong here, not in every service.

### Status codes it generates
- `502 Bad Gateway`: upstream refused, reset, or sent an invalid response (common on deploys when nginx reuses a keepalive connection the app closed; set `keepalive_timeout` on Tomcat ≥ nginx's `keepalive_time`).
- `504 Gateway Timeout`: `proxy_read_timeout` or `proxy_connect_timeout` hit; the app may still be working on the request, which is how duplicate processing happens without idempotency ([[idempotency]]).
- `499`: the client closed the connection before nginx answered (only in logs); a wave of 499s means clients time out before you do.
- `413`, `429`, `503` from `limit_req`/`limit_conn`.

### Observability
- JSON access log with `$request_time` (client-facing total) and `$upstream_response_time` (the app's share); the difference is buffering and slow clients. Forward `$request_id` as `X-Request-ID` to correlate app logs and traces ([[distributed-tracing]], [[observability-three-pillars]]).

## Example
A production-shaped `server` block for a Spring Boot API: TLS, HTTP/2, upstream keepalive, forwarded headers, a streaming location with buffering off, rate limit on the auth endpoint, and JSON access logs.
```nginx
upstream esim_api {
    least_conn;
    server 10.20.6.10:8080 max_fails=2 fail_timeout=10s;
    server 10.20.6.11:8080 max_fails=2 fail_timeout=10s;
    keepalive 32;                       # pooled connections to upstreams
}

limit_req_zone $binary_remote_addr zone=auth:10m rate=5r/s;

log_format json escape=json '{"time":"$time_iso8601","request_id":"$request_id","status":$status,'
    '"method":"$request_method","uri":"$request_uri","rt":$request_time,'
    '"upstream_rt":"$upstream_response_time","upstream":"$upstream_addr","client":"$remote_addr"}';

server {
    listen 443 ssl;
    http2 on;
    server_name api.beeline.kg;
    ssl_certificate     /etc/letsencrypt/live/api.beeline.kg/fullchain.pem;   # leaf + intermediates
    ssl_certificate_key /etc/letsencrypt/live/api.beeline.kg/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=31536000" always;
    access_log /var/log/nginx/api.json json;
    client_max_body_size 5m;

    location /api/ {
        proxy_pass http://esim_api;                          # no trailing slash: path passed unchanged
        proxy_http_version 1.1;
        proxy_set_header Connection "";                      # enables upstream keepalive
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;
        proxy_connect_timeout 3s;
        proxy_read_timeout 30s;
        proxy_next_upstream error timeout http_502;          # idempotent-only by default
    }

    location /api/v1/provisioning/ {                        # SSE: stream, long timeout
        proxy_pass http://esim_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_read_timeout 1h;
    }

    location /api/v1/auth/ {
        limit_req zone=auth burst=10 nodelay;
        proxy_pass http://esim_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /actuator/ { deny all; }                        # never public
}
```
Validate and reload without dropping connections:
```bash
$ sudo nginx -t && sudo nginx -s reload
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
$ curl -sI https://api.beeline.kg/api/v1/tariffs | grep -iE 'HTTP|x-request-id|strict'
HTTP/2 200
```

## Pitfalls
- `proxy_pass http://api/` with a trailing slash strips the `location` prefix; without it the full path is forwarded. Half of all "404 behind nginx" tickets.
- Missing `proxy_http_version 1.1` + `Connection ""`: no upstream keepalive, a new TCP connection per request, `TIME_WAIT` pile-up on the proxy ([[tcp-handshake-and-connection-states]]).
- `proxy_read_timeout` is between reads, not a total deadline; a slowly trickling upstream can hold a request for minutes.
- Forgetting to forward `Host` and `X-Forwarded-Proto`: Spring builds `http://esim_api/...` redirects and `Location` headers that point at the upstream name.
- nginx resolves `proxy_pass` hostnames once at startup unless a `resolver` is set and a variable is used; after a DNS change it keeps the old IP ([[dns-resolution]]).
- Related: [[load-balancers-l4-vs-l7]] for the balancing side of the same box, [[cdn]] for the layer in front of it.
