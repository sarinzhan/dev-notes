---
title: Consistent hashing, the ring and virtual nodes
tags: [distributed-systems, partitioning, hashing, kafka]
learned: 2026-10-27
---
## TL;DR
`hash(key) mod N` is a terrible way to assign keys to N nodes because changing N remaps almost every key. **Consistent hashing** places both nodes and keys on a ring of hash values (0 to 2^32−1); a key belongs to the first node clockwise from it. Adding or removing a node moves only the keys in one arc, about 1/N of them. **Virtual nodes** (many ring positions per physical node) fix the uneven arcs that random placement produces and let a big machine own more of the ring. Used by Cassandra, DynamoDB, Riak, Memcached clients, Envoy/nginx load balancing and CDNs.

## Key points

### The problem with modulo
- 4 cache nodes, `node = hash(key) % 4`. Add a fifth: `hash % 5` differs from `hash % 4` for roughly 80% of keys. Every one of those keys is now a cache miss on the new node and stale on the old one; the database behind the cache gets the full load at once ([[distributed-cache-coherence]]).
- The same applies to sharded databases: resharding by modulo means moving most of the data ([[sharding-strategies]]).

### The ring
- Hash node ids (`"cache-1"`, `"cache-2"`) and keys with the same function into a circular space. Each key is owned by the nearest node clockwise (its **successor**).
- Add node X between A and B: only keys in the arc (A, X] move from B to X. Remove node B: its arc goes to its successor. Expected movement per change: K/N keys.
- Replication falls out naturally: store a key on its successor and the next R−1 distinct physical nodes clockwise (Dynamo's **preference list**).

### Virtual nodes (vnodes)
- With one position per node, random placement gives arcs of very different sizes: one node may own 3× the average. Solution: each physical node gets V positions (`"cache-1#0"` … `"cache-1#149"`). With V ≈ 100–200 the load per node is within a few percent of the mean.
- Heterogeneous hardware: give a node with twice the RAM twice the vnodes.
- Cassandra's default is 16 vnodes per node (was 256); DynamoDB and Riak use fixed partitions assigned to nodes, which is the same idea with the ring pre-cut into equal pieces.
- Cost: the successor lookup is a sorted-map search, O(log(N·V)); the ring metadata grows with V; rebuilding a node's data on failure streams from more peers (which is actually good for speed).

### Variants worth naming in an interview
- **Rendezvous (HRW) hashing**: for each key, compute `hash(key, node)` for every node and pick the max. No ring, perfectly even, O(N) per lookup; fine for small N like a handful of cache servers or a load balancer.
- **Jump consistent hash** (Google): O(1) memory, maps keys to bucket numbers 0..N−1 with minimal movement; buckets must be removed from the end only, so it suits numbered shards, not arbitrary node loss.
- **Maglev** (Google's load balancer): a lookup table built so that backend changes disturb few entries, with O(1) lookup.

### Kafka's analogy (and the difference)
- Kafka does *not* consistent-hash: the producer computes `murmur2(key) % numPartitions`. That is why **changing the partition count breaks key→partition stability**: records for a key start landing on a different partition and per-key ordering is lost across the boundary ([[kafka-producer]], [[kafka-architecture]]).
- The fixed partition count acts like the pre-cut ring: partitions are the units that get moved between brokers (reassignment) and between consumers (rebalance); the key→partition mapping never changes. The consumer-group **sticky/cooperative** assignor is the "move as few as possible" property applied to partitions instead of keys ([[kafka-consumer]]).

### Where you meet it
- Client-side cache sharding: Memcached (Ketama), Redis Cluster uses 16384 hash slots assigned to nodes (pre-cut ring) with `CRC16(key) % 16384` ([[redis-patterns-and-persistence]]).
- Load balancers with session affinity: nginx `hash $cookie_sid consistent`, Envoy ring hash and Maglev, so that removing one backend only moves its own sessions ([[load-balancing]]).
- Databases: Cassandra token ring, DynamoDB, CockroachDB ranges (range-based, but the rebalancing idea is the same), ScyllaDB.
- Request coalescing and rate limiting: route all requests for one key to the same node so its local state is authoritative ([[rate-limiting]]).

## Example
A compact ring with virtual nodes and MD5-derived 32-bit positions, as a client-side router over cache or shard nodes.
```java
public final class HashRing<N> {
    private final TreeMap<Integer, N> ring = new TreeMap<>();
    private final int vnodes;

    public HashRing(Collection<N> nodes, int vnodes) {
        this.vnodes = vnodes;
        nodes.forEach(this::add);
    }

    public void add(N node) {
        for (int i = 0; i < vnodes; i++) ring.put(hash(node + "#" + i), node);
    }

    public void remove(N node) {
        for (int i = 0; i < vnodes; i++) ring.remove(hash(node + "#" + i));
    }

    /** Owner of the key: first ring position clockwise, wrapping around. */
    public N nodeFor(String key) {
        if (ring.isEmpty()) throw new IllegalStateException("no nodes");
        Map.Entry<Integer, N> e = ring.ceilingEntry(hash(key));
        return (e != null ? e : ring.firstEntry()).getValue();
    }

    /** The key's owner plus the next (replicas-1) distinct physical nodes clockwise. */
    public List<N> preferenceList(String key, int replicas) {
        List<N> result = new ArrayList<>(replicas);
        for (N n : ring.tailMap(hash(key), true).values()) if (!result.contains(n) && result.size() < replicas) result.add(n);
        for (N n : ring.values()) if (result.size() < replicas && !result.contains(n)) result.add(n);
        return result;
    }

    private static int hash(String s) {
        byte[] d = md5().digest(s.getBytes(StandardCharsets.UTF_8));   // MD5/murmur3: uniform, fast enough
        return (d[0] & 0xff) << 24 | (d[1] & 0xff) << 16 | (d[2] & 0xff) << 8 | (d[3] & 0xff);
    }
    private static MessageDigest md5() {
        try { return MessageDigest.getInstance("MD5"); } catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }
}
```
Adding a fourth node to a ring of three with 150 vnodes each moves about 25% of keys, each from exactly one old owner, instead of ~75% with modulo.

## Pitfalls
- Using `String.hashCode()` or `Objects.hash` for the ring: poor distribution and not stable across JVMs/languages; use murmur3 or MD5/SHA truncated.
- Too few vnodes: one node ends up with a far larger arc and becomes the hot spot you were trying to avoid.
- Hashing on a key with skew: consistent hashing spreads *keys*, not *load*; one hot key still lands on one node ([[sharding-strategies]]).
- Treating Kafka partition count as freely changeable because "it's just hashing": it is modulo hashing, and existing keys move.
- Related: [[hash-tables]] for hashing basics, [[caching]] for the cache-cluster use case, [[load-balancing]] for ring-hash affinity.
