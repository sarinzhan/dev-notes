---
title: Virtual memory and paging
tags: [operating-systems, memory, paging, linux]
learned: 2026-10-27
---
## TL;DR
Every process runs in its own **virtual address space**; the CPU's MMU translates virtual addresses to physical frames through per-process **page tables**, with the **TLB** caching recent translations. Pages are mapped lazily on first touch (a **page fault**), which is why reserved memory (`VmSize`) is much larger than resident memory (`RSS`). When physical RAM runs out the kernel reclaims page cache, swaps anonymous pages, and as a last resort the **OOM killer** terminates a process. Linux tooling (`free`, `/proc/meminfo`, swappiness, page cache) is in [[linux-memory-filesystems-io]]; this note covers the mechanism and what it means for a JVM service.

## Key points

### Why virtual memory
- **Isolation**: a process cannot address another process's memory; an invalid access raises `SIGSEGV` instead of corrupting a neighbour.
- **Illusion of contiguity**: the heap looks like one contiguous range even though physical frames are scattered and some are not resident at all.
- **Sharing**: `libc.so` and the JVM's `libjvm.so` are mapped once in RAM and appear in every process; `fork()` shares all pages copy-on-write ([[linux-processes]]).
- **Overcommit**: the kernel hands out more virtual memory than there is RAM, betting that not all of it is touched (`vm.overcommit_memory`).

### Translation mechanics
- Page size 4 KiB on x86-64 (16 KiB on Apple silicon, 64 KiB on some ARM servers). A virtual address is split into page-table indices plus a 12-bit offset within the page.
- x86-64 uses a 4-level (5-level with 57-bit addressing) radix tree of page tables. A full walk is four dependent memory reads, so the **TLB** (a few thousand entries per core) caches translations. A TLB miss on a random-access workload can cost more than the data access itself.
- **Huge pages** (2 MiB, 1 GiB) cut TLB misses by 512×; the JVM can use them with `-XX:+UseTransparentHugePages` or `-XX:+UseLargePages`. Transparent huge pages can also cause latency spikes from compaction (`khugepaged`), which is why databases such as Redis and MongoDB recommend disabling THP.
- Each page-table entry carries flags: present, writable, executable (NX), user/supervisor, accessed, dirty. The kernel uses accessed/dirty bits for reclaim decisions.

### Page faults
| Kind | Cause | Cost |
|---|---|---|
| Minor (soft) | page is in RAM but not mapped yet: first touch of `malloc`/`mmap` memory, copy-on-write break, page-cache hit | ~1 µs, no disk |
| Major (hard) | page must be read from disk: swapped-out page or a file page not in cache | ms (disk) |
| Invalid | address not mapped at all, or permission violation | `SIGSEGV`; in the JVM, a crash log (`hs_err_pid`) or, for a null check, a `NullPointerException` (HotSpot deliberately uses the fault on page 0) |
- `-Xms` equal to `-Xmx` plus `-XX:+AlwaysPreTouch` pays all minor faults at startup instead of in the first requests; worth it for latency-sensitive services ([[jvm-memory-areas]]).
- `ps -o min_flt,maj_flt` or `/proc/<pid>/stat` show fault counts; a growing `maj_flt` on a server means it is swapping or re-reading evicted files.

### Reclaim, swap and the OOM killer
- Reclaim order under pressure: clean page cache (just drop), dirty page cache (write back then drop), anonymous pages (heap, stacks) to swap. `vm.swappiness` biases between the last two.
- Swapping a JVM is catastrophic because a GC cycle touches the whole heap: a 4 GiB heap half on disk turns a 50 ms pause into minutes. Production JVM hosts run with little or no swap and rely on correct sizing.
- The **OOM killer** runs when reclaim cannot free a page. It picks the task with the highest `oom_score` (roughly RSS, adjusted by `oom_score_adj`), sends `SIGKILL`; in a container with `memory.max` the same happens inside the cgroup and the pod shows exit code 137 / `OOMKilled` ([[docker-containers-and-resources]]).
- `memory.max` counts RSS **plus page cache** charged to the cgroup; a service that streams big files can be killed for cache it does not control unless the kernel can reclaim it in time. Kubernetes `limits.memory` must leave room for heap, metaspace, thread stacks, direct buffers and code cache, not just `-Xmx`.

### Memory-mapped files
- `mmap()` maps a file into the address space: reads become page faults served from page cache, writes become dirty pages flushed later. Kafka index files and Lucene use it; Java exposes it as `MappedByteBuffer` ([[java-nio]]). Zero-copy `sendfile()` moves page-cache pages straight to a socket, which is Kafka's consumer fetch path ([[kafka-architecture]]).

## Example
```bash
PID=$(pgrep -f esim-provisioning.jar)
grep -E 'VmSize|VmRSS|RssAnon|RssFile|VmSwap' /proc/$PID/status
#   VmSize:  6 402 112 kB   reserved (heap -Xmx + stacks + code cache + mmap)
#   VmRSS:   1 213 400 kB   resident now
#   RssAnon: 1 150 200 kB   heap, metaspace, stacks
#   RssFile:    63 200 kB   mapped jars and libraries, shared with other JVMs
#   VmSwap:          0 kB   anything else here is a red flag for a JVM

# Page faults per second while a load test runs
perf stat -e page-faults,major-faults -p $PID -- sleep 10

# What killed the pod?
dmesg -T | grep -iE 'oom|killed process' | tail -3
#   Out of memory: Killed process 41233 (java) total-vm:6402112kB, anon-rss:3980120kB
#   -> anon RSS hit the cgroup limit; heap + native exceeded limits.memory
```
`anon-rss` close to `limits.memory` with `-Xmx` set to the same number means native memory (metaspace, threads, direct buffers) had no room: set `-XX:MaxRAMPercentage=70` or lower `-Xmx` instead of raising the limit blindly.

## Pitfalls
- Setting `-Xmx` to the container memory limit. The JVM needs 20–40% beyond the heap; the OOM killer does not throw `OutOfMemoryError`, it sends `SIGKILL` with no stack trace.
- Judging memory by `VmSize`/`VSZ`: it is address space, not RAM. Look at `RSS`, and for a cgroup at `memory.current` minus reclaimable `file` pages in `memory.stat`.
- Enabling swap "as a safety net" on a database or JVM host: it converts a fast failure into a slow, hard-to-diagnose meltdown with major faults in every GC.
- Transparent huge pages set to `always` on a Redis/Postgres box: latency spikes and memory bloat from compaction; use `madvise` and let the JVM opt in.
- Leaving `oom_score_adj` default for a sidecar: the kernel may kill the main JVM (bigger RSS) instead of the leaking log shipper.
- Related: [[stack-vs-heap-memory]] for how allocation sits on top of pages, [[cpu-caches-and-cache-lines]] for the layer below the TLB.
