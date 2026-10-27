---
title: Bloom filters and probabilistic data structures
tags: [data-structures, bloom-filter, hyperloglog, count-min-sketch, probabilistic]
learned: 2026-10-27
---
## TL;DR
Probabilistic data structures trade exactness for a huge memory saving. A **Bloom filter** answers "have I seen this key?" with no false negatives and a tunable false-positive rate using a few bits per element. **HyperLogLog** counts distinct elements (cardinality) within ~1% using 12 KB regardless of n. **Count-min sketch** estimates how often a key appeared, never undercounting. They appear wherever exact sets or counters would not fit: database storage engines, caches, Redis, Kafka, analytics and dedup pipelines.

## Key points

### Bloom filter
- A bit array of m bits and k hash functions. **Add**: set bits `h1(x) … hk(x)`. **Query**: all k bits set means "probably present"; any bit clear means "definitely absent".
- False positives come from other keys having set the same bits. No deletes (clearing a bit could remove another key's evidence); a **counting Bloom filter** uses small counters instead of bits to allow deletes; a **cuckoo filter** supports deletes and is more space-efficient at low error rates.
- Sizing: for n elements and target false-positive rate p, `m ≈ −n·ln p / (ln 2)²` bits and `k ≈ (m/n)·ln 2`. Example: 10M keys at 1% needs ~12 MB and 7 hashes; a `HashSet<String>` of the same keys would need hundreds of MB.
- Hashing: use one good 64-bit hash (Murmur3, xxHash) and derive k hashes as `h1 + i·h2` (Kirsch–Mitzenmacher) rather than k independent functions.

| Operation | Complexity | Note |
|---|---|---|
| add / query | O(k) | k is small (5–10), independent of n |
| memory | ~1.44·log₂(1/p) bits per element | 1% → ~9.6 bits per element |
| union of two filters | OR the bit arrays | same m and hashes |
| delete | not supported | counting/cuckoo variant |

### Where Bloom filters are used
- **LSM storage engines** (RocksDB, Cassandra, HBase, Kafka Streams state stores): one filter per SSTable so a point lookup skips files that cannot contain the key ([[column-and-wide-column-stores]]).
- **Postgres**: BRIN bloom opclass and the `bloom` extension index for many-column equality predicates ([[postgres-indexes]]).
- **Caches and CDNs**: "one-hit wonders" are not cached until a filter says the URL was seen before ([[caching]], [[cdn]]).
- **Dedup at the edge**: has this event id / notification id already been processed? A filter in front of the exact store (Redis/DB) turns most "no" answers into zero round trips ([[idempotency]], [[design-notification-service]]).
- **Distributed joins**: ship a filter of the join keys to the other side instead of the full key set; also spell checkers and malicious-URL lists in browsers.

### HyperLogLog (cardinality)
- Hash each element; the number of leading zeros in the hash is a hint of how many distinct values you saw (seeing a hash with 20 leading zeros suggests ~2²⁰ distinct items). Keep the max per 2^p registers and combine with a harmonic mean.
- Standard error ≈ 1.04/√(2^p): p = 14 gives 16384 registers, ~0.8% error, 12 KB. Counts billions.
- Mergeable (take max per register), so per-day sketches combine into per-month without recounting.
- Redis `PFADD`/`PFCOUNT`/`PFMERGE` ([[redis-data-structures]]); Postgres `hll` extension; ClickHouse `uniq`; Elasticsearch `cardinality` aggregation; unique visitors, unique MSISDNs per cell, unique IPs per minute.

### Count-min sketch (frequency)
- A d×w matrix of counters and d hash functions. Add: increment `row[i][hi(x)]` for each row. Query: the minimum over rows. Collisions only inflate counts, so the estimate is ≥ the true count; error ≤ ε·N with probability 1−δ for w = e/ε, d = ln(1/δ).
- Uses: heavy hitters / top-k talkers, rate limiting per key with bounded memory ([[rate-limiting]]), hot-key detection in caches, trending items.

### Comparison
| Structure | Question | Error type | Memory | Deletes | Merge |
|---|---|---|---|---|---|
| Bloom filter | membership | false positives only | ~10 bits/elem at 1% | no (counting/cuckoo: yes) | OR |
| Cuckoo filter | membership | false positives only | ~7–8 bits/elem at 1% | yes | no simple merge |
| HyperLogLog | distinct count | ±~1% | 12 KB fixed | no | max per register |
| Count-min sketch | frequency of a key | overestimate only | KB–MB, fixed | no (with care) | add matrices |
| t-digest / DDSketch | quantiles (p99 latency) | relative error on rank/value | KB | no | yes |

Quantile sketches are what Prometheus histograms and OTel exponential histograms are built for ([[otel-metric-types]]).

## Example
A Bloom filter over a `long[]` bit set with double hashing, plus the sizing formula; the kind of thing used in front of an idempotency table to skip the DB on first-seen ids.

```java
public final class BloomFilter {
    private final long[] bits;
    private final int numBits;
    private final int numHashes;

    public BloomFilter(long expectedInsertions, double falsePositiveRate) {
        double m = -expectedInsertions * Math.log(falsePositiveRate) / (Math.log(2) * Math.log(2));
        this.numBits = (int) Math.max(64, Math.ceil(m));
        this.numHashes = Math.max(1, (int) Math.round(numBits / (double) expectedInsertions * Math.log(2)));
        this.bits = new long[(numBits + 63) / 64];
    }

    public void add(String key) {
        long h = hash64(key);
        int h1 = (int) h, h2 = (int) (h >>> 32);
        for (int i = 0; i < numHashes; i++) {
            int idx = Math.floorMod(h1 + i * h2, numBits);          // Kirsch–Mitzenmacher
            bits[idx >>> 6] |= 1L << (idx & 63);
        }
    }

    /** false = definitely never added; true = probably added. */
    public boolean mightContain(String key) {
        long h = hash64(key);
        int h1 = (int) h, h2 = (int) (h >>> 32);
        for (int i = 0; i < numHashes; i++) {
            int idx = Math.floorMod(h1 + i * h2, numBits);
            if ((bits[idx >>> 6] & (1L << (idx & 63))) == 0) return false;
        }
        return true;
    }

    private static long hash64(String s) {                       // FNV-1a; use Murmur3 in production
        long h = 0xcbf29ce484222325L;
        for (byte b : s.getBytes(StandardCharsets.UTF_8)) { h ^= b; h *= 0x100000001b3L; }
        return h ^ (h >>> 29);
    }
}

// usage: skip the DB for ids that were certainly never processed
if (!seen.mightContain(eventId) || !repo.existsById(eventId)) { process(event); seen.add(eventId); }
```

## Pitfalls
- Using a Bloom filter where a false positive is unacceptable (deciding a message is a duplicate and dropping it). Use it only to short-circuit the negative case; confirm positives against the exact store.
- Overfilling: a filter sized for 1M keys holding 5M gives a false-positive rate near 100%. Either track the count and rebuild, or use a scalable Bloom filter (a chain of growing filters).
- Weak or correlated hash functions (`String.hashCode` plus a shift) cluster bits and inflate the error; use Murmur3/xxHash with two independent seeds.
- Persisting a filter across restarts without versioning the hash function or `m`: a changed parameter makes every old bit meaningless.
- Related: [[hash-tables]] for the exact structure these approximate, [[redis-data-structures]] for ready-made HyperLogLog and Bloom modules, [[caching]] and [[rate-limiting]] for the systems that need them.
