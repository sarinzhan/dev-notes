---
title: Stack problems and the monotonic stack
tags: [algorithms, stack, monotonic-stack, arrays]
learned: 2026-10-27
---
## TL;DR
A stack solves anything with **nesting** or **"most recent unmatched"** semantics: balanced brackets, expression evaluation, undo, path normalisation, call frames. A **monotonic stack** keeps its elements sorted (increasing or decreasing from bottom to top) by popping everything that breaks the order before pushing; it answers "next greater / previous smaller element" questions for every index in O(n) total, and powers the largest-rectangle-in-histogram and daily-temperatures family. The stack-and-queue data structure itself lives in [[stacks-and-queues]]; this note is about the algorithms.

## Key points

### Classic stack problems
| Problem | Idea |
|---|---|
| balanced brackets | push opener, on closer pop and check it matches; valid iff stack empty at the end |
| evaluate RPN / infix via shunting-yard | operand stack and operator stack; pop while precedence of top ≥ current |
| simplify a path (`/a/./b/../c`) | split on `/`, push names, pop on `..` |
| min-stack (`getMin` in O(1)) | second stack of running minimums, or store `(value, minSoFar)` pairs |
| decode nested strings `3[a2[c]]` | count stack and string stack, resolve on `]` |
| basic calculator with parentheses | push running result and sign when `(`, pop on `)` |
| iterative DFS / tree traversal | explicit stack replaces recursion ([[recursion-and-backtracking]]) |

- Nesting depth equals stack size; the call stack of the JVM is the same structure ([[stack-vs-heap-memory]]).
- In Java use `ArrayDeque` as the stack; `java.util.Stack` is a synchronized legacy `Vector` subclass ([[collections-framework]]).

### Monotonic stack
- Invariant: when you are about to push `x`, pop every top `t` for which `x` would make the sequence non-monotonic. The popped `t` has just found its answer: `x` is the first element to its right that is greater (or smaller). Elements left on the stack at the end have no such element.
- Each index is pushed once and popped at most once, so the whole pass is O(n) despite the inner loop ([[big-o-and-complexity]]).

| Question | Stack order (bottom → top) | Pop condition |
|---|---|---|
| next greater element to the right | decreasing | `a[i] > a[top]` |
| next smaller to the right | increasing | `a[i] < a[top]` |
| previous greater to the left | decreasing | same, answer is the new top after popping |
| daily temperatures (days until warmer) | decreasing | `t[i] > t[top]`, answer `i - top` |
| largest rectangle in histogram | increasing | on pop, width = `i - newTop - 1`, area = `height[popped] * width` |
| trapping rain water | decreasing | on pop compute the bounded water between new top and `i` |
| stock span / sum of subarray minimums | increasing | contribution counting: each element is the minimum of `left * right` subarrays |
| remove k digits to make the smallest number | increasing | pop while `top > digit` and `k > 0` |

- Store **indices**, not values: you nearly always need the distance or the width.
- Circular arrays (next greater in a ring): iterate `i` from 0 to 2n−1 and use `i % n`.
- The same idea with a deque that pops from both ends gives **sliding window maximum** in O(n): keep indices decreasing by value, drop the front when it leaves the window ([[two-pointers-and-sliding-window]]).

### Where it appears outside interviews
- Parsers and template engines (bracket matching, operator precedence), JSON validators.
- Histogram-of-latencies analysis: largest rectangle = the longest period where latency stayed above a threshold weighted by its height.
- Kafka consumer offset management of "highest contiguous processed offset" uses a monotonic structure to find the next gap.

### Complexity
| Pattern | Time | Space |
|---|---|---|
| bracket matching, RPN | O(n) | O(n) worst (all openers) |
| monotonic stack pass | O(n) | O(n) |
| sliding window max (deque) | O(n) | O(k) |

## Example
```java
// For each day, how many days until a strictly higher p99 latency is observed.
// 0 when never. Monotonic decreasing stack of indices. O(n) time, O(n) space.
static int[] daysUntilHigher(int[] p99) {
    int n = p99.length;
    int[] answer = new int[n];
    Deque<Integer> stack = new ArrayDeque<>();        // indices; values strictly decreasing bottom->top
    for (int i = 0; i < n; i++) {
        while (!stack.isEmpty() && p99[i] > p99[stack.peek()]) {
            int j = stack.pop();                       // p99[i] is the first higher value after j
            answer[j] = i - j;
        }
        stack.push(i);
    }
    return answer;                                     // indices still on the stack keep 0
}

// Balanced brackets: the warm-up every interview still asks.
static boolean balanced(String s) {
    Deque<Character> st = new ArrayDeque<>();
    for (char c : s.toCharArray()) {
        switch (c) {
            case '(', '[', '{' -> st.push(c);
            case ')' -> { if (st.isEmpty() || st.pop() != '(') return false; }
            case ']' -> { if (st.isEmpty() || st.pop() != '[') return false; }
            case '}' -> { if (st.isEmpty() || st.pop() != '{') return false; }
            default -> {}
        }
    }
    return st.isEmpty();
}
```

## Pitfalls
- Pushing values instead of indices and then being unable to compute widths or distances.
- Using `>=` where `>` is needed (or vice versa) in the pop condition; it changes how equal elements are handled and breaks "sum of subarray minimums" counting (duplicates must be counted exactly once: use strict on one side and non-strict on the other).
- Forgetting the sentinel pass for histogram problems: append a 0-height bar (or drain the stack after the loop) so remaining bars get processed.
- `Deque<Integer>` with `stack.pop() != p99[...]` compares boxed `Integer` by identity above 127 ([[casting-and-autoboxing]]); compare `int`s, as in the example.
- `java.util.Stack.peek()` on an empty stack throws `EmptyStackException`; `ArrayDeque.peek()` returns `null`, and `pop()` throws `NoSuchElementException`. Check `isEmpty()` first.
- Related: [[stacks-and-queues]] for the structure and ring buffers, [[two-pointers-and-sliding-window]] for the deque-based window max, [[interview-problem-patterns]].
