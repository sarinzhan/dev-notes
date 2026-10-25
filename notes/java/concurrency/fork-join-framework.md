---
title: Fork/Join framework
tags: [fork-join, parallelism, work-stealing, parallel-streams]
learned: 2026-10-22
---
## TL;DR
Fork/Join (Java 7) runs divide-and-conquer tasks: a big task is split (**fork**) into subtasks recursively until they are small enough to compute directly, then the results are merged (**join**). `ForkJoinPool` keeps its workers busy with **work stealing**, and it is the engine under parallel streams and the default `CompletableFuture` executor.

## Key points
- `ForkJoinPool`: a specialized `ExecutorService` where every worker has its own double-ended queue. A worker pushes and pops its own subtasks from one end; an idle worker steals from the *other* end of a busy worker's deque. Minimal contention, good locality.
- `RecursiveTask<V>`: subtask that returns a result; implement `V compute()`.
- `RecursiveAction`: subtask without a result.
- `ForkJoinPool.commonPool()` is shared JVM-wide, sized at `cores - 1`. Parallel streams and `CompletableFuture.supplyAsync` without an executor use it.
- Flow: submit the root task → in `compute()` decide whether the task is small enough → if not, `fork()` the left half, `compute()` the right half directly, `join()` the left → return the merged result.

## Example
```java
class SumTask extends RecursiveTask<Long> {
    private static final int THRESHOLD = 10_000;
    private final long[] arr; private final int lo, hi;
    SumTask(long[] arr, int lo, int hi) { this.arr = arr; this.lo = lo; this.hi = hi; }

    @Override protected Long compute() {
        if (hi - lo <= THRESHOLD) {
            long s = 0; for (int i = lo; i < hi; i++) s += arr[i]; return s;
        }
        int mid = (lo + hi) >>> 1;
        SumTask left = new SumTask(arr, lo, mid);
        left.fork();                                   // run asynchronously
        long right = new SumTask(arr, mid, hi).compute(); // run here
        return left.join() + right;
    }
}
long total = ForkJoinPool.commonPool().invoke(new SumTask(data, 0, data.length));
```

## Pitfalls
- Blocking I/O inside a fork/join task starves the common pool and slows every parallel stream in the JVM. Fork/Join is for CPU-bound work; for I/O use [[executors-and-thread-pools]] or [[virtual-threads]].
- Too small a threshold drowns the work in task overhead; too large loses parallelism. Thousands of leaf tasks is a good target.
- `fork()` both halves and `join()` both wastes the current worker; compute one half directly as above.
- Related: [[completable-future]] runs on the common pool by default.
