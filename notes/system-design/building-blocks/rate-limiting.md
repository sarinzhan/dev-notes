---
title: Rate limiting — token bucket, leaky bucket, sliding window, distributed limits in Redis
tags: [system-design, rate-limiting, redis, resilience]
learned: 2026-10-27
---
## TL;DR
A rate limiter protects a resource from more requests than it can or should handle: your API from an abusive client, a downstream provider from your own retries, a tenant from its neighbours. The four classic algorithms differ in how they treat bursts: a **token bucket** allows a burst up to its capacity and then a steady rate, a **leaky bucket** smooths output to a constant rate, a **fixed window** counter is cheap but lets 2× through at the boundary, and a **sliding window** (log or weighted counter) is accurate without the boundary problem. With several API pods the counters live in Redis and are updated atomically with a Lua script; the client gets `429` and `Retry-After`. The full Redis sliding-window design is the [[design-rate-limiter]] case study.

## Key points

### Where limits live
| Place | Limits what | Typical key |
|---|---|---|
| Edge / API gateway | abuse, DDoS, per-API-key quotas | client IP, API key, user id ([[api-gateway-and-service-discovery]], [[cdn]]) |
| Service | expensive endpoints, per-tenant fairness | tenant id + endpoint ([[multi-tenancy-strategies]]) |
| Outbound client | calls to a provider with a contractual limit (SMS gateway 100 msg/s) | provider name |
| Consumer / worker | drain rate from a queue so the DB is not flooded | worker group ([[backpressure-and-flow-control]]) |

A limiter is a **policy**: it rejects early and cheaply. It is not a [[circuit-breaker]] (which reacts to failures) and not load shedding (which rejects by current capacity), though all three appear in the same resilience layer.

### Algorithms
| Algorithm | State per key | Burst | Accuracy | Notes |
|---|---|---|---|---|
| Token bucket | tokens, last refill time (2 numbers) | up to capacity, then `rate` | exact | industry default (AWS, Envoy, Guava `RateLimiter`, Bucket4j); refill lazily on each request |
| Leaky bucket (as meter) | same as token bucket | none: constant output | exact | same math viewed from the other side; "as queue" variant delays requests instead of rejecting |
| Fixed window | one counter per window | 2× at the boundary | low | `INCR key:minute` + `EXPIRE`; cheapest |
| Sliding window log | sorted set of timestamps | none | exact | memory ∝ limit; `ZADD`/`ZREMRANGEBYSCORE`/`ZCARD` |
| Sliding window counter | two counters (current, previous window) | small | ~1 % error | `prev × (1 − elapsed/window) + current`; the practical compromise |

- Token bucket math: `tokens = min(capacity, tokens + (now − last) × rate)`; allow if `tokens ≥ cost`, then `tokens −= cost`. Capacity is the burst, rate is the sustained limit; `cost` lets expensive calls consume more.
- A limit is a pair "N per window" plus a decision on what to do with the excess: reject (`429`), delay (queue), or degrade (serve a cached response).

### Distributed limits
- Per-pod in-memory limits multiply by the pod count and reset on deploy; fine for a coarse safety net, wrong for a contractual limit.
- Shared state in Redis: one round trip per decision (~0.5 ms), atomic via a Lua script or `MULTI`, keys with TTL so idle clients cost nothing ([[redis-data-structures]]). Redis Cluster shards keys, so one hot key still lands on one node; at 100 K decisions/s per key, move to local token buckets replenished from a central quota ("token leasing": each pod takes a lease of 100 tokens/s).
- Clock: use Redis `TIME` inside the script, not each pod's clock, so pods with skewed clocks agree ([[time-and-clocks]]).
- Fail-open or fail-closed when Redis is down: public API edge usually fails open with a local fallback limit; a provider quota fails closed (better to delay SMS than to get the account suspended).

### Responses and headers
- `429 Too Many Requests` with `Retry-After: <seconds>` and informative headers (`RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`, the IETF draft names) so clients can back off intelligently.
- Clients: exponential backoff with jitter, honour `Retry-After`, never retry a `429` immediately ([[failure-modes-and-timeouts]]).
- Log and meter rejections per key: a spike in `429` for one tenant is either abuse or a broken client, both worth an alert ([[slo-sli-and-alerting]]).

### Choosing
- Public API per client: token bucket (burst-friendly) in Redis, limits by plan.
- Per-user protection ("max 5 SMS per hour", OTP brute force): sliding window counter, strict.
- Outbound provider limit: token bucket per provider, shared across workers, fail closed, with the queue absorbing what the bucket rejects ([[message-queues-and-streams]]).
- Fairness among tenants: weighted fair queueing or per-tenant buckets plus a global one.

## Example
A token bucket shared across pods, stored in a Redis hash and updated atomically with a Lua script; used both for the public API filter and as an outbound guard before calling the SMS provider.

```java
@Component
public class RedisTokenBucket {
    // KEYS[1] = bucket key; ARGV = capacity, refillPerSec, cost, ttlSec. Uses Redis TIME, not the pod clock.
    private static final String LUA = """
        local capacity = tonumber(ARGV[1]); local rate = tonumber(ARGV[2])
        local cost = tonumber(ARGV[3]);     local ttl  = tonumber(ARGV[4])
        local t = redis.call('TIME'); local now = t[1] + t[2] / 1000000
        local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
        local tokens = tonumber(b[1]); local ts = tonumber(b[2])
        if tokens == nil then tokens = capacity; ts = now end
        tokens = math.min(capacity, tokens + (now - ts) * rate)
        local allowed = 0; local retryAfter = 0
        if tokens >= cost then tokens = tokens - cost; allowed = 1
        else retryAfter = math.ceil((cost - tokens) / rate) end
        redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
        redis.call('EXPIRE', KEYS[1], ttl)
        return { allowed, retryAfter, math.floor(tokens) }
        """;
    private final RedisScript<List> script = RedisScript.of(LUA, List.class);
    private final StringRedisTemplate redis;

    public RedisTokenBucket(StringRedisTemplate redis) { this.redis = redis; }

    public record Decision(boolean allowed, long retryAfterSec, long remaining) {}

    @SuppressWarnings("unchecked")
    public Decision tryConsume(String key, long capacity, double refillPerSec, long cost) {
        long ttl = (long) Math.ceil(capacity / refillPerSec) + 1;            // idle bucket is full again after ttl
        List<Long> r = redis.execute(script, List.of(key),
                String.valueOf(capacity), String.valueOf(refillPerSec), String.valueOf(cost), String.valueOf(ttl));
        return new Decision(r.get(0) == 1, r.get(1), r.get(2));
    }
}

@Component
@Order(1)
public class ApiRateLimitFilter extends OncePerRequestFilter {
    private final RedisTokenBucket bucket;
    public ApiRateLimitFilter(RedisTokenBucket bucket) { this.bucket = bucket; }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        String client = Optional.ofNullable(req.getHeader("X-Api-Key")).orElse(req.getRemoteAddr());
        RedisTokenBucket.Decision d;
        try {
            d = bucket.tryConsume("rl:api:" + client, 100, 50.0, 1);          // burst 100, sustained 50 rps
        } catch (DataAccessException redisDown) {
            d = new RedisTokenBucket.Decision(true, 0, -1);                     // fail open at the public edge
        }
        res.setHeader("RateLimit-Limit", "50");
        res.setHeader("RateLimit-Remaining", String.valueOf(Math.max(d.remaining(), 0)));
        if (!d.allowed()) {
            res.setHeader("Retry-After", String.valueOf(d.retryAfterSec()));
            res.sendError(HttpStatus.TOO_MANY_REQUESTS.value());
            return;
        }
        chain.doFilter(req, res);
    }
}

// outbound guard in the SMS worker: fail closed, the Kafka consumer pauses instead of calling the provider
// var d = bucket.tryConsume("rl:provider:sms-gw", 100, 100.0, 1); if (!d.allowed()) pauseFor(d.retryAfterSec());
```

One Redis round trip per request, two numbers of state per client, and all pods agree on the count.

## Pitfalls
- Fixed window at the minute boundary: 100 requests at 12:00:59 and 100 at 12:01:00 are "within limit" but 200 in one second hit the backend.
- Limiting by client IP only: a mobile carrier NAT puts 10 000 users behind one IP; prefer API key or user id once authenticated, IP only for anonymous traffic.
- Non-atomic `GET` then `SET` in Redis: two pods read 99, both allow, counter ends at 100 while 101 went through. Use Lua or `INCR`.
- Retrying a `429` immediately from the client or from the proxy: the limiter now spends its capacity on rejections. Honour `Retry-After` with jitter.
- Limiter as the only protection for a provider quota: the limiter rejects, the worker retries, the queue grows unbounded. Pair with queue depth alerts and priority shedding ([[back-of-envelope-estimation]] sizes the backlog).
- Related: [[design-rate-limiter]] for the full Redis sliding-window design; [[circuit-breaker]] for the failure-driven sibling.
