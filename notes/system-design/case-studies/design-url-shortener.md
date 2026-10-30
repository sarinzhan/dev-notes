---
title: Design a URL shortener
tags: [system-design, case-study, caching, key-generation]
learned: 2026-10-27
---
## TL;DR
The URL shortener is the warm-up case study: a tiny write path (store `code → long URL`) and a huge read path (redirect). The interesting decisions are how to generate short, unique, unguessable codes without a central bottleneck, how to make the redirect hit a cache rather than the database, and how to count clicks without putting a write on the read path. Say the numbers early: reads outnumber writes by 100:1, so this is a [[caching]] and [[cdn]] problem with a key-generation twist.

## Key points

### Requirements and estimates
- Functional: create a short link (optionally custom alias and expiry), redirect, basic analytics (clicks by day), delete.
- Non-functional: redirect latency < 50 ms at the edge, 99.99% read availability, codes not sequential (no enumeration), links live for years.
- Numbers: 10 M new links/month ≈ 4 writes/s; 100:1 → 400 redirects/s average, 4 000/s peak. Row ≈ 200 B → 2 GB/month, trivial. Over five years: 600 M links; a 7-character base62 code gives 62⁷ ≈ 3.5 × 10¹² values, enough.

### API
```
POST /v1/links            { url, alias?, expiresAt? }   -> 201 { code, shortUrl }
GET  /{code}              -> 302 Location: <url>  (301 if you want browsers to cache, see trade-offs)
GET  /v1/links/{code}     -> { url, createdAt, clicks }
DELETE /v1/links/{code}
```
- `POST` with an `Idempotency-Key` or a unique `(owner, url)` constraint so a retried create returns the same code ([[idempotency]]).
- Validate the target: scheme allow-list, no private IP ranges (SSRF via redirect), length limit ([[owasp-top-10-for-backend]]).

### Data model
- `link(code text PK, url text, owner_id, created_at, expires_at, deleted boolean)`; index on `(owner_id, created_at)`.
- `click_daily(code, day, count)` or raw `click(code, ts, referer, ua_hash)` partitioned by day for analytics ([[postgres-partitioning]]).
- Postgres is enough for 600 M rows; a KV store (DynamoDB/Cassandra) if multi-region writes are needed ([[storage-choices]]).

- Database-backend view (from the course): the simplest schema is `url_table(id serial PK, url text)` with the code = base62(id); the read is a primary-key lookup, the write an insert. Alternative: code = first 8 chars of a hash, stored in an indexed `short_url` column, with a collision check on insert. At billions of rows, the code itself becomes the shard key and the app routes to the shard that owns it ([[sharding-strategies]]).

### High-level design
```
browser ──GET /abc1234──▶ CDN / edge (cached 302 for hot codes)
                              │ miss
                              ▼
                      LB ──▶ redirect service (stateless) ──▶ Redis (code → url, TTL)
                                                                  │ miss
                                                                  ▼
                                                               Postgres (read replica)
                              │ async: click event ──▶ Kafka ──▶ analytics aggregator
create: API ──▶ key generator ──▶ Postgres primary ──▶ (warm Redis)
```

### Deep dive: key generation
| Method | How | Pros | Cons |
|---|---|---|---|
| Hash of URL | base62(md5/sha256(url))[0:7] | same URL → same code, no coordinator | collisions must be checked and resolved; two users shortening the same URL share a code and analytics |
| DB sequence + base62 | `nextval()` → encode | unique by construction, dense | sequential = enumerable; single primary for ids |
| Pre-generated key pool | a Key Generation Service fills a table of random unused codes; API takes one with `FOR UPDATE SKIP LOCKED` | random, no collision check at request time, horizontally scalable | one more service, keys must be marked used atomically |
| Random + retry | generate 7 random base62 chars, `INSERT ... ON CONFLICT DO NOTHING`, retry on conflict | simplest, no coordinator | collision probability grows with fill ratio (still tiny at 600 M / 3.5 T) |
| Snowflake-style id | time + node + sequence, then base62 | unique across nodes, roughly ordered | 11 characters, time-leaky ([[time-money-and-ids]]) |

- Interview-safe answer: random 7-char base62 with `ON CONFLICT` retry, mention the key pool as the scaled-out version and the sequence as the one to avoid for public links.
- Custom aliases share the namespace: reserve a prefix or a separate uniqueness check; block reserved words (`api`, `admin`).

### Deep dive: the read path
- Cache-aside in Redis keyed by code with a long TTL; the working set is skewed (a few % of links take most clicks), so hit rate above 95% is realistic ([[caching]]).
- Negative caching for unknown codes (short TTL) stops bots from scanning the database; a [[bloom-filters-and-probabilistic|Bloom filter]] of existing codes in each instance does the same cheaper.
- `302` vs `301`: `301` lets browsers and the CDN cache the redirect, which cuts load but loses click counting and makes URL changes stick in browsers; `302` (or `307`) keeps every click visible. Use `302` by default, `301` for immutable public links with `Cache-Control` ([[http-caching]]).
- Click counting is fire-and-forget: publish `{code, ts, referer}` to Kafka, aggregate per day in a consumer; never `UPDATE link SET clicks = clicks + 1` on the hot path ([[async-processing-and-event-driven-scaling]]).

### Trade-offs
- Postgres vs KV: Postgres wins on simplicity, custom aliases and owner listings; KV wins for multi-region active/active where a global unique constraint is unavailable ([[multi-region-basics]]).
- Expiry: lazy (check `expires_at` on read, purge in a nightly job) is enough; a TTL on the Redis key mirrors it.
- Analytics accuracy vs cost: exact counts via Kafka and daily rollups; approximate unique visitors via HyperLogLog in Redis.

## Example
Random code with conflict retry, and the redirect with cache-aside:

```java
private static final String ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

public String create(String url) {
    for (int attempt = 0; attempt < 5; attempt++) {
        String code = randomCode(7);
        int inserted = jdbc.update("""
            INSERT INTO link (code, url, created_at) VALUES (?, ?, now())
            ON CONFLICT (code) DO NOTHING""", code, url);
        if (inserted == 1) return code;
    }
    throw new IllegalStateException("could not allocate a code");
}

public Optional<String> resolve(String code) {
    String cached = redis.opsForValue().get("l:" + code);
    if (cached != null) return cached.isEmpty() ? Optional.empty() : Optional.of(cached);
    Optional<String> url = jdbc.query("SELECT url FROM link WHERE code = ? AND NOT deleted AND (expires_at IS NULL OR expires_at > now())",
            rs -> rs.next() ? Optional.of(rs.getString(1)) : Optional.empty(), code);
    redis.opsForValue().set("l:" + code, url.orElse(""), url.isPresent() ? Duration.ofDays(7) : Duration.ofMinutes(5));
    return url;
}
```

`randomCode` draws from `SecureRandom` over `ALPHABET`; the empty-string value is the negative-cache marker.

## Pitfalls
- Interviewers probe: "why not md5?" collisions and shared codes; "why not autoincrement?" enumeration and a single id source; "what if Redis dies?" the database still answers, with a cold-cache spike to handle via request coalescing ([[distributed-cache-coherence]]).
- "How do you delete a link that is cached at the CDN and in browsers?" You cannot recall a `301`; that is why default is `302` and the edge cache TTL is short.
- Open-redirect abuse: shorteners are phishing tools; validate targets, add a safe-browsing check, allow reporting.
- Counting clicks synchronously turns a 4 000/s read load into a 4 000/s write load on the primary.
- Related: [[system-design-basics]], [[load-balancing]], [[hash-tables]] for why 7 random characters are enough.
