---
title: CPU scheduling: preemption, priorities and load
tags: [operating-systems, scheduling, linux, performance]
learned: 2026-10-27
---
## TL;DR
The scheduler decides which runnable thread gets a core and for how long. Modern general-purpose kernels are **preemptive** (a thread is interrupted by a timer or a higher-priority wakeup) and **fair** (Linux CFS and its EEVDF successor give each runnable task a share of CPU weighted by `nice`). "Load average" counts tasks that want a CPU (plus tasks in uninterruptible disk wait on Linux), not CPU percent. For a backend developer the practical questions are: why does latency spike when the box is busy, what do `nice` and cgroup CPU quotas actually do, and why a container with `cpu: 1` gets throttled.

## Key points

### Goals and the basic trade-off
- Throughput (work per second), latency (time from wakeup to running), fairness, and respecting priorities. You cannot maximise all four: long time slices help throughput (fewer context switches, warm caches) but hurt latency.
- **Preemptive**: the kernel can take the CPU away at a timer tick or when a higher-priority task wakes. **Cooperative**: the task runs until it yields. Kernels are preemptive; many user-space schedulers (Java [[virtual-threads]], JavaScript event loops, Go before 1.14) are cooperative: a virtual thread that spins in a CPU loop blocks its carrier and starves other virtual threads.
- The wakeup path matters more than the time slice for servers: a request thread is usually blocked on a socket, wakes when data arrives, runs a few hundred microseconds, blocks again. Scheduling latency = time between the wakeup and actually running.

### Classic algorithms (interview vocabulary)
| Algorithm | Idea | Problem |
|---|---|---|
| FCFS | run in arrival order | one long job delays everybody (convoy) |
| SJF / SRTF | shortest job first | needs to know job length; starves long jobs |
| Round-robin | fixed quantum, cycle through ready queue | quantum too small → switch overhead; too big → latency |
| Priority | highest priority first | starvation unless priorities age |
| Multilevel feedback queue | demote CPU-hungry tasks, promote interactive ones | heuristics, tuning |
| CFS (Linux 2.6.23–6.5) | pick the task with the smallest virtual runtime (red-black tree); weight by nice | no deadline notion for latency-sensitive tasks |
| EEVDF (Linux 6.6+) | CFS weights plus per-task virtual deadlines; picks the eligible task with the earliest deadline | newer, fewer tunables |

### Linux specifics you can observe
- Scheduling classes: `SCHED_OTHER` (normal, CFS/EEVDF), `SCHED_BATCH`, `SCHED_IDLE`, and real-time `SCHED_FIFO`/`SCHED_RR` (fixed priorities 1–99, preempt everything normal; used by audio and some trading systems, dangerous because a spinning RT thread can lock a core).
- `nice` from -20 to 19 maps to a weight: each step is about 1.25× CPU share; nice 0 gets roughly 10× nice 10. It changes the share under contention only; on an idle box a nice 19 process runs at full speed.
- Per-CPU run queues with periodic load balancing and migration; the scheduler prefers to keep a task on the core where its cache is warm ([[cpu-caches-and-cache-lines]]). `taskset`/`sched_setaffinity` pins threads to cores.
- Time slice is not fixed: target latency (`sched_latency`, ~6 ms × log(cores)) divided among runnable tasks, with a minimum granularity (~0.75 ms). More runnable threads → smaller slices → more switches ([[processes-and-threads]]).

### Load average
- `uptime` / `/proc/loadavg`: exponentially damped averages over 1, 5 and 15 minutes of the number of tasks that are **running or runnable**, plus, on Linux only, tasks in **uninterruptible sleep** (`D`, usually disk or NFS IO).
- Rule of thumb: load ≈ number of cores means saturated; load 8 on 4 cores means on average 4 tasks are waiting for a CPU, or stuck in IO. Always compare with `%iowait` and `%steal` in `vmstat`/`mpstat` to tell which.
- A JVM with 200 threads and load 1.5 is fine: blocked threads (`S`) do not count.

### Cgroups and containers
- `cpu.max = 100000 100000` (Kubernetes `limits.cpu: 1`) is a **quota per 100 ms period**, not a core. A multi-threaded JVM can burn its 100 ms in the first 20 ms using 8 threads and then be **throttled** for 80 ms; p99 latency explodes while average CPU looks low. Check `cpu.stat` for `nr_throttled`.
- `cpu.weight` (requests) is proportional sharing like nice, only under contention.
- The JVM reads cgroup limits since JDK 10: `availableProcessors()`, GC thread counts and default pool sizes follow the limit ([[docker-containers-and-resources]], [[kubernetes-basics]]).

## Example
```bash
# Is the service slow because of CPU scheduling?
mpstat -P ALL 1 3            # %usr %sys %iowait %steal per core
cat /proc/loadavg            # 7.80 6.90 5.10 3/612 41233  -> 3 runnable of 612 tasks
pidstat -w -t -p $(pgrep -f esim-provisioning.jar) 1 3
#   cswch/s (voluntary, blocking)  nvcswch/s (involuntary, preempted)

# Container throttling (cgroup v2, inside the pod)
cat /sys/fs/cgroup/cpu.max          # 100000 100000  -> 1 CPU quota
cat /sys/fs/cgroup/cpu.stat
#   nr_periods 48211  nr_throttled 9120  throttled_usec 712003000
# 19% of periods throttled: raise the limit or cap JVM threads
#   (-XX:ActiveProcessorCount, smaller pools), or drop the CPU limit and keep the request.

# Lower the priority of a batch reindex so request threads win under contention
nice -n 15 java -jar reindex.jar
```
`nr_throttled` growing while `%usr` of the node is far below 100% is the signature of a CPU limit set too tightly for a bursty JVM.

## Pitfalls
- Reading load average as CPU utilisation. Load 12 on 16 cores is healthy; load 4 on 2 cores with 5% CPU means IO wait, not compute.
- Setting Kubernetes CPU limits equal to requests on a JVM "for predictability": GC and JIT compilation are bursty and get throttled right when latency matters. Many teams set requests and no limits, or limits 2–4× requests.
- Sizing thread pools by `Runtime.availableProcessors()` on a host-mode container without limits: you get the host's 64 cores, and a 64-thread GC on a pod that is really meant to use 2 cores ([[executors-and-thread-pools]]).
- Using `Thread.setPriority()` in Java and expecting an effect: on Linux the JVM ignores it unless started with `-XX:ThreadPriorityPolicy=1` as root. Use `nice`/cgroups at the OS level.
- Real-time scheduling for "low latency": a `SCHED_FIFO` thread that never blocks makes the core unusable, including for SSH.
- Related: [[processes-and-threads]] for context switch cost, [[linux-processes]] for `nice`, `renice`, `ps` states and `systemd` CPU limits.
