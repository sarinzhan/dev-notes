---
title: String algorithms for interviews and backends
tags: [algorithms, strings, hashing, kmp]
learned: 2026-10-27
---
## TL;DR
Most string questions are array questions in disguise: frequency arrays (`int[26]` or `int[128]`), two pointers and sliding windows ([[two-pointers-and-sliding-window]]), and hash maps. Two dedicated algorithms are worth knowing by idea: **KMP** finds a pattern in O(n + m) using a failure table instead of backing up, and **rolling hash** (Rabin–Karp) turns substring comparison into O(1) integer comparison, which also powers deduplication and chunking in real systems. Java specifics (immutability, the pool, `StringBuilder`) are in [[strings-and-string-pool]].

## Key points

### Frequency tricks
- Anagram check: count characters with `int[26]`, compare arrays. O(n) instead of sorting at O(n log n).
- Group anagrams: key = sorted string or the count array encoded as a string; `HashMap<String, List<String>>`.
- First unique character, most frequent, "can construct from magazine": one counting pass.
- Palindrome: two pointers from the ends; permutation-is-palindrome: at most one odd count.
- Use `char - 'a'` indexing only when the alphabet is guaranteed; for arbitrary Unicode use a `HashMap<Integer, Integer>` over code points ([[text-encodings-and-unicode]]).

### Substring search
| Algorithm | Time | Idea | Use |
|---|---|---|---|
| naive | O(n · m) | compare at every offset | tiny inputs; `String.indexOf` is this with a fast loop |
| KMP | O(n + m) | prefix function `lps[i]` = length of the longest proper prefix of `p[0..i]` that is also a suffix; on mismatch jump to `lps[j-1]` instead of restarting | guaranteed linear, streaming input |
| Rabin–Karp (rolling hash) | O(n + m) average | hash of a window updated in O(1) when sliding; verify on hash match | multiple patterns, plagiarism/dedup, 2D search |
| Z-function | O(n + m) | `z[i]` = longest match of `s[i..]` with the prefix | same niche as KMP, simpler to write for some |
| Boyer–Moore | sub-linear in practice | skip using the last character | `grep`, editors |
| Aho–Corasick | O(n + total pattern length + matches) | trie plus failure links ([[tries]]) | many patterns at once (profanity filters, log scanners) |

- The KMP `lps` table also answers: shortest palindrome by prepending, smallest repeating unit (`n - lps[n-1]` divides `n`), and string rotation checks (`s2` in `s1 + s1`).
- `String.indexOf` in the JDK is a tuned naive search with intrinsics; for one search in application code just call it.

### Rolling hash
- Polynomial hash `h(s) = Σ s[i] · B^(m-1-i) mod M`. Sliding one character: `h' = (h - s[out] · B^(m-1)) · B + s[in]`, all mod M.
- Use a large prime modulus or `long` with natural overflow plus a random base; use **two** moduli if collisions must be negligible, or always verify the match by comparing the actual substring.
- Applications beyond search: longest duplicate substring (binary search on length + rolling hash, [[binary-search-and-variants]]), content-defined chunking in backup and dedup tools (rsync, restic), Bloom filter hashing ([[bloom-filters-and-probabilistic]]).

### Other frequent questions
- Longest common prefix: vertical scan, or sort and compare first and last.
- Longest palindromic substring: expand around each centre, O(n²); Manacher's is O(n) but rarely expected.
- Edit distance, LCS: [[dynamic-programming]].
- Word break, autocomplete, prefix counts: [[tries]].
- Parsing and validation (`isNumber`, balanced tags): state machine or stack ([[stack-and-monotonic-stack]]).
- String compression, run-length encoding, `StringBuilder` with a capacity hint.

### Backend reality check
- Hashing a string is O(length): `HashMap<String, …>` lookups are not free for long keys; Java caches `String.hashCode()` after the first call, so repeated lookups of the same instance are cheap ([[hashmap-internals]]).
- `LIKE '%foo%'` in Postgres is a naive scan unless you use a trigram GIN index, which is an n-gram variant of frequency tricks ([[postgres-indexes]]).
- Full-text search tokenises, normalises and builds an inverted index; `tsvector` and Elasticsearch are the production answer to "find documents containing" ([[postgres-jsonb-and-full-text]], [[elasticsearch-basics]]).

## Example
```java
// KMP: all start indices of `pattern` in `text`, O(n + m).
static List<Integer> kmpSearch(String text, String pattern) {
    int[] lps = prefixFunction(pattern);
    List<Integer> hits = new ArrayList<>();
    for (int i = 0, j = 0; i < text.length(); i++) {
        while (j > 0 && text.charAt(i) != pattern.charAt(j)) j = lps[j - 1];   // fall back, do not restart
        if (text.charAt(i) == pattern.charAt(j)) j++;
        if (j == pattern.length()) {                                           // full match ending at i
            hits.add(i - j + 1);
            j = lps[j - 1];                                                     // continue for overlaps
        }
    }
    return hits;
}

// lps[i] = length of the longest proper prefix of pattern[0..i] that is also its suffix.
static int[] prefixFunction(String p) {
    int[] lps = new int[p.length()];
    for (int i = 1, len = 0; i < p.length(); ) {
        if (p.charAt(i) == p.charAt(len)) lps[i++] = ++len;
        else if (len > 0) len = lps[len - 1];
        else lps[i++] = 0;
    }
    return lps;
}
```
`prefixFunction("abab")` gives `[0, 0, 1, 2]`; searching `"ababab"` returns `[0, 2]` because the fallback keeps the overlap.

## Pitfalls
- `s += c` in a loop is O(n²); `String.substring` copies since Java 7u6, so repeated substring recursion is O(n²) as well.
- `char` is a UTF-16 unit, not a character: emoji and many scripts take two `char`s; `length()`, `charAt` and reversal break them ([[text-encodings-and-unicode]]).
- Rolling hash without verification or with a small modulus produces false matches; an interviewer will ask about collisions.
- Comparing strings with `==` or sorting with `compareTo` when locale-aware collation is required.
- Regex for structured parsing of large inputs: catastrophic backtracking on patterns like `(a+)+` turns O(n) into exponential; a hand-written scan or a stack is safer ([[owasp-top-10-for-backend]] lists ReDoS).
- Related: [[tries]], [[hash-tables]], [[two-pointers-and-sliding-window]], [[interview-problem-patterns]].
