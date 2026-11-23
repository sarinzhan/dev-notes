---
title: Blocking queues
tags: [blockingqueue, producer-consumer, backpressure, thread-pools]
learned: 2026-11-23
---
## TL;DR
A `BlockingQueue` is a thread-safe queue whose `put` waits while it is full and whose `take` waits while it is empty. That makes it the standard way to pass work between threads (producer/consumer) and the thing every `ThreadPoolExecutor` is built around. Pick the implementation by two questions: is it bounded (backpressure, no OOM), and how does it lock (one lock, two locks, or none)?

## Key points
### Four ways to call every operation
| | Throws | Returns a value | Blocks | Waits with a timeout |
|---|---|---|---|---|
| Insert | `add(e)` → `IllegalStateException` | `offer(e)` → `false` | `put(e)` | `offer(e, time, unit)` |
| Remove | `remove()` → `NoSuchElementException` | `poll()` → `null` | `take()` | `poll(time, unit)` |
| Examine | `element()` | `peek()` | — | — |

The first two columns come from `Queue`; `BlockingQueue` adds the last two. Blocking queues reject `null`, because `poll()` uses `null` to mean "empty". `drainTo(list, max)` moves up to `max` elements in one call, which is how consumers read in batches.

### Implementations
| Class | Bounded | Backed by | Locking | Notes |
|---|---|---|---|---|
| `ArrayBlockingQueue` | yes, fixed capacity | circular array | one `ReentrantLock`, two conditions (`notEmpty`, `notFull`) | no allocation per element; optional fair mode |
| `LinkedBlockingQueue` | optional, default `Integer.MAX_VALUE` | linked nodes | **two locks**: `putLock` and `takeLock` | producers and consumers do not block each other; one node per element |
| `LinkedBlockingDeque` | optional | linked nodes | one lock | both ends: e.g. urgent work goes to the front |
| `PriorityBlockingQueue` | no | binary heap | one lock | `take` returns the smallest; `put` never blocks |
| `DelayQueue` | no | priority heap | one lock | elements implement `Delayed`; `take` returns only expired ones: retries, timeouts |
| `SynchronousQueue` | capacity 0 | — | lock-free hand-off | every `put` waits for a `take`; `offer` without a waiting consumer returns `false` |
| `LinkedTransferQueue` | no | linked nodes | lock-free | `transfer(e)` waits until a consumer receives it; `tryTransfer` does not wait |

Not blocking: `ConcurrentLinkedQueue` / `ConcurrentLinkedDeque` (lock-free, `poll` returns `null` when empty); see [[concurrent-collections]].

### Why the choice matters for thread pools
- `ThreadPoolExecutor` calls `queue.offer(task)`, which never blocks. If the queue accepts the task, no new thread is created above the core size. Only when `offer` returns `false` does the pool grow to `maximumPoolSize`, and after that the rejection policy runs.
- `newFixedThreadPool` uses an unbounded `LinkedBlockingQueue`: `offer` never fails, so the pool never grows and the queue can eat all the memory under overload.
- `newCachedThreadPool` uses a `SynchronousQueue`: `offer` succeeds only if an idle thread is waiting, otherwise a new thread is created. That is why it can create thousands of threads.
- In production, use a bounded `ArrayBlockingQueue` or `LinkedBlockingQueue` with an explicit rejection policy (`CallerRunsPolicy` slows the producer down). Details: [[executors-and-thread-pools]].

### Backpressure and shutdown
- A bounded queue is backpressure for free: a fast producer blocks on `put` instead of filling the heap.
- `put` and `take` throw `InterruptedException`. Either rethrow it or restore the flag with `Thread.currentThread().interrupt()`; never swallow it.
- To stop consumers, interrupt them or send a **poison pill**: a special element that means "no more work".
- Blocking on these queues is fine on virtual threads: they use `ReentrantLock` and conditions, so the carrier thread is released ([[virtual-threads]]).

## Example
```java
record Event(String type, String payload) {}
static final Event STOP = new Event("STOP", "");               // poison pill

BlockingQueue<Event> queue = new ArrayBlockingQueue<>(10_000);  // bounded: backpressure

// producer: waits instead of running out of memory when the writer is slow
void onEvent(Event e) throws InterruptedException { queue.put(e); }

// consumer: one blocking take, then drain the rest and write a batch
void writerLoop() throws InterruptedException {
    List<Event> batch = new ArrayList<>(500);
    while (true) {
        batch.add(queue.take());
        queue.drainTo(batch, 499);
        boolean stop = batch.remove(STOP);
        if (!batch.isEmpty()) repository.insertAll(batch);
        if (stop) return;
        batch.clear();
    }
}
```

## Pitfalls
- "Unbounded" queues are bounded by the heap. `new LinkedBlockingQueue<>()` without a capacity is the classic slow OOM.
- `size()` and `remainingCapacity()` are snapshots: by the next line another thread may have changed them. Do not write `if (queue.size() < max) queue.put(e)`.
- Iterating a `PriorityBlockingQueue` does not return elements in priority order; only `take`/`poll` do.
- `SynchronousQueue` holds nothing, so `peek()` always returns `null` and `size()` is always 0.
- Related: [[concurrent-collections]], [[executors-and-thread-pools]], [[synchronizers]].
