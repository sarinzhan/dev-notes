---
title: Design a distributed rate limiter
tags: [system-design, case-study, rate-limiting, redis]
learned: 2026-10-27
---
## TL;DR
A rate limiter decides, for each request, whether a client has exceeded its quota (for example 100 requests per minute per API key) and answers in well under a millisecond, consistently across all service replicas. The design is a shared counter store (Redis) with an atomic check-and-increment (Lua script), an algorithm chosen for the burst behaviour you want, and a middleware layer at the gateway or in each service that returns `429` with `Retry-After`. The hard part is doing this atomically, cheaply in memory, and deciding what happens when the counter store is unavailable. Algorithms themselves (token bucket, leaky bucket, fixed/sliding window) are described in [[rate-limiting]]; this note is the system around them.

## Key points

### Requirements and estimates
- Functional: limit by API key, user id or IP; several rules per route (per second and per day); return remaining quota in headers; rules editable without redeploy.
- Non-functional: decision latency < 1 ms p99 added to the request; correct across N replicas (no per-pod limits); fail-open or fail-closed policy stated; memory bounded by number of active clients.
- Numbers: 10 000 req/s across the fleet, 200 k distinct clients per hour. Sliding window log keeps one entry per request: 100 req/min × 200 k clients ≈ 20 M entries, too much; sliding window counter keeps two integers per client-window ≈ a few MB. Redis handles 100 k+ simple ops/s on one node, so one instance suffices; a cluster for availability.

### API
```
# applied as middleware, not called by clients; responses carry:
HTTP 429 Too Many Requests
Retry-After: 12
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1730000012

# control plane
PUT /v1/limits/{ruleId}   { key: "api_key", route: "/v1/sms/**", limit: 100, window: "1m", burst: 20 }
```

### Data model
- Redis: one key per `(rule, client, window)`: `rl:sms:key123:1730000000` → counter, with TTL = 2 × window. Token bucket stores `(tokens, lastRefillMs)` in a hash.
- Rules in Postgres, pushed to each service via a config refresh or pub/sub; cached in memory and reloaded every 30 s ([[caching]]).
- Memory: keys expire by TTL, so storage is proportional to active clients, not total clients ([[redis-data-structures]]).

### High-level design
```
client ──▶ API gateway / each service pod
               │ middleware: identify client (API key, JWT sub, IP)
               │ match rules for route
               ▼
         Redis (Lua: check + increment atomically) ──▶ allow / deny + remaining
               │
         rules cache ◀── control plane (Postgres) ── admin API
```
- Where it runs: at the [[api-gateway-and-service-discovery|gateway]] for coarse global limits (cheap, one place), inside services for business limits ("max 5 SMS/hour per subscriber") that need domain context ([[load-balancing]] does not help here; every replica must see the same count).

### Deep dive: atomic sliding window counter in Redis
- Fixed window (`INCR` + `EXPIRE`) allows 2× the limit at the window boundary. Sliding window log (`ZADD` + `ZREMRANGEBYSCORE` + `ZCARD`) is exact but O(limit) memory per client. **Sliding window counter** approximates: `count = current_window + previous_window × (1 − elapsed_fraction)`; two integers, error within a few percent, the usual production choice.
- Atomicity: a `GET`/compare/`INCR` from the app races between replicas; a Lua script runs on the server as one unit. `MULTI/EXEC` is not enough because the decision depends on the read.
- Clock: use Redis `TIME` inside the script or pass the app's time; mixed clocks across pods shift windows ([[time-and-clocks]]).
- Token bucket in Lua: read `tokens` and `ts`, add `rate × (now − ts)` capped at `burst`, if `tokens ≥ 1` subtract and allow, store both; one round trip, allows bursts up to `burst` then steady `rate`.
- Failure policy: Redis unreachable → **fail-open** for ordinary APIs (availability over strictness, log and alert), **fail-closed** for expensive or abuse-prone endpoints (SMS, OTP, login). Add a tiny in-memory fallback limiter per pod so fail-open is not unlimited.
- Hot clients: one key per client, so one client's traffic lands on one Redis shard; fine, because a single client cannot exceed one node's capacity anyway. For global fleet-wide limits, shard the counter (`key:{0..9}`) and sum, or accept slight overshoot.

### Trade-offs
| Choice | Pick when | Cost |
|---|---|---|
| Token bucket | APIs where short bursts are fine (mobile app wakes up) | two fields per key, slightly more script logic |
| Sliding window counter | strict "N per minute" SLAs with providers | approximate at the boundary |
| Sliding window log | exact audit of every request (billing) | memory O(limit) per client |
| Local in-memory only | single instance, or per-pod protective limits | not shared, per-pod multiples |
| Gateway vs service | global protection vs business rules | gateway cannot see domain keys |

## Example
Sliding window counter as a Lua script, called with `EVALSHA` from Spring Data Redis:

```lua
-- KEYS[1] = prefix (rl:sms:key123), ARGV[1] = limit, ARGV[2] = window_ms, ARGV[3] = now_ms
local limit, win, now = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local cur_win  = math.floor(now / win)
local cur_key  = KEYS[1] .. ':' .. cur_win
local prev_key = KEYS[1] .. ':' .. (cur_win - 1)
local cur  = tonumber(redis.call('GET', cur_key) or '0')
local prev = tonumber(redis.call('GET', prev_key) or '0')
local elapsed = (now % win) / win
local estimate = prev * (1 - elapsed) + cur
if estimate + 1 > limit then
  local retry_ms = math.floor(win - (now % win))
  return {0, 0, retry_ms}                      -- denied, remaining, retry after
end
redis.call('INCR', cur_key)
redis.call('PEXPIRE', cur_key, win * 2)
return {1, math.floor(limit - estimate - 1), 0}
```

```java
Long[] r = (Long[]) redis.execute(script, List.of("rl:sms:" + apiKey), "100", "60000", String.valueOf(clock.millis()));
if (r[0] == 0) throw new TooManyRequestsException(Duration.ofMillis(r[2]));
response.setHeader("X-RateLimit-Remaining", r[1].toString());
```

## Pitfalls
- Interviewers probe: "two replicas check at the same time — can both pass?" Not with a server-side script; yes with `GET` then `INCR` from the app.
- "Redis is down" — state the policy and the local fallback; "unlimited" and "everything 429" are both wrong answers.
- "Where does the 2× burst at window edges come from?" Fixed windows; show the sliding counter formula.
- Rate limiting by IP behind a proxy limits the proxy; trust `X-Forwarded-For` only from known hops ([[reverse-proxy-nginx-basics]]).
- Related: [[redis-patterns-and-persistence]], [[api-security-checklist]], [[backpressure-and-flow-control]] for the server-side counterpart.
