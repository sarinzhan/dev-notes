---
title: Java Memory Model and happens-before
tags: [jmm, happens-before, volatile, visibility]
learned: 2026-10-22
---
## TL;DR
The Java Memory Model (JMM) is the part of the language spec that says which writes a thread is guaranteed to see. It defines *actions* (read/write a variable, lock/unlock a monitor, read/write a `volatile`, start a thread) and one relation between them, **happens-before**: if A happens-before B, then everything A did is visible to B. Without such an edge the JVM and the CPU are free to cache values and reorder instructions.

## Key points
- **Visibility problem.** A thread may keep a field in a register or a CPU cache instead of main memory, so another thread reading main memory sees a stale value, and vice versa. The JMM describes this as "each thread has a working copy of variables"; this is a model, not a requirement on the hardware.
- **Reordering.** The compiler and the CPU may execute operations in a different order than written if a single thread cannot tell the difference. Another thread *can* tell, which is why a flag written after some data does not guarantee the data is visible unless the flag is `volatile`.
- The keywords that create happens-before edges are `synchronized`, `volatile` and `final`.

### The happens-before rules you actually use
- Program order: within one thread, each action happens-before the next one in source order.
- Monitor: unlocking a monitor happens-before every later lock of the **same** monitor (leaving a `synchronized` block → entering one on the same object).
- Volatile: a write to a `volatile` field happens-before every later read of that field.
- Thread start: `Thread.start()` happens-before anything in `run()`; everything in a thread happens-before `join()` on it returns.
- Transitivity: if A → B and B → C then A → C. This is what makes a volatile flag "publish" the plain writes before it.

### What synchronized and volatile do with memory
- Entering a `synchronized` block refreshes the thread's view from main memory; leaving it flushes the thread's writes. The guarantee only holds between threads that use the **same monitor**.
- `volatile` reads and writes go straight to main memory, and they cannot be reordered with reads and writes of other variables around them. That is why a volatile boolean works as a completion flag.

## Example
```java
class Config {
    private int value;                 // plain field
    private volatile boolean ready;    // the flag

    void init() { value = 42; ready = true; }   // writer thread

    int read() {                                // reader thread
        if (ready) return value;   // guaranteed 42: write(value) -> write(ready) -> read(ready) -> read(value)
        return -1;
    }
}
```
If `ready` were not `volatile`, the reader could see `ready == true` and `value == 0`.

## Heap vs stack from the thread's point of view
- Every thread has its **own stack**: local variables, method parameters, call frames. Nothing on one thread's stack is visible to another. Overflow → `StackOverflowError`.
- The **heap** is shared by all threads; every object lives there. Overflow → `OutOfMemoryError: Java heap space`. Threads cache heap values; `volatile` tells them not to.

## Pitfalls
- `volatile` gives visibility and ordering, **not atomicity**: `counter++` on a volatile is still a race. Use `AtomicInteger` or a lock.
- Synchronizing on different objects gives no happens-before edge between the threads.
- `synchronized` is also what makes `wait()`/`notify()` correct; see [[synchronization-and-monitors]]. Hazards that come from missing edges are in [[concurrency-hazards]].
