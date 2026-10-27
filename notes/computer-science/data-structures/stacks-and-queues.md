---
title: Stacks, queues, deques and ring buffers
tags: [data-structures, stack, queue, deque, ring-buffer]
learned: 2026-10-27
---
## TL;DR
A stack is LIFO (push/pop at one end), a queue is FIFO (add at the tail, remove at the head), and a deque does both ends. All three are O(1) per operation when backed by a ring buffer over an array or by a linked list. In Java the right class for all of them is `ArrayDeque`; `Stack` is a legacy synchronized `Vector`, and `LinkedList` is slower. Stacks model recursion, parsing and undo; queues model buffering, BFS and producer–consumer. The bounded variants (`ArrayBlockingQueue`, `LinkedBlockingQueue`) are the thread handoff primitive behind every executor ([[executors-and-thread-pools]]).

## Key points

### The abstractions
| ADT | Operations | Order | Typical uses |
|---|---|---|---|
| Stack | `push`, `pop`, `peek` | LIFO | call stack, DFS, bracket matching, expression evaluation, undo, monotonic stack ([[stack-and-monotonic-stack]]) |
| Queue | `offer`, `poll`, `peek` | FIFO | BFS ([[graph-traversal-bfs-dfs]]), task queues, buffering between producer and consumer, rate limiting |
| Deque | all of the above at both ends | both | sliding-window maximum, work stealing ([[fork-join-framework]]), LRU-like recency |
| Priority queue | `offer`, `poll` by priority | by key | separate structure, see [[heaps-and-priority-queues]] |

### Implementations and layout
| Backing | push/pop/offer/poll | Memory | Notes |
|---|---|---|---|
| Array as stack (top index) | O(1), amortized on growth | contiguous | simplest; `ArrayDeque` for one end |
| **Ring buffer** (circular array, `head`/`tail` indexes modulo capacity) | O(1), amortized on growth | contiguous, no per-element allocation | `ArrayDeque`, `ArrayBlockingQueue`, Disruptor, Kafka producer accumulator conceptually |
| Doubly linked list | O(1) | one node per element, cache-hostile | `LinkedList`, `LinkedBlockingQueue`, `ConcurrentLinkedQueue` (lock-free CAS on head/tail) |
| Two stacks | O(1) amortized | two arrays | the classic "queue from two stacks" question |

- A ring buffer with capacity a power of two replaces `% capacity` with `& (capacity - 1)`; `ArrayDeque` does exactly this and doubles when `head == tail` after a write.
- Bounded ring buffers are the natural backpressure tool: when the buffer is full the producer blocks or fails fast instead of growing memory without limit ([[backpressure-and-flow-control]]).

### Queue from two stacks (amortized O(1))
- `in` stack receives every `offer`. On `poll`, if `out` is empty, pop everything from `in` and push onto `out`, then pop from `out`. Each element moves at most twice, so the total cost over n operations is O(n).

### Java API map
| Need | Use | Avoid |
|---|---|---|
| Single-threaded stack | `ArrayDeque` (`push`/`pop`) | `java.util.Stack` (synchronized, extends `Vector`) |
| Single-threaded queue/deque | `ArrayDeque` | `LinkedList` |
| Thread handoff, bounded | `ArrayBlockingQueue` (single lock), `LinkedBlockingQueue` (two locks) | unbounded queues in a thread pool (OOM instead of rejection) |
| Lock-free, unbounded | `ConcurrentLinkedQueue`/`ConcurrentLinkedDeque` | — |
| Delayed or prioritized | `DelayQueue`, `PriorityBlockingQueue` | — |

`ArrayDeque` rejects `null` because `poll`/`peek` use `null` to mean "empty". Details of the concurrent ones are in [[concurrent-collections]]; `Queue`/`Deque` interface methods (`add` throws vs `offer` returns false) are in [[collections-framework]].

### Where they appear in systems
- The JVM call stack is a stack of frames; `StackOverflowError` is this structure running out of its fixed size ([[stack-vs-heap-memory]]).
- A thread pool is a queue plus workers; the queue type decides whether the pool rejects, blocks or grows ([[executors-and-thread-pools]]).
- Kafka partitions and message brokers are durable queues/logs ([[message-queues-and-streams]]); an in-memory queue is the same idea without durability.

## Example
A fixed-capacity ring buffer, the structure behind `ArrayDeque` and `ArrayBlockingQueue`, used here as a bounded in-memory buffer for the last N trace samples.

```java
public final class RingBuffer<T> {
    private final Object[] items;
    private int head;   // index of the oldest element
    private int size;

    public RingBuffer(int capacity) {
        if (Integer.bitCount(capacity) != 1) throw new IllegalArgumentException("power of two");
        items = new Object[capacity];
    }

    /** Appends; when full, overwrites the oldest (keep-latest semantics). */
    public void offer(T item) {
        int tail = (head + size) & (items.length - 1);
        items[tail] = item;
        if (size == items.length) head = (head + 1) & (items.length - 1); // drop oldest
        else size++;
    }

    @SuppressWarnings("unchecked")
    public T poll() {
        if (size == 0) return null;
        T item = (T) items[head];
        items[head] = null;                       // let GC reclaim it
        head = (head + 1) & (items.length - 1);
        size--;
        return item;
    }

    public int size() { return size; }
    public boolean isFull() { return size == items.length; }
}
```

## Pitfalls
- Using `java.util.Stack` in new code: it is synchronized, inherits `Vector`'s index API, and iterates bottom-to-top, which surprises people who expect LIFO order.
- `Executors.newFixedThreadPool` uses an unbounded `LinkedBlockingQueue`: a slow consumer turns into an `OutOfMemoryError` rather than rejected tasks. Build pools with a bounded queue and a rejection policy.
- Forgetting to null out the slot in an array-backed stack/queue keeps dead objects reachable (a memory leak in a long-lived buffer).
- Using a queue as a stack or vice versa in BFS/DFS flips the traversal; BFS needs FIFO.
- Related: [[data-structures]] for the overview, [[linked-lists]] and [[arrays-and-dynamic-arrays]] for the two backings, [[stack-and-monotonic-stack]] for the interview patterns.
