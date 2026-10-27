---
title: Dynamic programming
tags: [algorithms, dynamic-programming, memoization, recursion]
learned: 2026-10-27
---
## TL;DR
Dynamic programming applies when a problem has **optimal substructure** (the best answer is built from best answers of subproblems) and **overlapping subproblems** (the same subproblem is hit many times). You define a state, write the recurrence, then either **memoize** the recursion (top-down) or fill a table in dependency order (**tabulation**, bottom-up). The hard part is choosing the state; after that the code is ten lines. Classic families: 1D sequences (climb stairs, house robber, LIS), knapsack (0/1 and unbounded, coin change), two-sequence (LCS, edit distance), grid paths, interval DP.

## Key points

### Recognising DP
- The question asks for a count, a minimum/maximum, or "is it possible", over choices, and a brute-force recursion is exponential ([[recursion-and-backtracking]]).
- Greedy fails because a locally best choice can block a better global one ([[greedy-algorithms]]); coin change with coins `{1, 3, 4}` and target 6 is the standard counter-example to greedy.
- The recursion tree repeats arguments. If `f(i, remaining)` is called twice with the same values, memoize.

### Method
1. **State**: the minimal set of parameters that determines the rest of the problem (`i` = position, plus capacity, previous choice, count used, etc.).
2. **Recurrence**: `dp[state] = combine(dp[smaller states])`, with the explicit choices listed.
3. **Base cases**.
4. **Order**: top-down memoization follows naturally; bottom-up needs the loop order to respect dependencies.
5. **Answer location**: `dp[n]`, `max over dp[...]`, or `dp[0][n-1]` for interval DP.
6. **Space optimisation**: if `dp[i]` depends only on `dp[i-1]`, keep two rows (or one, iterating backwards for 0/1 knapsack).

### Top-down vs bottom-up
| | Memoization (top-down) | Tabulation (bottom-up) |
|---|---|---|
| Code | the recursion plus a cache (`HashMap` or array with sentinel) | nested loops |
| Computes | only reachable states | every state |
| Risk | stack depth = recursion depth ([[stack-vs-heap-memory]]) | wrong loop order |
| Space optimisation | hard | easy (rolling rows) |
| When | state space is sparse or irregular | state space is a dense grid |

### Classic families and their states
| Family | State | Recurrence idea | Time |
|---|---|---|---|
| climb stairs / Fibonacci / decode ways | `dp[i]` = ways to reach i | `dp[i-1] + dp[i-2]` | O(n) |
| house robber / max non-adjacent sum | `dp[i]` = best up to i | `max(dp[i-1], dp[i-2] + a[i])` | O(n) |
| longest increasing subsequence | `dp[i]` = LIS ending at i | `1 + max dp[j]` for `j < i, a[j] < a[i]`; O(n log n) with patience sorting ([[binary-search-and-variants]]) | O(n²) |
| coin change (min coins / ways), unbounded knapsack | `dp[amount]` | `min(dp[amount - c]) + 1` over coins | O(amount · coins) |
| 0/1 knapsack, subset sum, partition equal subset | `dp[i][w]` or 1D `dp[w]` iterated backwards | take or skip item i | O(n · W) |
| LCS, edit distance | `dp[i][j]` over prefixes of two strings | match → diagonal + 1; else best of neighbours | O(n · m) |
| grid paths / min path sum / unique paths with obstacles | `dp[r][c]` | from top or left | O(r · c) |
| word break | `dp[i]` = prefix of length i is segmentable | any `j < i` with `dp[j]` and word `s[j..i)` | O(n² · k) |
| interval DP (burst balloons, matrix chain, palindromic substrings) | `dp[l][r]` | split point k in `[l, r]` | O(n³) |
| bitmask DP (TSP, assign tasks) | `dp[mask][last]` | add one unset bit ([[bit-manipulation]]) | O(2ⁿ · n²) |
| DAG longest path | order by topological sort | relax edges in order | O(V + E) |

### Where DP shows up outside interviews
- Edit distance in fuzzy matching for search ([[elasticsearch-basics]]); `git diff` and JSON patch are LCS variants.
- Knapsack-style packing of items into capacity-limited batches; query planners picking join order with DP over subsets of tables ([[query-planning-and-execution]]).

## Example
```java
// Minimum number of top-up vouchers to reach `amount` exactly (unbounded coin change).
// Returns -1 when impossible. Bottom-up, O(amount * denominations) time, O(amount) space.
static int minVouchers(int[] denominations, int amount) {
    int INF = amount + 1;                              // more than any valid answer
    int[] dp = new int[amount + 1];
    Arrays.fill(dp, INF);
    dp[0] = 0;                                         // base case: zero vouchers for zero
    for (int a = 1; a <= amount; a++)
        for (int d : denominations)
            if (d <= a && dp[a - d] + 1 < dp[a])       // choice: use one voucher of value d last
                dp[a] = dp[a - d] + 1;
    return dp[amount] == INF ? -1 : dp[amount];
}
```
The top-down version is the same recurrence as a recursive `min(1 + f(amount - d))` with a `Map<Integer, Integer>` cache in front. For "number of ways" instead of "minimum", swap the inner condition for `dp[a] += dp[a - d]` and loop over denominations **outside** amounts to count combinations rather than permutations.

## Pitfalls
- Wrong loop order: counting coin combinations with amounts outside and coins inside counts `1+2` and `2+1` separately.
- 0/1 knapsack with a 1D array iterated forwards reuses an item twice; iterate `w` from high to low.
- Using `Integer.MAX_VALUE` as "infinity" and then adding 1 to it: overflow to a negative number that wins every `min`. Use `amount + 1` or check before adding.
- `HashMap<Long, Integer>` memo keyed on a packed pair of ints: fine, but a 2D array is much faster when the state space is dense.
- Memoized recursion over `n = 10⁵` states can overflow the stack; convert to tabulation.
- Related: [[recursion-and-backtracking]] for the exponential baseline, [[greedy-algorithms]] for when DP is not needed, [[big-o-and-complexity]] for pseudo-polynomial bounds (knapsack is O(n · W) where W is a value, not a size), [[interview-problem-patterns]].
