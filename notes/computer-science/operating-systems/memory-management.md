---
title: Memory management (RAM)
tags: [operating-systems, memory, ram, virtual-memory, paging, dma, linux]
learned: 2026-10-28
order: 4
---
## TL;DR
RAM is the volatile working memory of the machine: the CPU reads and writes it in 64-byte cache lines through its caches and memory controller, about 100 times slower than L1. Programs never use physical addresses directly: every process gets its own **virtual address space**, cut into 4 KiB pages that a per-process **page table** maps to physical frames. That one mechanism gives isolation, removes external fragmentation, lets processes share code and libraries, makes `fork` cheap through **copy-on-write** (which is how Redis takes snapshots), and lets the kernel swap out idle pages. Its price is translation work (TLB misses, page faults). Devices move data with **DMA**, straight into physical RAM without the CPU copying each byte; `O_DIRECT` uses it to skip the page cache.

## Key points
### What RAM is
- **Random-access, byte-addressable, volatile** memory: any byte can be read in about the same time, and everything is lost when the power goes. It holds the code, data, heap and stacks of every running process and the kernel's own structures, including page tables.
- Main memory is **DRAM**: each bit is a tiny capacitor plus a transistor. Capacitors leak, so every row is refreshed about every 64 ms, and reading a row drains it, so it is written back. That is why DRAM is dense and cheap but slow compared with the CPU.
- The CPU's caches are **SRAM**: six transistors per bit, no refresh, roughly 10× faster, but much bigger and more expensive per bit. That is why L1 is tens of KB and RAM is tens of GB ([[cpu-caches-and-cache-lines]]).
- A memory module (DIMM) is organised into ranks, **banks**, **rows** and columns. A bank can have one row open at a time in its **row buffer**; reading from the open row is fast, opening another row costs extra.

| Type | Where | Key property |
|---|---|---|
| SRAM | CPU caches (L1–L3), registers' neighbours | fast, no refresh, expensive, small |
| DRAM / SDRAM | main memory | dense and cheap; SDRAM is clocked in sync with the memory bus |
| DDR4, DDR5 | desktop and server DIMMs | "double data rate": transfers on both clock edges. DDR5 splits each module into two independent 32-bit channels and has higher bandwidth |
| LPDDR | phones, laptops | low-power DDR, soldered next to the CPU |
| HBM | GPUs, AI accelerators | stacked DRAM dies with a very wide bus: huge bandwidth |
| ECC (RDIMM) | servers | extra bits detect and correct single-bit errors; "registered" buffers allow more modules per channel |

### How the CPU reads and writes RAM
- A load or store instruction carries a **virtual** address. The **MMU** translates it to a physical address (using the TLB), then the caches are checked: L1 (~1 ns), L2 (~4 ns), L3 (~12–40 ns).
- On a miss everywhere, the **memory controller** (on the CPU die) asks the DIMM: *activate* the row into the row buffer, then *read* the columns. The data comes back as a **64-byte burst**: one whole cache line, never a single byte. The line is placed in the caches and the requested bytes go into a register. Total: about 100 ns, or roughly 300 cycles of waiting.
- A **store** goes into the L1 line and marks it **dirty** (write-back cache). RAM is updated only when the line is evicted. Other cores still see the new value, because the cache coherence protocol (MESI) moves the line between caches.
- Consequence for code: memory is fast when access is **sequential** (whole lines are used, the hardware prefetcher runs ahead) and slow when it jumps around (one line fetched per value). An array of primitives beats a linked list of objects for the same reason.

```anim
ram-read-write
```

### Alignment
- Data types are **aligned to their size**: a 1-byte `char` can be anywhere, a 4-byte `int` sits at an address divisible by 4, an 8-byte `long` or pointer at one divisible by 8.
- Why: an aligned value never crosses a cache line or a page, so it is read in one access and can be read **atomically**. A misaligned 8-byte value at `0x...7C` spans two lines: two fetches, no atomicity. x86 tolerates it with a penalty; some ARM instructions and most atomic operations fault.
- The compiler inserts **padding** to keep fields aligned, so field order changes the size of a struct:

```c
struct a { char c; int i; char d; };   // 1 + 3 pad + 4 + 1 + 3 pad = 12 bytes
struct b { int i; char c; char d; };   // 4 + 1 + 1 + 2 pad          =  8 bytes
```

- The JVM does the same: fields are laid out to keep them aligned, and every object starts at an 8-byte boundary. That alignment is what makes **compressed oops** work: a 32-bit reference counts in 8-byte units, so it can address a 32 GB heap. Above `-Xmx32g` references become 64-bit and the same data takes more memory ([[jvm-memory-areas]]).
- `O_DIRECT` and DMA buffers must be aligned to the block size (512 B or 4 KiB); see below.

### Limits of physical memory without virtual memory
If processes used physical addresses directly:
- **Isolation**: every address is real, so a buggy or malicious process can read or overwrite another process's memory, or the kernel's.
- **Relocation**: a program has to know where in RAM it was loaded; two copies of the same program cannot both use address 4,000.
- **Size**: all running programs together must fit in RAM, and a program larger than RAM cannot run at all.
- **Fragmentation**: each process needs one contiguous block, and as processes start and exit, free memory breaks into holes.
- **Duplication**: five processes of the same program hold five copies of the same code.

### External vs internal fragmentation
| | External | Internal |
|---|---|---|
| What | free memory is split into holes, none big enough for the request, although the total would be | an allocation is bigger than what was asked for; the extra space inside it is wasted |
| Cause | variable-size contiguous allocations that come and go | allocation in fixed units: whole pages, allocator size classes |
| Example | 12 KB free as 8 KB + 4 KB, and a process needs 12 KB in one piece | a 9 KB region gets three 4 KB pages; 3 KB of the last page is unused |
| Fix | compaction (move blocks, very expensive), or paging | smaller units (more bookkeeping), accept the waste |

- Paging trades the bad kind for the cheap kind: any free frame fits any page, so external fragmentation of **physical** memory disappears; the cost is internal fragmentation of half a page per region on average.
- Fragmentation still exists **inside** a process's virtual space: `malloc` arenas and the Java heap can have free memory in holes. Compacting garbage collectors (G1, ZGC) move live objects together to remove it; G1's "humongous" objects need contiguous regions and are a typical source of trouble ([[garbage-collection]]).

```anim
memory-fragmentation
```

### Virtual memory and page tables
- Each process gets a **full virtual address space** (47 usable bits on x86-64, 128 TiB), and most of it is not mapped. It is cut into **pages** (4 KiB on x86-64, 16 KiB on Apple silicon, 64 KiB on some ARM servers).
- A per-process **page table** maps each virtual page to a physical **frame**. The table lives in RAM; the CPU holds a pointer to the current process's table in the **page table base register** (CR3 on x86, TTBR on ARM), and a context switch reloads it.
- A virtual address is split into a page number and an offset: the page number is looked up, the offset is kept. Real tables store frame *numbers* and flags (present, writable, executable/NX, user/kernel, accessed, dirty), not full addresses.
- On x86-64 the table is a 4-level tree (5-level with 57-bit addresses), so a full walk is four dependent memory reads. The **TLB** caches recent translations (a few thousand entries per core); a TLB miss on a random-access workload can cost more than the data access itself.
- **Huge pages** (2 MiB, 1 GiB) cover 512× more memory per TLB entry. The JVM can use them with `-XX:+UseLargePages` or `-XX:+UseTransparentHugePages`; Postgres with `huge_pages=try` for its shared memory.
- Overview first, then the details step by step:

```anim
virtual-memory-mapping
```

```anim
vm-page-tables
```

### Page faults
| Kind | Cause | Cost |
|---|---|---|
| Minor (soft) | page is in RAM but not mapped yet: first touch of `malloc`/`mmap` memory, copy-on-write break, page-cache hit | ~1 µs, no disk |
| Major (hard) | page must be read from disk: swapped-out page or a file page not in cache | ms (disk) |
| Invalid | address not mapped at all, or permission violation | `SIGSEGV`; in the JVM, a crash log (`hs_err_pid`) or, for a null check, a `NullPointerException` (HotSpot deliberately uses the fault on page 0) |
- Memory is allocated lazily: `malloc` and `mmap` only reserve address space, and a physical page is attached on first touch. That is why a JVM with `-Xmx4g` shows 4 GiB virtual (`VSZ`) and much less resident (`RSS`) until the heap is actually used.
- `-Xms` equal to `-Xmx` plus `-XX:+AlwaysPreTouch` pays all minor faults at startup instead of in the first requests; worth it for latency-sensitive services ([[jvm-memory-areas]]).
- `ps -o min_flt,maj_flt` or `/proc/<pid>/stat` show fault counts; a growing `maj_flt` on a server means it is swapping or re-reading evicted files.

### Shared memory and shared libraries
- Because a mapping is just a table entry, two page tables can point to the **same frame**. The kernel loads a program's code or a library once and maps it into every process that uses it.
- **Shared libraries**: `libc.so`, `libjvm.so`, `libssl.so` are mapped read-only into hundreds of processes and occupy RAM once. `/proc/<pid>/maps` lists every mapping of a process, with the file it comes from (see the example below).
- **Explicit shared memory**: `mmap(MAP_SHARED)` or `shm_open` maps the same pages into two processes, so communication is a plain memory write with no system call. No synchronisation comes with it ([[ipc-mechanisms]]).
- Use cases:
  - **Threads** share the whole address space by definition: that is what makes them threads ([[process-management]]).
  - **Postgres**: every backend process attaches to `shared_buffers`, WAL buffers and lock tables in one shared segment ([[postgres-performance-checklist]]).
  - **NGINX** and proxies: worker processes share cache metadata, rate-limit counters and upstream state in shared memory zones.
  - Java: `FileChannel.map` on a file in `/dev/shm` gives a `MappedByteBuffer` shared across processes (Chronicle Queue, Aeron).
  - **fork** with copy-on-write, below.

### Copy-on-write and fork
- `fork()` gives the child a **copy of the parent's page table**, not of its memory. Every writable page is marked read-only in both tables, and both point to the same frames.
- The first write to such a page, by either process, causes a page fault. The kernel copies that one page to a new frame, points the writer's table at the copy and makes it writable. Pages nobody writes to stay shared forever.
- So `fork` of a process with a 10 GB heap is fast (it copies page tables, about 20 MB for 10 GB of 4 KiB pages), and memory grows only with the pages actually written afterwards. `exec` right after `fork` throws the shared pages away without ever copying them ([[linux-processes]], [[signals-and-process-lifecycle]]).

### How Redis uses copy-on-write
- Redis keeps the whole dataset in memory and has to save it to disk (an RDB snapshot, or an AOF rewrite) while it keeps serving writes. It uses `fork()` as a snapshot mechanism:
  - `BGSAVE` forks. The child sees memory exactly as it was at the moment of the fork and writes it to `dump.rdb` at its own pace.
  - The parent keeps handling commands. Each write to a page that is still shared triggers copy-on-write, so the parent changes its own copy and the child's view stays frozen.
  - No locks and no special data structures: the kernel's page tables provide the consistent snapshot ([[redis-patterns-and-persistence]]).
- Costs to plan for:
  - **The fork itself blocks Redis** while the page tables are copied: milliseconds per GB, visible as `latest_fork_usec` in `INFO stats`. On a 50 GB instance it can be a noticeable latency spike.
  - **Memory overhead** = pages modified during the save. A read-mostly cache barely grows; a write-heavy instance can approach **2×** its dataset. Redis logs `RDB: N MB of memory used by copy-on-write` and reports `rdb_last_cow_size`.
  - **Transparent huge pages** make it worse: with 2 MiB pages, one small write copies 2 MiB instead of 4 KiB. Redis warns at startup and asks for THP to be disabled.
  - **Overcommit**: with `vm.overcommit_memory=0` the kernel may refuse a fork that *could* need twice the memory ("Can't save in background: fork: Cannot allocate memory"). Redis recommends `vm.overcommit_memory=1`.
  - Leave RAM headroom beyond `maxmemory`, or the copy-on-write growth ends with the OOM killer.

```anim
redis-cow
```

### Swap, reclaim and the OOM killer
- When RAM runs short, the kernel reclaims in this order: clean page cache (just drop it), dirty page cache (write back, then drop), then anonymous pages (heap, stacks) written to **swap** on disk. `vm.swappiness` biases between the last two. A swapped page keeps its virtual address; its table entry is marked not present, and the next touch is a major page fault.
- Swapping a JVM is catastrophic because a GC cycle touches the whole heap: a 4 GiB heap half on disk turns a 50 ms pause into minutes. Production JVM and database hosts run with little or no swap (`vm.swappiness=1`) and rely on correct sizing.
- The **OOM killer** runs when reclaim cannot free a page. It picks the task with the highest `oom_score` (roughly RSS, adjusted by `oom_score_adj`) and sends `SIGKILL`. In a container with `memory.max` the same happens inside the cgroup, and the pod shows exit code 137 / `OOMKilled` ([[docker-containers-and-resources]]).
- `memory.max` counts RSS **plus page cache** charged to the cgroup; a service that streams big files can be killed for cache it does not control unless the kernel can reclaim it in time. Kubernetes `limits.memory` must leave room for heap, metaspace, thread stacks, direct buffers and code cache, not just `-Xmx`.
- **Page cache**: file contents read or written stay in RAM as cache; `free -h` shows them under `buff/cache`, and they count as *available*. Writes go to the cache first (dirty pages) and are flushed later; `fsync()` forces it, which is what Postgres does at commit and Kafka deliberately does not ([[kafka-architecture]]).
- **Overcommit**: the kernel hands out more virtual memory than there is RAM, betting that not all of it will be touched (`vm.overcommit_memory`).

### Memory-mapped files
- `mmap()` maps a file into the address space: reads become page faults served from the page cache, writes become dirty pages flushed later. Kafka index files and Lucene use it; Java exposes it as `MappedByteBuffer` ([[java-nio]]).
- Zero-copy `sendfile()` moves page-cache pages straight to a socket without copying them into user space, which is Kafka's consumer fetch path ([[kafka-architecture]]).

### Limitations of virtual memory
| Cost | Why |
|---|---|
| Extra translation | the CPU cannot use a virtual address directly; every access needs a translation, and a TLB miss adds a page-table walk of up to four memory reads |
| Page tables to maintain | they take RAM (a few MB per GB mapped), must be copied on `fork`, updated on every `mmap`/`munmap` |
| Page faults | each one is a switch into the kernel; a major one waits for the disk |
| More complex CPU | the MMU and TLB are hardware that every memory access goes through |
| TLB misses and flushes | a process context switch invalidates translations (PCID/ASID tags reduce this); workloads with a big, random working set spend a lot of time in page walks. Database performance regressions have been traced to more TLB misses (the course's example is MySQL 8.x vs 5.x); huge pages are the usual fix |

### DMA: devices writing straight to RAM
- Without DMA, data from a network card or disk passes through the CPU: it reads each word from the device and stores it in RAM (**programmed I/O**). Fine for a keyboard, far too slow for a 10 Gbit/s NIC or an NVMe drive.
- With **DMA** (direct memory access), the driver gives the device or a DMA engine a descriptor (destination physical address, length). The device writes into RAM by itself and raises an **interrupt** when it is done; the CPU runs other threads meanwhile. Modern NICs and NVMe drives are *bus masters* with their own DMA engines and read descriptors from ring buffers in RAM.
- Constraints:
  - DMA uses **physical addresses**; the device knows nothing of virtual memory and often has no MMU of its own.
  - The buffer must be **pinned**: the kernel must not swap it out or move it during the transfer. Kernel-allocated DMA memory is never swapped.
  - A large buffer that is not physically contiguous is described as a **scatter-gather** list of frames.
  - An **IOMMU** gives devices their own address translation: it lets the kernel hand a device virtual-looking addresses, restricts which RAM each device can touch (protection from malicious devices, for example over Thunderbolt), and is what makes passing a device into a VM safe.

| Pros | Cons |
|---|---|
| Efficient bulk transfers at full bus speed | Setup and initialisation cost per transfer |
| Much less CPU overhead: the CPU does not copy | Security risk: a device can write anywhere unless an IOMMU restricts it |
| Data lands in RAM without the CPU or its caches touching it | Complexity: pinning, alignment, cache coherence of the buffer |
| | Not worth it for tiny, interrupt-driven input (keyboard, mouse): the CPU copying a few bytes is faster |

- Java: `ByteBuffer.allocateDirect` is off-heap memory that does not move, so the OS can DMA into it directly; a heap `byte[]` can be moved by the GC, so the JDK copies it into a temporary direct buffer first ([[java-nio]]).

```anim
dma-transfer
```

### O_DIRECT: bypassing the page cache
- A normal `read()` goes disk → DMA → **page cache** → copy → your buffer. Opening a file with `O_DIRECT` makes the DMA transfer go straight into the user-space buffer, skipping the page cache.
- Databases that run their own cache use it to avoid **double buffering** (the same page held once in the database's buffer pool and again in the kernel's cache): MySQL InnoDB with `innodb_flush_method=O_DIRECT`, Oracle, ScyllaDB. Postgres, by contrast, relies on the page cache, which is why its `shared_buffers` is usually only about 25% of RAM.
- Requirements: the buffer address, the file offset and the length must all be aligned to the logical block size (usually 512 B or 4 KiB), or the call fails with `EINVAL`. You also lose the kernel's read-ahead and write-behind, so it only pays off when the application caches and schedules I/O better than the kernel would ([[io-models-blocking-nonblocking-epoll]]).
- `O_DIRECT` does not mean durable: data may still sit in the device's write cache. Use `fsync`/`fdatasync` or `O_DSYNC` for durability.

## Example
```bash
PID=$(pgrep -f esim-provisioning.jar)
grep -E 'VmSize|VmRSS|RssAnon|RssFile|VmSwap' /proc/$PID/status
#   VmSize:  6 402 112 kB   reserved (heap -Xmx + stacks + code cache + mmap)
#   VmRSS:   1 213 400 kB   resident now
#   RssAnon: 1 150 200 kB   heap, metaspace, stacks
#   RssFile:    63 200 kB   mapped jars and libraries, shared with other JVMs
#   VmSwap:          0 kB   anything else here is a red flag for a JVM

# Every mapping of the process: address range, permissions, offset, file
grep -E 'libc|libjvm|heap|stack' /proc/$PID/maps
#   7f3a1c000000-7f3a1c028000 r--p 00000000 08:02 1180  /usr/lib/x86_64-linux-gnu/libc.so.6   <- shared with every process
#   7f3a1d200000-7f3a1e1c5000 r-xp 00200000 08:02 2241  /opt/java/lib/server/libjvm.so
#   7ffd8a3f1000-7ffd8a412000 rw-p 00000000 00:00 0     [stack]

# Page faults per second while a load test runs
perf stat -e page-faults,major-faults -p $PID -- sleep 10

# Redis: what did the last background save cost?
redis-cli INFO persistence | grep -E 'rdb_last_cow_size|rdb_last_bgsave_status'
redis-cli INFO stats | grep latest_fork_usec
#   rdb_last_cow_size:1342177280   <- 1.25 GB copied during the save
#   latest_fork_usec:84210         <- Redis was blocked 84 ms by fork()

# What killed the pod?
dmesg -T | grep -iE 'oom|killed process' | tail -3
#   Out of memory: Killed process 41233 (java) total-vm:6402112kB, anon-rss:3980120kB
#   -> anon RSS hit the cgroup limit; heap + native exceeded limits.memory
```
`anon-rss` close to `limits.memory` with `-Xmx` set to the same number means native memory (metaspace, threads, direct buffers) had no room: set `-XX:MaxRAMPercentage=70` or lower `-Xmx` instead of raising the limit blindly.

## Pitfalls
- Setting `-Xmx` to the container memory limit. The JVM needs 20–40% beyond the heap; the OOM killer does not throw `OutOfMemoryError`, it sends `SIGKILL` with no stack trace.
- Judging memory by `VmSize`/`VSZ`: it is address space, not RAM. Look at `RSS`, and for a cgroup at `memory.current` minus reclaimable `file` pages in `memory.stat`.
- Reading the "free" column of `free` and concluding memory is exhausted; look at `available`, which includes reclaimable page cache.
- Enabling swap "as a safety net" on a database or JVM host: it converts a fast failure into a slow, hard-to-diagnose meltdown with major faults in every GC.
- Transparent huge pages set to `always` on a Redis or Postgres box: latency spikes from compaction and memory bloat from copy-on-write of 2 MiB pages; use `madvise` and let the JVM opt in.
- Sizing a Redis host for the dataset only: `BGSAVE` and AOF rewrites need copy-on-write headroom, up to the size of the dataset on a write-heavy instance.
- Leaving `oom_score_adj` at its default for a sidecar: the kernel may kill the main JVM (bigger RSS) instead of the leaking log shipper.
- Using `O_DIRECT` with an unaligned buffer (`EINVAL`), or expecting it to make writes durable without `fsync`.
- Default `vm.max_map_count` (65530) is too low for Elasticsearch and some JVM workloads, which create many mappings; raise it in `/etc/sysctl.d/`.
- Related: [[anatomy-of-a-process]] for the layout of one address space, [[stack-vs-heap-memory]] for how allocation sits on top of pages, [[cpu-caches-and-cache-lines]] for the layer below the TLB, [[linux-memory-filesystems-io]] for the Linux commands.
