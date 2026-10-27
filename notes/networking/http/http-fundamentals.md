---
title: HTTP fundamentals
tags: [networking, http, headers, status-codes]
learned: 2026-10-27
---
## TL;DR
HTTP is a stateless request/response protocol on top of a reliable byte stream: a start line, headers, blank line, optional body. The method says what you want, headers say how to interpret it, the status code says what happened. Safe and idempotent methods are what makes retries, caching and proxies possible; everything else is convention. This note is about the protocol itself; REST conventions, URL design, status-code etiquette and CORS in Spring live in [[rest-and-http]] and are not repeated here.

## Key points
### Message anatomy
```
POST /api/v1/esims/8999600000000000001/activations HTTP/1.1      <- request line
Host: api.beeline.kg                                              <- mandatory in 1.1, selects the virtual host
Content-Type: application/json
Content-Length: 27
Idempotency-Key: 0b1f0a3e-...                                     <- custom header, hyphenated, case-insensitive
                                                                  <- empty line ends the headers
{"channel":"MY_BEELINE_APP"}                                      <- body, exactly Content-Length bytes
```
- Headers are case-insensitive, values are text, duplicates are joined with commas (except `Set-Cookie`). HTTP/2 lowercases them and stores them as binary fields, which is why `X-Request-ID` and `x-request-id` must be treated the same.
- Body length is signalled by `Content-Length`, by `Transfer-Encoding: chunked` (stream of length-prefixed chunks, final `0` chunk), or by closing the connection (1.0 only). Mismatch between length and actual bytes is the classic cause of "hangs" and request smuggling.
- A request without a body (GET, DELETE) still may legally carry one; servers mostly ignore it.

### Methods: safety and idempotency are the point
- **Safe** (GET, HEAD, OPTIONS): no state change intended; caches and crawlers may issue them freely.
- **Idempotent** (GET, HEAD, PUT, DELETE, OPTIONS): N identical requests have the effect of one. A client, proxy or load balancer may retry them after a timeout without asking. POST and PATCH are not idempotent by definition; retry them only with an idempotency key ([[idempotency]]).
- `HEAD` returns GET's headers without the body (cheap existence/size check); `OPTIONS` lists allowed methods and carries CORS preflight ([[cookies-sessions-and-cors]]).

### Status classes and the ones that matter for retries
| Class | Meaning | Retry? |
|---|---|---|
| 1xx | informational (`100 Continue`, `101 Switching Protocols` for WebSocket) | n/a |
| 2xx | success; `202 Accepted` = work queued, poll later | no |
| 3xx | redirect; `304 Not Modified` is the cache hit ([[http-caching]]) | follow, but not for POST unless 307/308 |
| 4xx | client error; `408`, `425`, `429` are the only ones worth retrying (with backoff) | mostly no |
| 5xx | server error; `502/503/504` usually transient at a gateway | yes with backoff and budget, only for idempotent requests |

### Headers you must know by name
| Header | Direction | Purpose |
|---|---|---|
| `Host` | req | virtual host selection; required in HTTP/1.1 |
| `Content-Type` / `Accept` | both / req | body media type / content negotiation |
| `Content-Length`, `Transfer-Encoding` | both | framing |
| `Connection: keep-alive` / `close` | both | 1.1 defaults to keep-alive; `close` forces teardown |
| `Authorization: Bearer ...` | req | credentials ([[authentication-vs-authorization]]) |
| `Cache-Control`, `ETag`, `If-None-Match` | both | caching ([[http-caching]]) |
| `Location` | resp | where the created resource or redirect target is |
| `Retry-After` | resp | with 429/503, seconds or date |
| `X-Forwarded-For`, `X-Forwarded-Proto`, `Forwarded` | req | original client IP/scheme, added by proxies ([[reverse-proxy-nginx-basics]]) |
| `traceparent` | req | W3C trace context ([[distributed-tracing]]) |

### Persistent connections and pipelining
- HTTP/1.0 closed the connection after each response. HTTP/1.1 keeps it open by default (keep-alive); one request at a time per connection, so clients open several (browsers: 6 per host). Pipelining (sending the next request before the previous answer) exists in the spec but is disabled everywhere because responses must come back in order.
- Multiplexing on one connection is what HTTP/2 adds ([[http-1-1-vs-2-vs-3]]). Server-side keep-alive limits in Spring Boot/Tomcat: `server.tomcat.keep-alive-timeout`, `server.tomcat.max-keep-alive-requests`.

### Statelessness
- The protocol carries no memory between requests; every request must be self-contained. Session state is layered on with cookies ([[cookies-sessions-and-cors]]) or tokens ([[jwt-authentication]]). This is exactly why HTTP services scale horizontally behind a load balancer.

## Example
Watching the raw protocol with `curl -v` against a Spring Boot service, which is the fastest way to see headers the browser hides.
```bash
$ curl -sv -X POST https://api.beeline.kg/api/v1/esims/8999600000000000001/activations \
     -H 'Content-Type: application/json' -H 'Idempotency-Key: 0b1f0a3e-7c1e-4d1a-9f0a-1a2b3c4d5e6f' \
     -d '{"channel":"MY_BEELINE_APP"}'
> POST /api/v1/esims/8999600000000000001/activations HTTP/2
> Host: api.beeline.kg
> user-agent: curl/8.6.0
> accept: */*
> content-type: application/json
> idempotency-key: 0b1f0a3e-7c1e-4d1a-9f0a-1a2b3c4d5e6f
> content-length: 28
>
< HTTP/2 202
< content-type: application/json
< location: /api/v1/esims/8999600000000000001/activations/7f3c
< retry-after: 2
< x-request-id: 4a8d0d6e
<
{"status":"PENDING","activationId":"7f3c"}
```
`202 + Location + Retry-After` is the standard shape for "accepted, poll here"; the same request with the same `Idempotency-Key` must return the same `activationId` instead of starting a second workflow.

## Pitfalls
- Returning `200` with an error inside the body; clients, proxies and monitoring key off status codes.
- Retrying a POST on timeout because "the server was probably down": the request may have been processed. Timeouts are ambiguous; only idempotent requests are safe to retry blindly.
- Forgetting `Host` when talking HTTP by hand (`nc`, raw sockets): HTTP/1.1 servers answer `400`.
- Missing or wrong `Content-Length` makes the peer wait for more bytes until the read timeout; chunked encoding without the terminating `0` chunk does the same.
- Headers are not a secure channel for secrets by themselves; they are only protected inside TLS ([[tls-handshake-and-certificates]]) and they end up in proxy logs unless scrubbed.
- Related: [[rest-and-http]] for API design on top of all this, [[spring-mvc-request-lifecycle]] for how Spring parses the request.
