---
title: REST principles and HTTP essentials
tags: [rest, http, api-design, cors]
learned: 2026-10-22
---
## TL;DR
**REST** (Representational State Transfer) is an architectural style for distributed systems built on HTTP: resources identified by URIs, manipulated with the standard methods, stateless requests, cacheable responses, a uniform interface. An API that follows all the constraints is **RESTful**. HTTP itself is a text-based (until HTTP/2) application-level request/response protocol: start line, headers, body; methods with defined safety and idempotency; status codes in five classes. **CORS** is the browser-side rule set for calling an API on another origin.

## Key points
### Definitions
- **API** (Application Programming Interface): a description of how one program talks to another; for a web service, the set of endpoints, their inputs and outputs.
- **REST**: a set of constraints (not a protocol or standard) for designing APIs that are simple to use and scale well.
- **RESTful API**: an API that satisfies all the REST constraints. Most "REST" APIs stop before HATEOAS; that is fine in practice.

### REST constraints
- **Client-server**: the client initiates a request, the server processes it and returns a **resource** representation. Resources have a unique address (URI). The two sides evolve independently.
- **Stateless**: the server keeps no client session between requests; each request carries everything needed (auth token, parameters). This is what makes horizontal scaling trivial.
- **Cacheable**: responses declare whether they can be cached (`Cache-Control`, `ETag`, `Last-Modified`); intermediaries (CDN, proxy) may serve them.
- **Layered system**: the client cannot tell whether it talks to the origin server or to a proxy, load balancer or gateway in between.
- **Uniform interface**: resources identified by URI; representations (JSON) are separate from the resource; **self-descriptive messages** (content type, status, headers tell how to process them); **HATEOAS**: responses link to related resources so the client can navigate (Spring HATEOAS implements it; rarely used in internal APIs).

### HTTP
- Application-layer protocol, originally for HTML, now the universal transport for web data. Resources are addressed by URL; client-server; text-based in 1.x.
- Versions: 1.0, 1.1 (keep-alive; one request at a time per connection), **2** (binary, multiplexing on one connection, header compression), **3** (QUIC over UDP). Server-side settings are in [[spring-boot-properties-and-profiles]].
- Message structure: **start line** (request: method, URL, version; response: version, status, reason), **headers** (metadata: `Content-Type`, `Authorization`, `Accept`, `Cache-Control`), empty line, **body** (data).
- Status classes: `1xx` informational, `2xx` success (200 OK, 201 Created, 202 Accepted, 204 No Content), `3xx` redirect (301/302/304 Not Modified), `4xx` client error (400, 401 Unauthorized = not authenticated, 403 Forbidden = authenticated but not allowed, 404, 405, 409 Conflict, 415, 422, 429 Too Many Requests), `5xx` server error (500, 502, 503, 504).

### Methods
| Method | Action | Safe | Idempotent | Body |
|---|---|---|---|---|
| GET | read | yes | yes | no |
| HEAD | headers only, resource metadata | yes | yes | no |
| OPTIONS | allowed methods, CORS preflight | yes | yes | rarely |
| POST | create / non-idempotent action | no | no | yes |
| PUT | full replace | no | yes | yes |
| PATCH | partial update | no | no (by spec; usually in practice) | yes |
| DELETE | remove | no | yes | allowed but ignored by most servers |
| TRACE | echo request | yes | yes | no |

- **Safe** = does not change server state. **Idempotent** = repeating the request has the same effect as doing it once; this is what lets clients and proxies retry safely. POST is retried only with an idempotency key ([[idempotency]]).
- Authentication and authorization endpoints (login, token exchange, even OAuth 2.0 / OIDC token endpoint) use POST: credentials go in the body, not in a URL that ends up in logs.

### Where data goes
- **Path parameters** (`/orders/42`): identify the resource; without them the request is meaningless.
- **Query parameters** (`?status=ACTIVE&page=2&sort=createdAt,desc`): filtering, sorting, pagination, optional switches.
- **Body**: the representation to create or update (JSON); also for large or sensitive inputs.
- Headers: cross-cutting metadata (auth, content negotiation, tracing ids, `Idempotency-Key`).

### CORS
- **Cross-Origin Resource Sharing** is a browser security mechanism that governs whether JavaScript loaded from one *origin* (scheme + host + port, e.g. `https://my-frontend.com`) may read responses from another (`https://api-backend.com`).
- By default the browser blocks such reads (same-origin policy) to limit attacks like CSRF and data theft via XSS. The server opts in with `Access-Control-Allow-Origin` / `-Methods` / `-Headers` / `-Credentials`; for non-simple requests the browser first sends an `OPTIONS` **preflight**.
- Enforced only by browsers; curl, Postman and server-to-server calls ignore it. It is not authentication.
- Spring: `@CrossOrigin` on a controller, a global `WebMvcConfigurer.addCorsMappings`, or `http.cors()` with a `CorsConfigurationSource` bean when Spring Security is present ([[spring-security-basics]]).

## Example
A resource-oriented API for one entity:
```
GET    /api/v1/subscribers/{msisdn}/esims            200, list (query: ?status=ACTIVE&page=0&size=20)
POST   /api/v1/subscribers/{msisdn}/esims            201 + Location: /api/v1/esims/{iccid}
GET    /api/v1/esims/{iccid}                         200 | 404
PUT    /api/v1/esims/{iccid}                         200, full replace (idempotent)
PATCH  /api/v1/esims/{iccid}                         200, {"status":"SUSPENDED"}
DELETE /api/v1/esims/{iccid}                         204 | 404 (second call also 404; still idempotent in effect)
POST   /api/v1/esims/{iccid}/activations             202, action as a sub-resource, Idempotency-Key header
```
Error body (RFC 9457 Problem Details, what Spring 6 `ProblemDetail` produces):
```json
{ "type": "https://api.beeline.kg/errors/esim-not-found", "title": "Not Found",
  "status": 404, "detail": "ICCID 8999600000000000001 not found", "instance": "/api/v1/esims/8999600000000000001" }
```

## Pitfalls
- Verbs in URLs (`/getOrder`, `/createUser`) and using GET for state changes (crawlers and prefetch will trigger them).
- Returning 200 with `{"error": ...}` inside; clients and monitoring rely on status codes.
- Mixing up 401 and 403, and leaking existence of resources via 403 vs 404.
- Sessions in a "stateless" API: sticky sessions break scaling; use tokens ([[jwt-authentication]]).
- Breaking changes without versioning (`/v1/`, or media-type versioning); additive changes (new optional fields) are safe, removals and renames are not.
- Related: [[spring-mvc-request-lifecycle]] for how Spring maps all this, [[rest-clients]] for the calling side, [[openapi-swagger]] for documenting it.
