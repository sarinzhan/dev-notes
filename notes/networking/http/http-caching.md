---
title: HTTP caching
tags: [networking, http, caching, etag, cdn]
learned: 2026-10-27
---
## TL;DR
HTTP caching is controlled by the server through response headers: `Cache-Control` says who may store the response and for how long, `ETag`/`Last-Modified` let a client revalidate cheaply with a conditional request that returns `304 Not Modified`, and `Vary` says which request headers change the answer. Browsers, reverse proxies and CDNs all follow the same rules, so one correct set of headers makes every layer work; one wrong `Cache-Control: public` on a per-user endpoint leaks data through a shared cache. Application-side caching strategies (cache-aside, write-through, invalidation) are a different topic owned by [[caching]].

## Key points
### Freshness: `Cache-Control`
| Directive | Meaning |
|---|---|
| `max-age=N` | fresh for N seconds from the response time; serve without contacting origin |
| `s-maxage=N` | same, but only for shared caches (CDN, proxy); overrides `max-age` there |
| `no-cache` | may store, but must revalidate before every use (name is misleading) |
| `no-store` | never store anywhere: tokens, personal data, payment pages |
| `private` | only the end user's browser may store; shared caches must not |
| `public` | shared caches may store even responses with `Authorization` |
| `must-revalidate` | once stale, do not serve stale while revalidating or on error |
| `stale-while-revalidate=N` | serve the stale copy for N s while fetching a fresh one in the background |
| `stale-if-error=N` | serve stale if origin returns 5xx; cheap resilience at the CDN |
| `immutable` | never revalidate within `max-age`; for content-hashed assets (`app.3f9c1a.js`) |

- Heuristic caching: with no `Cache-Control` and no `Expires`, caches may guess a lifetime from `Last-Modified` (typically 10% of the age). Always send an explicit policy on APIs, usually `no-store` or `private, max-age=0`.
- `Expires` is the HTTP/1.0 absolute-date form; `Cache-Control` wins when both are present.
- Only safe methods are cacheable by default; a `POST` response is cached only with explicit headers and almost never in practice.

### Validation: conditional requests
- **`ETag`**: opaque version identifier, strong (`"3f9c1a"`) or weak (`W/"3f9c1a"`, semantically equal, bytes may differ). Client sends it back in `If-None-Match`; the server answers `304` with no body if unchanged. Cheap to compute from `updated_at`, a version column or a hash of the body.
- **`Last-Modified`** + `If-Modified-Since`: one-second granularity, fine for files, weak for rows updated several times a second.
- The same validators protect writes: `If-Match: "3f9c1a"` on PUT/PATCH gives optimistic concurrency over HTTP, returning `412 Precondition Failed` when someone else changed the resource first ([[jpa-locking]] does the same at the database layer).
- A `304` still costs a round trip; the win is bandwidth and server work, not latency. `max-age` is what removes the round trip.

### `Vary` and cache keys
- A cache keys entries by method + URL. If the response depends on a request header, say so: `Vary: Accept-Encoding` (gzip vs identity), `Vary: Accept-Language`, `Vary: Origin` for CORS responses ([[cookies-sessions-and-cors]]).
- `Vary: Authorization` or `Vary: Cookie` effectively makes the response uncacheable in shared caches, which is usually the right outcome for personalised data; prefer `private`.
- `Vary: *` means never serve from cache.

### Where caches sit
1. Browser cache (per user): `private` is enough.
2. Reverse proxy / nginx `proxy_cache` ([[reverse-proxy-nginx-basics]]): honours `s-maxage`, can add `stale-while-revalidate`.
3. CDN edge ([[cdn]]): same rules, plus purge APIs and signed URLs; the right place for `public, s-maxage=3600` on catalog-like data.
4. API gateway response cache: usually configured by route, ignores headers; dangerous for anything per-user.

### Spring side
- `ResponseEntity.ok().cacheControl(CacheControl.maxAge(10, MINUTES).cachePublic()).eTag(version).body(dto)`.
- `WebRequest.checkNotModified(etag)` in a controller returns `true` and sets up the `304` for you when `If-None-Match` matches, so you skip serialisation.
- `ShallowEtagHeaderFilter` hashes every response body to produce an ETag: saves bandwidth, not CPU or database work. Fine for small APIs, wrong for large or streaming responses.
- Static resources: `spring.web.resources.cache.cachecontrol.max-age=365d` plus content-hashed filenames via the resource chain.

## Example
Tariff catalog endpoint: public data, changes a few times a day, read by every app start. Safe to cache at the CDN for 10 minutes and revalidated by ETag afterwards.
```java
@GetMapping("/api/v1/tariffs")
public ResponseEntity<List<TariffDto>> tariffs(WebRequest request) {
    CatalogVersion v = catalog.currentVersion();            // one cheap query: max(updated_at)
    String etag = "\"" + v.hash() + "\"";
    if (request.checkNotModified(etag)) {
        return null;                                         // Spring writes 304 + ETag, no body
    }
    return ResponseEntity.ok()
            .eTag(etag)
            .cacheControl(CacheControl.maxAge(10, TimeUnit.MINUTES)
                    .cachePublic()
                    .staleWhileRevalidate(60, TimeUnit.SECONDS))
            .header(HttpHeaders.VARY, "Accept-Language")
            .body(catalog.list(request.getLocale()));
}
```
What the wire looks like on the second call:
```bash
$ curl -sI https://api.beeline.kg/api/v1/tariffs -H 'If-None-Match: "9c4e12"' -H 'Accept-Language: ru'
HTTP/2 304
etag: "9c4e12"
cache-control: max-age=600, public, stale-while-revalidate=60
vary: Accept-Language
age: 143                       # the CDN served it; 143 s into the 600 s window
```
A per-subscriber endpoint such as `/api/v1/subscribers/{msisdn}/balance` gets the opposite policy: `Cache-Control: no-store` and no ETag, so nothing between the app and the service keeps a copy.

## Pitfalls
- `public` on a response that depends on `Authorization`: the CDN hands user A's data to user B. Default to `private` or `no-store` for anything behind auth.
- Forgetting `Vary: Accept-Encoding` on a proxy that caches: a client that cannot gunzip receives a gzipped body.
- ETags computed from the serialized body with `ShallowEtagHeaderFilter` still run the full query and serialization; use a version column.
- `no-cache` read as "do not cache": it caches and revalidates. For secrets you want `no-store`.
- Long `max-age` on non-hashed assets or on JSON that changes: the only fixes are URL versioning or a CDN purge, both of which should be designed in up front.
- Related: [[http-fundamentals]] for conditional request mechanics, [[caching]] for application-level cache strategies and stampede protection.
