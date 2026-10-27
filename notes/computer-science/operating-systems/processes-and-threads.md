---
title: Processes and threads from the OS point of view
tags: [operating-systems, processes, threads, scheduling]
learned: 2026-10-27
---
## TL;DR
A **process** is the unit of isolation: its own virtual address space, file descriptor table, credentials and PID. A **thread** is the unit of scheduling: an execution context (registers, stack, program counter) that lives inside a process and shares its memory with sibling threads. Switching between threads of one process is cheaper than switching between processes mainly because the address space (and therefore the TLB and caches) stays warm. Linux specifics, `fork`/`exec` and signals are in [[linux-processes]]; this note is about the concepts an interviewer expects.

## Key points

### What the kernel stores per process
- The **process control block** (PCB; `task_struct` in Linux) holds: PID and parent, state (running, ready, blocked, zombie), saved CPU registers, pointer to the page tables, open file descriptors, signal dispositions, scheduling priority, accounting counters, cgroup membership.
- The **address space** is private: code (text), initialised data, heap (`brk`/`mmap`), shared libraries, and one stack per thread ([[virtual-memory-and-paging]], [[stack-vs-heap-memory]]).
- Isolation is enforced by the MMU: a process cannot read another process's memory unless both explicitly map a shared segment ([[ipc-mechanisms]]).

### Process vs thread
| | Process | Thread |
|---|---|---|
| Memory | own address space | shared with the process |
| Creation cost | high (`fork` copies page tables, copy-on-write pages) | low (`clone` with shared VM; just a new stack and registers) |
| Context switch | save registers + switch page tables → TLB flush (mitigated by PCID/ASID) | save registers + switch stack pointer; TLB stays valid |
| Failure isolation | a crash kills only that process | one segfault or uncaught native error kills every thread |
| Communication | IPC: pipes, sockets, shared memory | plain memory; needs synchronization ([[race-conditions-and-critical-sections]]) |
| Typical use | nginx workers, Postgres backends, Chrome tabs | Tomcat request handlers, Kafka client IO threads, GC workers |
- Postgres chose process-per-connection; that is why connections are expensive and why pooling matters ([[hikaricp-pool-sizing]]). The JVM chose one process with many threads, so a `StackOverflowError` in one thread is survivable, but an `OutOfMemoryError` in native memory is not.

### Context switch cost
- Direct cost: a trap into the kernel, saving and restoring registers, scheduler bookkeeping. Typically 1–3 µs on modern hardware ([[latency-numbers]]).
- Indirect cost, usually bigger: the new thread's working set is not in L1/L2 and the branch predictor is cold ([[cpu-caches-and-cache-lines]]). A thread that blocks after every few microseconds of work spends more time switching than working.
- `vmstat 1` shows context switches per second in the `cs` column; `pidstat -w` shows voluntary (blocked on IO or lock) vs involuntary (time slice expired, preempted) switches per process. A high involuntary count means CPU contention; a high voluntary count means IO or lock waits.

### User-level vs kernel-level threads
| Model | Who schedules | Blocking syscall | Examples |
|---|---|---|---|
| 1:1 kernel threads | kernel | blocks only that thread | Linux NPTL, JVM platform threads |
| N:1 user threads (green threads) | user-space library | blocks the whole process | early JVMs, classic coroutine libraries |
| M:N | both: library multiplexes M user threads onto N kernel threads | library must intercept blocking calls and park the user thread instead | Go goroutines, Java [[virtual-threads]] (JDK 21) |
- Java platform threads are 1:1: `new Thread().start()` ends in `clone()`, and you can see them with `top -H -p <pid>` or `ls /proc/<pid>/task`. Virtual threads are M:N on top of a `ForkJoinPool` of carrier threads; the JDK unmounts them on blocking IO and `LockSupport.park`, but **pins** them on `synchronized` (fixed in JDK 24) and in native frames.
- Why M:N came back: a kernel thread costs around 1 MiB of reserved stack plus a `task_struct`, and tens of thousands of them make the scheduler slow. A virtual thread stack is a few hundred bytes to a few KiB on the heap ([[jvm-memory-areas]]).

### Process and thread states
- `New → Ready → Running → (Blocked ↔ Ready) → Terminated`. "Blocked" is the normal state of a server thread: waiting on a socket, a lock, or a disk read.
- `Thread.State` in Java maps loosely: `RUNNABLE` covers both OS-running and OS-ready (and even blocked in native IO), `BLOCKED` is waiting for a monitor, `WAITING`/`TIMED_WAITING` is parked ([[threads-basics]]).

## Example
```bash
# A Spring Boot app: one process, many kernel threads
PID=$(pgrep -f 'esim-provisioning.jar')
ls /proc/$PID/task | wc -l          # number of threads (kernel tasks)
cat /proc/$PID/status | grep -E 'Threads|VmRSS|VmSize|voluntary'
#   VmSize:   6 412 340 kB   -> reserved virtual space (heap + stacks + mmap)
#   VmRSS:      812 004 kB   -> actually resident
#   Threads:  57
#   voluntary_ctxt_switches:    183 214
#   nonvoluntary_ctxt_switches:   1 042

# Per-thread CPU: which JVM threads are hot?  (nid in a jstack dump = hex TID)
top -H -p $PID -n 1 | head -20
printf '%x\n' 12345                 # convert TID to the nid shown by jstack
```
A thread dump line like `"kafka-producer-network-thread" ... nid=0x3039` points at kernel task 12345: the same thread seen by the OS and by the JVM.

## Pitfalls
- Assuming threads are free: each platform thread reserves `-Xss` (1 MiB default) of virtual memory and a kernel structure; a thread-per-request design dies at a few thousand concurrent requests. Use a bounded pool ([[executors-and-thread-pools]]) or virtual threads.
- Forgetting that a process context switch flushes the TLB: a design that bounces a request between several processes (sidecars, local proxies) pays this on every hop.
- Measuring only CPU usage: a service with 20% CPU and a huge `cs` rate is bottlenecked on lock or IO waits, not compute. Check `pidstat -w` before adding cores.
- `ps` shows the process as `R` while every thread of a JVM may be parked except GC: process state is a summary of its threads.
- Related: [[cpu-scheduling]] for how the kernel picks the next thread, [[linux-processes]] for `fork`, `exec`, signals and `systemd`.
