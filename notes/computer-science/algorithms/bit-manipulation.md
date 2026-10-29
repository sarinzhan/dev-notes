---
title: Bit manipulation
tags: [algorithms, bits, bitmask, xor]
learned: 2026-10-27
---
## TL;DR
Bit tricks treat an `int` or `long` as an array of 32 or 64 booleans. The operators are `&` (mask/test), `|` (set), `^` (toggle, and "cancel pairs"), `~` (invert), `<<` / `>>` / `>>>` (shift; `>>>` is the unsigned shift Java needs because its integers are signed). Interview uses: flags and bitmask subsets, XOR to find the single non-duplicated number, counting set bits, power-of-two checks, and bitmask DP over small sets. Backend uses: permission flags, feature flags, EMV tag bytes, protocol headers, and `BitSet` for compact membership.

## Key points

### Operators and identities
| Expression | Meaning |
|---|---|
| `x & (1 << i)` | test bit i (non-zero if set) |
| `x \| (1 << i)` | set bit i |
| `x & ~(1 << i)` | clear bit i |
| `x ^ (1 << i)` | toggle bit i |
| `x & (x - 1)` | clear the lowest set bit; zero iff `x` is a power of two (and `x > 0`) |
| `x & -x` | isolate the lowest set bit |
| `x ^ x == 0`, `x ^ 0 == x` | XOR cancels pairs: XOR of an array finds the element that appears once |
| `(a + b) >>> 1` | overflow-safe midpoint of non-negative ints ([[binary-search-and-variants]]) |
| `x >> 1` vs `x >>> 1` | arithmetic shift keeps the sign; logical shift fills with zeros |
| `x & 0xFF` | the low byte as 0–255; needed because Java `byte` is signed |

- Negative numbers are two's complement: `-x == ~x + 1`, so `x & -x` works; `Integer.MIN_VALUE` has no positive counterpart ([[numbers-bits-and-floating-point]]).
- Shift counts are taken modulo 32 for `int` and 64 for `long`: `1 << 32 == 1`, a classic bug. Use `1L << n` for `long` masks.
- `Integer.bitCount`, `numberOfTrailingZeros`, `highestOneBit`, `Long.reverse` compile to single CPU instructions (popcnt, tzcnt); prefer them over hand-written loops in real code.

### Interview patterns
| Problem | Trick |
|---|---|
| single number (others appear twice) | XOR everything |
| two single numbers | XOR all → `d`; split by the lowest set bit of `d` (`d & -d`), XOR each group |
| single number (others appear three times) | count bits per position mod 3, or the `ones`/`twos` state machine |
| missing number 0..n | XOR indices and values, or `n(n+1)/2 - sum` |
| count bits for 0..n | `bits[i] = bits[i >> 1] + (i & 1)` ([[dynamic-programming]]) |
| power of two / four | `x & (x-1) == 0`; for four also `x & 0x55555555 != 0` |
| reverse bits | swap halves, quarters, ... or `Integer.reverse` |
| subsets of a set of n ≤ 20 | iterate `mask` from 0 to `2ⁿ - 1`; element i is in the subset iff bit i |
| iterate sub-masks of `mask` | `for (s = mask; s > 0; s = (s - 1) & mask)` |
| bitmask DP (TSP, assignment, "shortest superstring") | `dp[mask][last]`, O(2ⁿ · n²) |
| add without `+` | `sum = a ^ b`, `carry = (a & b) << 1`, loop |

### Where bits live in backend work
- Permission and feature flags stored as an integer column: `flags & CAN_EXPORT != 0`. Readable in SQL with `(flags & 4) = 4`, but hard to index; a boolean per flag or a `bit varying`/array column is often better for querying ([[data-modeling-for-services]]).
- `EnumSet` is a bitmask over enum ordinals: O(1) membership, 1 bit per constant, and it beats `HashSet<Enum>` in every benchmark ([[enums]]).
- `java.util.BitSet` for compact membership of integer ids (which of 10 million subscriber ids have been migrated: 1.25 MB instead of a `HashSet<Long>` at 50+ MB).
- EMV and smart-card protocols encode data in TLV with bit-level flags (AIP, TVR, CVM results); decoding them is masks and shifts over `byte[]` with `& 0xFF` everywhere ([[emv-card-processing]]).
- CIDR matching `ip & mask == network & mask` ([[ip-addressing-and-nat]]), Bloom filters ([[bloom-filters-and-probabilistic]]), hash spreading in `HashMap` (`h ^ (h >>> 16)`, [[hashmap-internals]]).

## Example
```java
// Permission flags on an API key, stored as one int column.
final class Permissions {
    static final int READ = 1, WRITE = 1 << 1, PROVISION = 1 << 2, ADMIN = 1 << 3;

    static int grant(int flags, int perm)   { return flags | perm; }
    static int revoke(int flags, int perm)  { return flags & ~perm; }
    static boolean has(int flags, int perm) { return (flags & perm) == perm; }   // works for combined masks
    static int count(int flags)             { return Integer.bitCount(flags); }
}

// XOR trick: the one ICCID that was shipped but not activated, given a log where
// every activated ICCID appears exactly twice (shipped + activated) and one appears once.
static long unmatched(long[] events) {
    long acc = 0;
    for (long id : events) acc ^= id;          // pairs cancel, the singleton survives
    return acc;
}
```
Subset enumeration follows the same shape: `for (int mask = 0; mask < (1 << n); mask++)` and test `(mask & (1 << i)) != 0` for each element, O(2ⁿ · n), fine for n ≤ 20.

## Pitfalls
- `if (flags & WRITE)` does not compile in Java; `(flags & WRITE) != 0` is required, and `&` binds weaker than `==`, so parenthesise.
- `byte b = (byte) 0xFF; int v = b;` gives `-1`; always `b & 0xFF` when reading protocol bytes.
- `1 << 31` is `Integer.MIN_VALUE`, `1 << 32` is `1`; use `1L` for 64-bit masks.
- `>>` on a negative number keeps it negative forever (`-1 >> 1 == -1`); use `>>>` for unsigned logic.
- Storing bit flags in the database because it is "efficient" and then needing `WHERE has_permission_X` queries that cannot use an index.
- Related: [[numbers-bits-and-floating-point]] for the representation, [[dynamic-programming]] for bitmask DP, [[hash-tables]] for why hash spreading uses XOR and shifts, [[interview-problem-patterns]].
