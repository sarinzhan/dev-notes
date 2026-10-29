---
title: Process management
tags: [operating-systems, processes, threads, scheduling, context-switch, linux]
learned: 2026-10-28
order: 6
aliases: [processes-and-threads, cpu-scheduling]
---
## TL;DR
[[anatomy-of-a-process]] shows what one process looks like in memory; this note is about how the kernel **manages many of them**. For every process it keeps a **process control block** (PCB) in a kernel **process table**; for every thread a **thread control block** (TCB). A **thread** is a lightweight process: it shares code, heap, data, open files and the page table, and owns only its stack, program counter and registers. The CPU knows nothing about processes; the OS loads a process's registers and page-table pointer into it (its **context**), and a **context switch** saves one context into its PCB and loads another. Switching between processes also invalidates the TLB unless entries are tagged with an **ASID/PCID**, which is why switching threads of one process is cheaper. The **scheduler** decides which thread runs on which core and for how long: preemptively, in time slices, favouring IO-bound threads that wake up. Running many processes or threads gives isolation or concurrency, and with shared memory comes the price: **race conditions**.

## Key points
### What the kernel keeps per process: the PCB
- The kernel needs metadata about each process. The **process control block** (`task_struct` in Linux) holds:

| Field | What it is |
|---|---|
| PID, parent PID, state | identity and current state (running, ready, blocked, stopped, zombie) |
| Saved program counter and registers | where to continue and with what values, while the process is not on a CPU |
| Process control info | priority, nice value, scheduling class, CPU affinity |
| Page table pointer | the value loaded into the page-table base register when it runs ([[memory-management]]) |
| Memory management info | where its code, data, heap and stacks are (`mm_struct`, the list of mappings) |
| Accounting | CPU time used, memory, page faults, context-switch counts: what `ps`, `top` and `/proc/<pid>/status` show |
| IO info | the file descriptor table: files, sockets, pipes ([[syscalls-and-file-descriptors]]) |
| IPC info | semaphores, shared memory segments, message queues; signal handlers and pending signals |
| Credentials, cgroups, namespaces | user and group, resource limits, which container it is in |
- The **process table** maps each PID to its PCB for a quick lookup. It lives in kernel space; user programs see it only through `/proc`.
- The **address space** itself is private: the MMU makes sure a process cannot read another's memory unless both map a shared segment ([[ipc-mechanisms]]).

### Threads and the TCB
- A **thread** is a lightweight process: an independent flow of execution inside a process. Threads of one process share the code, heap, data section, open files and the page table; each thread has its **own stack** (in the same address space, below the main stack) and its own **program counter and registers**.
- For each thread the kernel keeps a **thread control block**: TID, state, saved PC and registers, its stack pointer, priority, CPU accounting, and a pointer to the parent PCB. A **thread table** maps TID to TCB.
- In Linux there is no separate TCB: every thread is a `task_struct`, created by `clone()` with the address space (`mm`), file table and signal handlers shared with the parent. A "process" is just a group of tasks sharing a `mm`; the scheduler only ever schedules tasks. `ls /proc/<pid>/task` lists the threads of a process.

```anim
process-thread-tables
```

### Process vs thread
| | Process | Thread |
|---|---|---|
| Memory | own address space | shared with the process |
| Creation cost | high (`fork` copies page tables, copy-on-write pages) | low (`clone` with shared VM; just a new stack and registers) |
| Context switch | save registers + switch page tables → TLB flush (mitigated by PCID/ASID) | save registers + switch stack pointer; TLB stays valid |
| Failure isolation | a crash kills only that process | one segfault or uncaught native error kills every thread |
| Communication | IPC: pipes, sockets, shared memory | plain memory; needs synchronisation ([[race-conditions-and-critical-sections]]) |
| Typical use | NGINX workers, Postgres backends, Chrome tabs | Tomcat request handlers, Kafka client IO threads, GC workers |

### Multi-process vs multi-threaded servers
- **Multi-process**: the program starts several processes, isolated from each other. **NGINX** runs one worker per core; **Postgres** forks one backend process per connection. A crash or memory corruption stays in one process; the price is more memory, process creation and IPC for anything shared. That is why Postgres connections are expensive and need pooling ([[hikaricp-pool-sizing]]).
- **Multi-threaded**: one process starts many threads that share its memory. **MySQL** runs a thread per connection; **libuv** (Node.js) uses a thread pool for file IO and DNS; the JVM is one process with many threads. Cheaper and sharing is free, but one native crash or out-of-memory takes down everything, and shared data needs synchronisation.
- Both kinds of server have to share one listening port between their workers: by inheriting the listening socket through `fork`, or with `SO_REUSEPORT` sockets. How that works is in [[socket-management]].
- **Fork** creates the new process: the child must have its own virtual memory, but copy-on-write means pages are shared until one side writes. Redis uses exactly this for asynchronous durability (`BGSAVE`). The details and animation are in [[memory-management]]; the lifecycle (`exec`, `wait`, zombies) is in [[signals-and-process-lifecycle]].

- **Postgres and the process model.** Every connection is a forked backend, so the default `max_connections` is 100 and thousands of connections are a bad idea: thousands of processes, page tables and TLB flushes. Applications put a pool in front (HikariCP in the app, PgBouncer between apps and the database) and share a few connections between many users. Besides backends, Postgres runs auxiliary processes: checkpointer, background writer, WAL writer, autovacuum launcher and workers, logger, archiver, and parallel query workers.
- In 2023 the Postgres developers started a long discussion about moving to threads. One argument from it: on big machines with many connections, switching between processes costs more than switching threads of one process, and much of that time goes to TLB misses, which the process model cannot avoid. The counter-arguments are the work involved (global variables everywhere in the code) and losing crash isolation. It is still open.
- **Pipelining on a database connection**: Postgres's pipeline mode (libpq, Postgres 14+; the JDBC driver batches the same way) lets a client send several queries without waiting. The results come back **in the order the queries were sent**, so the client matches them by position; there are no query IDs (the lecture suggests there are). A slow first query delays the others, the same head-of-line blocking as HTTP/1.1 pipelining ([[cpu]]).
- **Threads trade isolation for speed.** All threads of a process share the heap, so a buggy or compromised thread can read or overwrite any other thread's data; nothing in the kernel separates them. What makes injected code hard to run is not threads but the memory permissions: the heap and stacks are not executable, the code is not writable ([[anatomy-of-a-process]]).

### Seeing fork and copy-on-write: `smaps`
The lecture's demo: a C program `malloc`s 10 MB, writes it with `memset` (until then the pages have no frames, so RSS stays small), forks, and both processes loop forever.

```bash
top -p 999,1000                      # RES ~10 MB for both: looks like 20 MB in total
grep -A 20 heap /proc/999/smaps      # one mapping of the process, in detail
#   Rss:            10244 kB
#   Pss:             5122 kB          <- proportional: shared pages divided by the number of sharers
#   Shared_Clean:       0 kB
#   Shared_Dirty:   10240 kB          <- written before fork, now mapped by parent and child
#   Private_Clean:      0 kB
#   Private_Dirty:      4 kB          <- pages only this process maps
cat /proc/999/smaps_rollup           # the same totals for the whole process
```

- **Dirty** means the page has been written; **clean** means it still matches its source (a file, or never touched). **Shared** pages are mapped by more than one process; **private** ones by only this one.
- In the second version the child writes one byte every 1,000 bytes (every 4,096 would touch each page exactly once). With every refresh the child's `Private_Dirty` grows and `Shared_Dirty` shrinks: each written page was copied for the child, and the parent's original became private to the parent.
- **RSS double-counts shared pages**; summing RSS over 20 forked workers can claim several times the real RAM. **Pss** gives each process its share, so the sum of Pss is the true total. `smem` shows it per process.

```anim
fork-cow-smaps
```

### Shared memory
- Processes and threads can share memory. Threads share the whole address space automatically. Separate processes share by mapping the same physical pages: `mmap(MAP_SHARED)` or `shm_open`. The **virtual** addresses can differ between the processes; the **physical** pages are the same.
- The classic example is a database's **shared buffers**: every Postgres backend process attaches to the same `shared_buffers` segment, so a page read by one connection is in memory for all of them ([[postgres-performance-checklist]]).

### The CPU and the context
- The CPU does not know what a process is. The OS loads the process's data into the CPU registers: pc, sp, bp, lr, general-purpose registers, and the pointer to its page table (the page table base register, ptbr: CR3 on x86, TTBR on ARM). This set is called the **context**. Then the CPU simply executes instructions.
- A **context switch** saves the current context into the current process's PCB (a memory write) and loads the next process's PCB into the registers (a memory read): pc, bp, sp, lr, ptbr and more. The switched-out process becomes **ready**; the new one **running**.
- When it happens: the time slice ends (timer interrupt), the thread blocks (IO, lock, `sleep`), a higher-priority thread wakes, or the thread exits.

- The page table base register must hold a **physical** address: if it were virtual, translating it would need the page table it points to. The PCB's copy of the PC is stale while the process runs; the live PC is only in the CPU and is written back at the switch.

### TLB flush and ASID
- The **TLB** caches virtual-to-physical translations. Processes cannot share these mappings: virtual address 4000 means one physical page for process 100 and another for 200. So a switch to a different process has to **flush** the TLB, and the new process then misses on its first memory accesses and pays page-table walks. That is slow, and usually costs more than saving the registers.
- **Threads of the same process are faster to switch**: same memory, same page table, the TLB stays valid. This holds only as long as both threads belong to the same process.
- **ASID** (address space ID on ARM) and **PCID** (process-context ID on x86) tag each TLB entry with the process it belongs to, so entries of several processes live side by side and a switch does not need a flush. The tags are small (8 or 16 bits on ARM, 12 on x86), so the kernel recycles them and flushes only when it runs out.
- Spectre/Meltdown mitigations (page-table isolation) made the user↔kernel switch itself more expensive; PCID is what keeps it tolerable.

- The lecture doubts that ASIDs are practical with so few bits and so many processes. The kernel handles it with generations: it hands out tags until they run out, then flushes the whole TLB once, starts a new generation and reassigns tags as processes run. Stale entries of a process that has not run for a while are simply evicted by newer ones. Linux uses PCID on x86 since 4.14 and ASIDs on ARM64.

```anim
context-switch
```

### Context switch cost
- Direct cost: a trap into the kernel, saving and restoring registers, scheduler bookkeeping. Typically 1–3 µs on modern hardware ([[latency-numbers]]).
- Indirect cost, usually bigger: the new thread's working set is not in L1/L2, the TLB and the branch predictor are cold ([[cpu-caches-and-cache-lines]]). A thread that blocks after every few microseconds of work spends more time switching than working.
- `vmstat 1` shows context switches per second in the `cs` column; `pidstat -w` shows voluntary (blocked on IO or a lock) vs involuntary (time slice expired, preempted) switches per process. Many involuntary switches mean CPU contention; many voluntary ones mean IO or lock waits.

### Process and thread states
- `New → Ready → Running → (Blocked ↔ Ready) → Terminated`. "Blocked" is the normal state of a server thread: waiting on a socket, a lock or a disk read. Running → Ready is preemption; Running → Blocked is the thread giving up the CPU itself.
- In `ps`: `R` running or runnable, `S` sleeping (interruptible), `D` uninterruptible (usually disk IO), `T` stopped, `Z` zombie ([[linux-processes]]).
- `Thread.State` in Java maps loosely: `RUNNABLE` covers both OS-running and OS-ready (and even blocked in native IO), `BLOCKED` is waiting for a monitor, `WAITING`/`TIMED_WAITING` is parked ([[threads-basics]]).

### Preemptive multitasking
- Some processes run for a long time. In **cooperative** multitasking a program keeps the CPU until it yields; one that never yields starves everyone else, which is how a single hung program froze Windows 3.1 or classic Mac OS.
- In **preemptive** multitasking the OS switches such processes out: a timer interrupt fires every few milliseconds, and when a thread's **time slice** is used up the kernel takes the CPU away. Every modern kernel works this way.
- Many user-space schedulers are still cooperative: Java [[virtual-threads]], JavaScript event loops, Go before 1.14. A virtual thread that spins in a CPU loop blocks its carrier thread and starves the other virtual threads on it.
- For servers the wakeup path matters more than the time slice: a request thread is blocked on a socket, wakes when data arrives, runs a few hundred microseconds, blocks again. **Scheduling latency** is the time between the wakeup and actually running.

### Scheduling algorithms
- The scheduler decides which process or thread gets which CPU core, and for how long. Many papers have been written on this; it is hard to be fair, fast and responsive at once. It also prefers to run threads of the same process on the same core, or at least where their caches are warm.
- Goals: throughput (work per second), latency (time from wakeup to running), fairness, and respecting priorities. You cannot maximise all four: long time slices help throughput (fewer switches, warm caches) but hurt latency.

| Algorithm | Idea | Problem |
|---|---|---|
| FCFS | run in arrival order | one long job delays everybody (convoy) |
| SJF / SRTF | shortest job first | needs to know job length; starves long jobs |
| Round-robin | fixed quantum, cycle through the ready queue | quantum too small → switch overhead; too big → latency |
| Priority | highest priority first | starvation unless priorities age |
| Multilevel feedback queue | demote CPU-hungry tasks, promote interactive ones | heuristics, tuning |
| CFS (Linux 2.6.23–6.5) | pick the task with the smallest virtual runtime (red-black tree); weight by nice | no deadline notion for latency-sensitive tasks |
| EEVDF (Linux 6.6+) | CFS weights plus per-task virtual deadlines; picks the eligible task with the earliest deadline | newer, fewer tunables |

```anim
cpu-scheduling-timeline
```

### CPU-bound vs IO-bound workloads
- **CPU-bound** work mostly uses the CPU: encryption (TLS), compression, query planning, sorting, protocol parsing (HTTP/2, QUIC), JSON serialisation. It uses every time slice it gets; the scheduler has to make sure it does not starve, and does not starve others.
- **IO-bound** work mostly waits: database queries, network reads and writes, file reads and writes. It runs briefly and blocks. A fair scheduler rewards it with quick wakeups, because it has used little CPU.
- Consequence for sizing: CPU-bound thread pools ≈ number of cores (more threads only add switches); IO-bound pools can be much larger, or use virtual threads or non-blocking IO ([[executors-and-thread-pools]], [[socket-management]]).

- **Concurrency vs parallelism**: concurrent tasks are in progress at the same time and take turns, like two threads on one core or two hardware threads under hyper-threading; parallel tasks literally run at the same instant on different cores. Concurrency hides waiting, parallelism adds compute.
- Every TLS connection costs CPU on both ends, so "just open another connection" is never free. Even shrinking TLS has a CPU price: certificate compression (RFC 8879) saves several KB per handshake, but the client must decompress, and a malicious server could send a decompression bomb, so it is applied carefully.
- libuv (Node.js) does file IO on a pool of 4 threads by default (`UV_THREADPOOL_SIZE`), because regular file reads block; `io_uring` can make them asynchronous without threads.

### Linux specifics you can observe
- Scheduling classes: `SCHED_OTHER` (normal, CFS/EEVDF), `SCHED_BATCH`, `SCHED_IDLE`, and real-time `SCHED_FIFO`/`SCHED_RR` (fixed priorities 1–99, preempt everything normal; used by audio and some trading systems, dangerous because a spinning RT thread can lock a core).
- `nice` from -20 to 19 maps to a weight: each step is about 1.25× CPU share; nice 0 gets roughly 10× nice 10. It changes the share only under contention; on an idle box a nice 19 process runs at full speed.
- Per-CPU run queues with periodic load balancing and migration; the scheduler prefers to keep a task on the core where its cache is warm. `taskset`/`sched_setaffinity` pins threads to cores.
- The time slice is not fixed: a target latency (`sched_latency`, ~6 ms × log(cores)) is divided among runnable tasks, with a minimum granularity (~0.75 ms). More runnable threads → smaller slices → more switches.

### Load average
- `uptime` / `/proc/loadavg`: exponentially damped averages over 1, 5 and 15 minutes of the number of tasks that are **running or runnable**, plus, on Linux only, tasks in **uninterruptible sleep** (`D`, usually disk or NFS IO).
- Rule of thumb: load ≈ number of cores means saturated; load 8 on 4 cores means on average 4 tasks are waiting for a CPU, or stuck in IO. Compare with `%iowait` and `%steal` in `vmstat`/`mpstat` to tell which.
- A JVM with 200 threads and load 1.5 is fine: blocked threads (`S`) do not count.

### Cgroups and containers
- `cpu.max = 100000 100000` (Kubernetes `limits.cpu: 1`) is a **quota per 100 ms period**, not a core. A multi-threaded JVM can burn its 100 ms in the first 20 ms using 8 threads and then be **throttled** for 80 ms; p99 latency explodes while average CPU looks low. Check `cpu.stat` for `nr_throttled`.
- `cpu.weight` (requests) is proportional sharing like nice, only under contention.
- The JVM reads cgroup limits since JDK 10: `availableProcessors()`, GC thread counts and default pool sizes follow the limit ([[docker-containers-and-resources]], [[kubernetes-basics]]).

### User-level vs kernel-level threads
| Model | Who schedules | Blocking syscall | Examples |
|---|---|---|---|
| 1:1 kernel threads | kernel | blocks only that thread | Linux NPTL, JVM platform threads |
| N:1 user threads (green threads) | user-space library | blocks the whole process | early JVMs, classic coroutine libraries |
| M:N | both: a library multiplexes M user threads onto N kernel threads | the library must intercept blocking calls and park the user thread instead | Go goroutines, Java [[virtual-threads]] (JDK 21) |
- Java platform threads are 1:1: `new Thread().start()` ends in `clone()`, and you can see them with `top -H -p <pid>` or `ls /proc/<pid>/task`. Virtual threads are M:N on top of a `ForkJoinPool` of carrier threads; the JDK unmounts them on blocking IO and `LockSupport.park`, but **pins** them on `synchronized` (fixed in JDK 24) and in native frames.
- Why M:N came back: a kernel thread costs around 1 MiB of reserved stack plus a `task_struct`, and tens of thousands of them make the scheduler slow. A virtual thread's stack is a few hundred bytes to a few KiB on the heap ([[jvm-memory-areas]]).

### Concurrency issues
- With concurrency come challenges: **two threads touching the same variable** (thread safety), or **two processes writing to the same shared memory**.
- `a++` is three steps: load `a` into a register, add 1, store it back. If two threads interleave (T1 loads 1, T2 loads 1, both add, both store 2), one increment is lost: a **race condition**. Each thread kept its own copy in its own registers, saved in its TCB across switches.
- Fixes: make the read-modify-write indivisible with a mutex (`synchronized`, `ReentrantLock`, a process-shared mutex in shared memory) or an atomic instruction (`lock xadd`, compare-and-swap, `AtomicInteger`) ([[race-conditions-and-critical-sections]], [[lock-free-and-cas]]). Locks bring their own problems: contention and deadlock ([[deadlocks-and-synchronization-primitives]]).

- **A mutex has an owner**: only the thread that locked it may unlock it. If that thread dies while holding it, the mutex can stay locked forever and everyone waiting deadlocks. POSIX *robust* mutexes report `EOWNERDEAD` to the next locker instead. In Java, `synchronized` is released automatically even when an exception is thrown; a `ReentrantLock` is released only by `unlock()`, which is why it always goes in `finally`.
- A **semaphore** is just a counter with `wait` (decrement, block at zero) and `signal` (increment); it has no owner, so any thread can signal it. Initialised to 1 it behaves like a mutex without the ownership checks ([[deadlocks-and-synchronization-primitives]]).
- Databases release row locks when the transaction ends, and never take a new lock after releasing one: that is **two-phase locking** (a growing phase, then a shrinking phase at commit or rollback). The lecture calls it two-phase commit, which is a different protocol for committing across several databases.

```anim
race-condition
```

## Example
```bash
# A Spring Boot app: one process, many kernel threads
PID=$(pgrep -f 'esim-provisioning.jar')
ls /proc/$PID/task | wc -l          # number of threads (kernel tasks)
grep -E 'Threads|VmRSS|voluntary' /proc/$PID/status
#   VmRSS:      812 004 kB
#   Threads:  57
#   voluntary_ctxt_switches:    183 214   <- blocked on IO or locks
#   nonvoluntary_ctxt_switches:   1 042   <- preempted: little CPU contention

# Per-thread CPU: which JVM threads are hot?  (nid in a jstack dump = hex TID)
top -H -p $PID -n 1 | head -20
printf '%x\n' 12345                 # convert TID to the nid shown by jstack

# Is the service slow because of CPU scheduling?
mpstat -P ALL 1 3            # %usr %sys %iowait %steal per core
cat /proc/loadavg            # 7.80 6.90 5.10 3/612 41233  -> 3 runnable of 612 tasks
pidstat -w -t -p $PID 1 3    # cswch/s (voluntary)  nvcswch/s (involuntary) per thread

# Container throttling (cgroup v2, inside the pod)
cat /sys/fs/cgroup/cpu.max          # 100000 100000  -> 1 CPU quota
cat /sys/fs/cgroup/cpu.stat
#   nr_periods 48211  nr_throttled 9120  throttled_usec 712003000
# 19% of periods throttled: raise the limit or cap JVM threads
#   (-XX:ActiveProcessorCount, smaller pools), or drop the CPU limit and keep the request.

# Lower the priority of a batch reindex so request threads win under contention
nice -n 15 java -jar reindex.jar
```
A thread dump line like `"kafka-producer-network-thread" ... nid=0x3039` points at kernel task 12345: the same thread seen by the OS and by the JVM. `nr_throttled` growing while the node's `%usr` is far below 100% is the signature of a CPU limit set too tightly for a bursty JVM.

## Pitfalls
- Adding up RSS of forked workers (Postgres backends, gunicorn or nginx workers) to size memory: shared pages are counted once per process. Use Pss from `smaps_rollup` or `smem`.
- Assuming threads are free: each platform thread reserves `-Xss` (1 MiB default) of virtual memory and a kernel structure; a thread-per-request design dies at a few thousand concurrent requests. Use a bounded pool ([[executors-and-thread-pools]]) or virtual threads.
- Forgetting that a process switch costs a TLB flush (or ASID churn): a design that bounces each request through several local processes (sidecars, local proxies) pays it on every hop.
- Measuring only CPU usage: a service with 20% CPU and a huge `cs` rate is bottlenecked on lock or IO waits, not compute. Check `pidstat -w` before adding cores.
- Reading load average as CPU utilisation. Load 12 on 16 cores is healthy; load 4 on 2 cores with 5% CPU means IO wait, not compute.
- Setting Kubernetes CPU limits equal to requests on a JVM "for predictability": GC and JIT compilation are bursty and get throttled right when latency matters. Many teams set requests and no limits, or limits 2–4× requests.
- Sizing thread pools by `Runtime.availableProcessors()` in a host-mode container without limits: you get the host's 64 cores and a 64-thread GC on a pod meant to use 2.
- Using `Thread.setPriority()` in Java and expecting an effect: on Linux the JVM ignores it unless started with `-XX:ThreadPriorityPolicy=1` as root. Use `nice`/cgroups at the OS level.
- Real-time scheduling "for low latency": a `SCHED_FIFO` thread that never blocks makes the core unusable, including for SSH.
- `ps` shows the process as `R` while every JVM thread except GC may be parked: a process's state is a summary of its threads.
- Related: [[anatomy-of-a-process]] for one process's layout, [[cpu]] for what runs the context, [[memory-management]] for page tables, fork and copy-on-write, [[signals-and-process-lifecycle]] for fork/exec, zombies and graceful shutdown, [[linux-processes]] for the commands.
