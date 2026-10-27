---
title: Design a news feed
tags: [system-design, case-study, fan-out, caching, ranking]
learned: 2026-10-27
---
## TL;DR
A news feed shows each user a list of recent posts from the people they follow, ordered by time or by a ranking score, within a few hundred milliseconds. The central decision is **fan-out on write** (when a post is published, push its id into every follower's precomputed feed in a cache) versus **fan-out on read** (when a user opens the feed, pull recent posts from everyone they follow and merge). Real systems do both: push for ordinary users, pull for celebrities with millions of followers, and merge at read time. Everything else is [[caching]], a [[cdn]] for media, and an async pipeline ([[message-queues-and-streams]]) that does the fan-out work outside the request.

## Key points

### Requirements and estimates
- Functional: publish a post (text, media), follow/unfollow, read the feed with infinite scroll, like/comment counts.
- Non-functional: feed load < 200 ms p99, posts appear within seconds for most followers, feed must be available even if ranking is down (fall back to chronological).
- Numbers: 10 M DAU, each opens the feed 10 times/day → 100 M feed reads/day ≈ 1 200/s, peak 5 000/s. 1 M posts/day ≈ 12/s; average 200 followers → 200 M feed insertions/day ≈ 2 300/s of cache writes on average. A celebrity with 5 M followers would cost 5 M writes for one post, which is why hybrid fan-out exists. Feed cache: 10 M users × 500 post ids × 16 B ≈ 80 GB in Redis, sharded.

### API
```
POST /v1/posts                        { text, mediaIds }        -> 201 { postId }
GET  /v1/feed?cursor=<opaque>&limit=20 -> { items: [ {postId, author, text, media, createdAt, score} ], nextCursor }
POST /v1/users/{id}/follow
GET  /v1/users/{id}/posts?cursor=     (profile timeline, separate from the home feed)
```
- Cursor pagination by `(score, postId)` or `(createdAt, postId)`; offset pagination breaks when new posts arrive ([[api-design-for-system-design]]).

### Data model
- `post(id, author_id, text, media jsonb, created_at)` — Postgres, partitioned by month; `(author_id, created_at desc)` index for profile timelines.
- `follow(follower_id, followee_id)` — graph edges; both directions needed ("who do I follow" for pull, "who follows me" for push). Store twice or in a graph-friendly store ([[graphs-representation]]).
- `feed:{userId}` — Redis sorted set of `postId` scored by time (or ranking score), trimmed to the last 500–1 000 entries ([[redis-data-structures]]).
- `post_cache:{postId}` — Redis hash with the rendered post, TTL hours, so the feed read never touches Postgres for hot posts.
- Counters (likes, comments) in Redis with periodic flush to Postgres.

### High-level design
```
POST /posts ──▶ post service ──tx: post + outbox──▶ Postgres ──relay──▶ Kafka post.created
                                                                           │
                                               fan-out workers: followers = get(author)
                                                 if followers < 10k: ZADD feed:{f} for each (push)
                                                 else: mark author as "pull source"; skip push
GET /feed ──▶ feed service ──▶ ZREVRANGE feed:{me} (pushed ids)
                           ├─▶ for each celebrity I follow: recent post ids (cached per celebrity)
                           ├─▶ merge, rank, dedupe, cut to 20
                           └─▶ hydrate posts from post cache (MGET) → fallback Postgres → CDN URLs for media
```

### Deep dive: fan-out strategy
| | Fan-out on write (push) | Fan-out on read (pull) | Hybrid |
|---|---|---|---|
| Publish cost | O(followers) cache writes | O(1) | O(followers) for normal users only |
| Read cost | O(1): read one sorted set | O(followees) queries + merge | one set read + k pulls for celebrities |
| Latency to appear | seconds (async) | immediate | mixed |
| Waste | feeds built for users who never log in | recomputed every open | bounded |
| Celebrity problem | 5 M writes per post, minutes of lag | none | solved |

- Push pipeline: `post.created` consumer loads follower ids in pages (`SELECT follower_id ... WHERE followee_id = ? AND follower_id > ? LIMIT 10000`), pipelines `ZADD` + `ZREMRANGEBYRANK` (trim) to Redis, and is idempotent because `ZADD` of the same member is a no-op ([[idempotency]], [[async-processing-and-event-driven-scaling]]).
- Inactive users: skip push for users not seen in 30 days; rebuild their feed on next login by pull. Cuts fan-out volume by half or more.
- Unfollow and delete: lazy; filter on read (`author not in blocked`, post exists in cache) instead of scanning millions of sets.
- Ranking: chronological is a score of `createdAt`; a ranked feed stores a model score computed at fan-out time and re-ranks the top 100 candidates at read time with fresh signals. Keep the chronological path as a fallback when the ranker is slow ([[circuit-breaker]]).
- Consistency: the author's own feed and profile timeline read the primary so "I posted, where is it?" never happens; everyone else's feed is eventually consistent ([[read-write-splitting-and-cqrs-lite]], [[consistency-models]]).

### Trade-offs
- Redis sorted sets vs a precomputed `feed_item` table in Postgres: Redis for the hot window, Postgres (or Cassandra) when feeds must survive cache loss without a rebuild ([[storage-choices]]).
- Celebrity threshold (10 k, 100 k): lower threshold means more pull work at read, higher means more fan-out lag; measure both.
- Media through a [[cdn]] with signed URLs; the feed response carries URLs, never bytes.

## Example
Hybrid read path: merge the pushed feed with pulled celebrity posts and hydrate from the post cache.

```java
public FeedPage load(long userId, Cursor cursor) {
    // 1. pushed ids (most feeds end here)
    Set<ZSetOperations.TypedTuple<String>> pushed = redis.opsForZSet()
        .reverseRangeByScoreWithScores("feed:" + userId, 0, cursor.maxScore(), 0, 100);

    // 2. pull sources: celebrities this user follows (small list, cached)
    List<Long> celebs = followCache.celebritiesFollowedBy(userId);
    Stream<Candidate> pulled = celebs.stream()
        .flatMap(c -> recentPostsCache.of(c, cursor.maxScore(), 20).stream());

    // 3. merge, dedupe, rank, cut
    List<Candidate> top = Stream.concat(pushed.stream().map(Candidate::fromTuple), pulled)
        .distinct()
        .sorted(comparing(Candidate::score).reversed().thenComparing(Candidate::postId))
        .limit(20).toList();

    // 4. hydrate: one MGET, misses go to Postgres and backfill the cache
    Map<Long, PostView> posts = postCache.getAll(top.stream().map(Candidate::postId).toList());
    return FeedPage.of(top, posts, Cursor.after(top.getLast()));
}
```

## Pitfalls
- Interviewers probe: "a user with 20 M followers posts — what happens?" Pull for that author, push for the rest; "how long until my followers see my post?" seconds, bounded by fan-out lag, with a lag metric and alert.
- "Redis loses a shard" — feeds rebuild on demand by pull from the follow graph; the post of record is in Postgres; rebuild cost spikes must be throttled ([[distributed-cache-coherence]]).
- Fan-out inside the POST request instead of a consumer: the publish latency equals the follower count.
- Offset pagination plus a live feed: duplicates and gaps while scrolling.
- Related: [[design-chat-service]] for the small-group fan-out counterpart, [[database-scaling-path]] for the follow table at scale, [[system-design-basics]].
