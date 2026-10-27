---
title: CDN — edge caching, static vs dynamic content, invalidation and signed URLs
tags: [system-design, cdn, caching, networking]
learned: 2026-10-27
---
## TL;DR
A CDN is a globally distributed set of reverse proxies (points of presence, PoPs) that serve responses from a cache close to the user, so that a Bishkek user gets an image from Almaty in 10 ms instead of from Frankfurt in 90 ms, and the origin sees one request per object per PoP instead of one per user. It is the first building block for anything with static assets, downloads or public read-mostly APIs. The decisions are: what is cacheable and for how long (`Cache-Control`), how to invalidate (versioned URLs beat purges), how to protect private objects (signed URLs), and what still has to reach the origin (dynamic, personalised responses, which the CDN only accelerates by terminating TLS near the client).

## Key points

### How a request flows
1. DNS resolves `cdn.example.kg` to the nearest PoP (geo-DNS or anycast, [[dns-resolution]], [[load-balancing]]).
2. The PoP terminates TLS and looks up the cache key: by default scheme + host + path + query; headers only if configured (`Vary`).
3. **Hit**: served from the edge, latency = user ↔ PoP RTT. **Miss**: the PoP (or a mid-tier "shield" PoP, to collapse misses from many edges) fetches from the origin over a warm keep-alive connection, stores it according to the response headers, and serves it.
4. The origin is your load balancer or a blob bucket ([[file-and-blob-storage]]). An S3-style bucket behind a CDN is the standard way to serve user uploads.

### What to cache
| Content | Cacheable at edge | Typical TTL | Note |
|---|---|---|---|
| Versioned static assets (`app.3f9a1c.js`, images) | yes | 1 year, `immutable` | content hash in the name, never changes |
| User uploads, media | yes | days–months | private ones via signed URLs |
| Public API GETs (catalogue, tariffs, news) | yes | 10 s–10 min | `s-maxage` for the edge, `max-age` for browsers |
| Personalised responses (`/me`, balances) | no (`private`, `no-store`) | — | CDN still terminates TLS close to the user and keeps a warm connection to the origin |
| HTML app shell | short | 0–60 s, revalidate | so a deploy is visible quickly |
| `POST`/`PUT`, WebSockets | no | — | proxied through, benefit from edge TLS and DDoS filtering |

Headers that drive it ([[http-caching]] has the full semantics): `Cache-Control: public, max-age=60, s-maxage=600, stale-while-revalidate=30`, `ETag` for conditional revalidation (`304` is cheap), `Vary: Accept-Encoding` (keep the `Vary` list tiny, each value multiplies cache entries).

### Invalidation
- **Versioned URLs** (content hash or `?v=` + build id) are the only invalidation that always works: the new deploy references new names, old objects expire by themselves. Index HTML gets a short TTL so it picks up the new names.
- **Purge API**: by URL, by tag/surrogate key (`Surrogate-Key: tariff-42`) or everything. Propagation takes seconds to a minute across PoPs and is rate-limited; keep it for emergencies and for objects that cannot be renamed (a user's avatar at a stable URL).
- **Short TTL + `stale-while-revalidate`**: the edge serves the stale copy immediately and refreshes in the background, so users never wait for the origin and staleness is bounded by the TTL. `stale-if-error` keeps serving during an origin outage, a free availability boost for read-only content.

### Signed URLs and private content
- A private object (invoice PDF, an eSIM QR code, a recording) should not be on a guessable public URL. The origin or the application generates a **signed URL**: path + expiry + optional client IP, signed with a key the CDN or bucket knows (HMAC or RSA). The CDN verifies the signature and expiry before serving; the application never streams the bytes itself.
- Expiry of minutes for one-off downloads, hours for media playback; signed **cookies** cover a whole prefix (all segments of a video). Revocation is "wait for expiry" or rotate the key; design for short lifetimes ([[symmetric-and-asymmetric-crypto]]).
- Uploads go the same way in reverse: a presigned `PUT` to the bucket so the upload bypasses the application ([[file-and-blob-storage]]).

### What the CDN does besides caching
- **TLS termination at the edge** and HTTP/3 to the user, HTTP/2 with long-lived connections to the origin: a 3-RTT handshake over 80 ms becomes 3 × 10 ms ([[tls-handshake-and-certificates]], [[http-1-1-vs-2-vs-3]]).
- **DDoS absorption and WAF**: volumetric attacks hit anycast capacity, not your LB; [[rate-limiting]] per IP at the edge; bot rules.
- **Origin shielding and request collapsing**: one miss per object per shield, not per PoP; protects the origin from a stampede when a popular object expires ([[caching]]).
- **Edge compute**: small functions at the PoP (A/B routing, header rewrites, auth checks of a signed token) without an origin round trip.
- **Compression and image transforms**: Brotli at the edge, resizing `?w=400` variants on demand.

### Sizing intuition
If 5 M subscribers open the app daily and each load pulls 2 MB of assets, that is 10 TB/day of egress; a 99 % edge hit ratio leaves the origin with 100 GB/day and ~10 Mbit/s, which a single bucket serves without noticing. Egress pricing, not CPU, is usually the cost line that a CDN changes ([[back-of-envelope-estimation]]).

## Example
Spring MVC configuration for a public tariff endpoint and versioned assets, plus a signed-URL generator for private invoice PDFs. The CDN reads the headers; nothing CDN-specific lives in the code.

```java
@RestController
@RequestMapping("/v1/tariffs")
class TariffController {
    private final TariffService tariffs;
    TariffController(TariffService tariffs) { this.tariffs = tariffs; }

    @GetMapping
    ResponseEntity<List<TariffDto>> list(WebRequest request) {
        var snapshot = tariffs.currentSnapshot();                       // version + list, cheap to compute
        String etag = "\"" + snapshot.version() + "\"";
        if (request.checkNotModified(etag)) return null;               // Spring returns 304 with the headers below
        return ResponseEntity.ok()
                .eTag(etag)
                .cacheControl(CacheControl.maxAge(Duration.ofSeconds(60))      // browsers
                        .cachePublic()
                        .sMaxAge(Duration.ofMinutes(10))                        // CDN edge
                        .staleWhileRevalidate(Duration.ofSeconds(30))
                        .staleIfError(Duration.ofHours(1)))
                .body(snapshot.items());
    }
}

@Configuration
class StaticAssetsConfig implements WebMvcConfigurer {
    @Override public void addResourceHandlers(ResourceHandlerRegistry registry) {
        registry.addResourceHandler("/assets/**")
                .addResourceLocations("classpath:/static/assets/")
                .setCacheControl(CacheControl.maxAge(Duration.ofDays(365)).cachePublic().immutable())
                .resourceChain(true)
                .addResolver(new VersionResourceResolver().addContentVersionStrategy("/**"));  // app.js -> app-3f9a1c.js
    }
}

@Component
class SignedUrlService {                                               // CDN-agnostic HMAC scheme
    private final byte[] key; private final String base;
    SignedUrlService(@Value("${cdn.signing-key}") String key, @Value("${cdn.base}") String base) {
        this.key = key.getBytes(StandardCharsets.UTF_8); this.base = base;
    }
    String invoiceUrl(long subscriberId, String invoiceId, Duration ttl) {
        long expires = Instant.now().plus(ttl).getEpochSecond();
        String path = "/private/invoices/%d/%s.pdf".formatted(subscriberId, invoiceId);
        String sig = hmacBase64Url(path + ":" + expires);               // edge function recomputes and compares
        return base + path + "?exp=" + expires + "&sig=" + sig;
    }
    private String hmacBase64Url(String data) {
        try {
            var mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return Base64.getUrlEncoder().withoutPadding().encodeToString(mac.doFinal(data.getBytes(StandardCharsets.UTF_8)));
        } catch (GeneralSecurityException e) { throw new IllegalStateException(e); }
    }
}
```

## Pitfalls
- Caching a personalised response as public: one user's `/me` served to everyone. Any endpoint that reads `Authorization` or a session cookie must send `Cache-Control: private, no-store`.
- `Vary: Cookie` or `Vary: User-Agent`: effectively disables caching (one entry per cookie value) while looking cached in the config.
- Relying on purges for deploys: a purge takes a minute to propagate and is rate-limited; version the assets instead.
- Query-string cache keys: `?utm_source=` variants multiply misses; configure the CDN to ignore marketing parameters.
- Signed URL with a long expiry that then leaks (shared link, logs): the object is public for a week. Minutes for downloads; put the user id in the signed path so one link cannot be reused for another object.
- Related: [[http-caching]] for header semantics; [[file-and-blob-storage]] for the origin that most CDN traffic actually comes from.
