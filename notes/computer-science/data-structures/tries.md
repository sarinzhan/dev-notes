---
title: Tries (prefix trees)
tags: [data-structures, trie, prefix-search, autocomplete, strings]
learned: 2026-10-27
---
## TL;DR
A trie stores strings as paths from the root, one character per edge, so all keys that share a prefix share the same path. Lookup, insert and "does any key start with X" cost O(L) in the key length and do not depend on how many keys there are. That makes it the structure for autocomplete, longest-prefix matching (IP routing, phone-number prefixes, MSISDN ranges) and spell checking. The price is memory: a naive node with a 26- or 256-slot child array is huge, so real implementations use hash-map children, compressed edges (radix tree) or bit-level tricks.

## Key points

### Structure
- Root is the empty string. Each node has children keyed by a character and a flag `terminal` (a key ends here); optionally a value, a count of keys below it, or a payload (route, tariff, suggestion weight).
- A key is **not stored anywhere** as a string; it is reconstructed by the path. Keys with a common prefix share nodes, which is the memory win for large dictionaries of similar strings and the memory loss for few, long, dissimilar strings.

### Operations and complexity (L = key length, A = alphabet size)
| Operation | Trie | Hash map | Sorted array / `TreeMap` |
|---|---|---|---|
| Insert / exact lookup | O(L) | O(L) average (hash the whole key) | O(L log n) |
| `startsWith(prefix)` | O(L) | O(n·L) scan | O(L log n) to find the start, then scan |
| All keys with a prefix | O(L + output) | O(n·L) | O(L log n + output) |
| Longest matching prefix of a text | O(L) | O(L²) tries of each prefix | O(L log n) per candidate |
| Ordered iteration | O(total chars), lexicographic for free | not ordered | yes |
| Memory | O(total chars) nodes, each with child storage | O(n) entries + key strings | O(n) + key strings |

### Child storage trade-offs
| Children as | Lookup per char | Memory per node | When |
|---|---|---|---|
| Fixed array `Node[26]` / `Node[256]` | O(1) | 26–256 references (200 B–2 KB) | small alphabet, dense keys, contest code |
| `HashMap<Character, Node>` | O(1) average | map overhead per node | Unicode, sparse |
| Sorted small array + binary search | O(log A) | tight | memory-sensitive |
| **Radix / Patricia tree**: merge single-child chains into one edge holding a substring | O(L) overall | far fewer nodes | Linux routing tables, Go's HTTP routers, Redis streams/RAX, Spring's path pattern matching conceptually |

### Where tries appear in systems
- **Autocomplete / typeahead**: walk to the prefix node, then DFS or keep the top-k suggestions precomputed per node (a heap per node, [[heaps-and-priority-queues]]). At scale the trie is sharded by first letters and served from memory ([[design-news-feed]] style fan-out does not apply; this is read-mostly).
- **Longest-prefix matching**: IP routing tables (binary trie over address bits, [[ip-addressing-and-nat]]), telecom number plans (which operator/tariff owns `+99655…`), URL routers that match `/api/v1/sims/{id}`.
- **Full-text search building blocks**: a dictionary of terms with prefix queries; inverted indexes use an FST (finite state transducer), a compressed trie, in Lucene/Elasticsearch ([[elasticsearch-basics]]).
- **Compressed in-memory indexes**: adaptive radix trees in databases, `btree_gin`-like prefix operators in Postgres rely on B-trees instead ([[postgres-indexes]]); a trie is purely in-memory in practice.
- **String algorithms**: Aho-Corasick (trie + failure links) finds many patterns in one pass; suffix tries/trees answer substring queries ([[string-algorithms]]).

### Trie vs hash map for a dictionary
- Exact membership only: hash map, it is simpler and often faster because hashing is one pass and one bucket read ([[hash-tables]]).
- Prefix, ordered or longest-match queries: trie.
- Approximate membership with tiny memory: Bloom filter ([[bloom-filters-and-probabilistic]]).

## Example
A trie over lowercase ASCII with prefix search and a longest-prefix lookup, the shape of a number-plan or route table.

```java
public final class Trie {
    private static final class Node {
        final Node[] next = new Node[26];
        boolean terminal;
        String value;                                 // payload for terminal nodes (e.g. operator code)
    }

    private final Node root = new Node();

    public void put(String key, String value) {
        Node n = root;
        for (char c : key.toCharArray()) {
            int i = c - 'a';
            if (n.next[i] == null) n.next[i] = new Node();
            n = n.next[i];
        }
        n.terminal = true;
        n.value = value;
    }

    public boolean contains(String key) {
        Node n = find(key);
        return n != null && n.terminal;
    }

    public boolean startsWith(String prefix) { return find(prefix) != null; }

    /** Value of the longest key that is a prefix of text, or null (longest-prefix match). */
    public String longestPrefixValue(String text) {
        Node n = root; String best = null;
        for (char c : text.toCharArray()) {
            n = n.next[c - 'a'];
            if (n == null) break;
            if (n.terminal) best = n.value;
        }
        return best;
    }

    public List<String> keysWithPrefix(String prefix, int limit) {
        List<String> out = new ArrayList<>();
        Node n = find(prefix);
        if (n != null) collect(n, new StringBuilder(prefix), out, limit);
        return out;
    }

    private Node find(String s) {
        Node n = root;
        for (char c : s.toCharArray()) {
            n = n.next[c - 'a'];
            if (n == null) return null;
        }
        return n;
    }

    private void collect(Node n, StringBuilder path, List<String> out, int limit) {
        if (out.size() >= limit) return;
        if (n.terminal) out.add(path.toString());
        for (int i = 0; i < 26 && out.size() < limit; i++) {
            if (n.next[i] != null) {
                path.append((char) ('a' + i));
                collect(n.next[i], path, out, limit);
                path.setLength(path.length() - 1);
            }
        }
    }
}
```

## Pitfalls
- Memory blow-up: a `Node[256]` per character for a million-word dictionary is gigabytes. Use a map or a radix tree, and measure before choosing a trie over a sorted array plus binary search ([[binary-search-and-variants]]).
- Forgetting the `terminal` flag: `startsWith("app")` is true after inserting `"apple"`, but `contains("app")` must be false.
- Deleting requires pruning nodes with no children and no terminal flag on the way back up, otherwise the trie leaks dead branches.
- Case, Unicode and normalization: lowercase and normalize keys before insertion and lookup, or `"É"` and `"é"` become different paths ([[text-encodings-and-unicode]]).
- Related: [[data-structures]] for the overview, [[string-algorithms]] for Aho-Corasick and suffix structures, [[hash-tables]] for when exact lookup is all you need.
