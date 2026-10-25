---
title: Threads, processes and the Thread API
tags: [threads, runnable, callable, interrupt, threadlocal]
learned: 2026-10-22
---
## TL;DR
A **process** is a running program with its own address space and resources; a **thread** is a unit of execution inside a process that shares that process's memory. Java exposes platform threads through `Thread`, work through `Runnable` / `Callable`, and cooperative stopping through the interrupt flag.

## Key points
### Process vs thread
- Process: isolated virtual address space; talking to another process needs IPC. May contain many threads.
- Thread: the smallest unit the OS scheduler manages; lives inside a process; shares its heap, handles and data with the other threads. Much cheaper than a process, so scale with threads, not processes.

### Concurrency vs parallelism
- **Concurrency**: the system handles several tasks by switching between them, possibly on one core. An illusion of simultaneity.
- **Parallelism**: several tasks run at the same instant on different cores.
- Related terms: *multitasking* (one or more CPUs juggle tasks), *multiprocessing* (two or more CPUs). *Cooperative* multitasking: a task runs until it yields. *Preemptive*: the scheduler takes the CPU away after a time slice (how OS threads work; see [[virtual-threads]] for the cooperative model).

### Creating and running
- Subclass `Thread` and override `run()`; or pass a `Runnable` (`void run()`) to `new Thread(r)`; or submit a `Callable<V>` (`V call() throws Exception`, Java 5) to an executor ([[executors-and-thread-pools]]).
- `start()` asks the OS for a new thread and calls `run()` on it. Calling `run()` directly just runs the code on the current thread. Interview classic.
- `Thread.sleep(ms)` pauses the current thread; it throws `InterruptedException` if interrupted.
- Priority: `setPriority(1..10)`, default 5. A hint to the scheduler, rarely honoured in practice.
- Daemon threads (`setDaemon(true)`) are background helpers; the JVM exits when only daemon threads remain. The main thread cannot be made a daemon.

### Stopping a thread
- `stop()` is deprecated and unsafe: it kills the thread instantly, leaving monitors and connections in an unknown state.
- `interrupt()` only sets a flag. The target thread must check it (`Thread.currentThread().isInterrupted()`), and blocking calls (`sleep`, `wait`, `join`, blocking queues) throw `InterruptedException` and **clear** the flag.
- `Thread.interrupted()` (static) reads and clears the flag; `isInterrupted()` reads it without clearing.

### Thread states (`Thread.State`)
`NEW` → `RUNNABLE` (ready or running) → `BLOCKED` (waiting for a monitor) / `WAITING` (`wait()`, `join()`, `park()`) / `TIMED_WAITING` (same with a timeout, `sleep`) → `TERMINATED`.

### ThreadLocal
Gives each thread its own copy of a variable; other threads cannot see it. Used for per-request context (security, transactions, MDC). With pooled threads, always `remove()` it when done, or the next task inherits stale data.

## Example
```java
Thread worker = new Thread(() -> {
    while (!Thread.currentThread().isInterrupted()) {
        try { poll(); Thread.sleep(500); }
        catch (InterruptedException e) {
            Thread.currentThread().interrupt(); // restore the flag, then leave
            return;
        }
    }
});
worker.start();
// later
worker.interrupt();
worker.join();
```

## Pitfalls
- Swallowing `InterruptedException` with an empty catch makes the thread unstoppable. Either rethrow or re-set the flag as above.
- Creating threads by hand in a service is almost always wrong; use an executor.
- Thread states confuse people: a thread waiting on I/O is `RUNNABLE` from the JVM's point of view.
