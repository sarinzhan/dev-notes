---
title: System architecture overview
tags: [operating-systems, kernel, user-space, interrupts, architecture]
learned: 2026-10-27
order: 2
---
## TL;DR
The OS exists to manage scarce resources. A computer is four kinds of hardware: a **CPU** that executes machine instructions, **memory** (RAM) that holds running programs and their data, **storage** (SSD, HDD) that keeps data when the power is off, and a **network** card that talks to other machines. The **kernel** is the part of the OS that owns all four. It schedules processes onto CPU cores, gives each process its own virtual memory, turns disk blocks into files, drives devices through drivers, and runs the network stack. Programs live in **user space** and can reach the kernel only through **system calls**; the kernel is also entered by device **interrupts** and CPU **exceptions**. Everything a backend service does, from a SQL query to an HTTP response, is a walk down and back up through these layers.

## Key points
### The big picture
- Every one of these resources is **limited**, and many programs want it at the same time: cores, gigabytes of RAM, the IOPS of a disk, the bandwidth of a network card. Scarce resources with high demand need a manager, and that manager is the kernel. This note is the map; each resource has its own deeper note further down the list ([[cpu]], [[memory-management]], [[process-management]], [[storage-management]], [[socket-management]]).

```anim
os-layers
```

### CPU
- Executes **machine instructions**, one small operation each. The main kinds:

| Type | x86-64 examples | What it does |
|---|---|---|
| Data movement | `mov`, `push`, `pop` | Copy between registers and memory |
| Arithmetic and logic | `add`, `sub`, `imul`, `and`, `xor`, `shl` | Compute on registers |
| Comparison | `cmp`, `test` | Set flags for a later jump |
| Control flow | `jmp`, `je`/`jne`, `call`, `ret` | Change the program counter |
| Floating point and SIMD | `addsd`, `vaddps` | Math on doubles; one instruction over many values |
| Atomic | `lock cmpxchg`, `lock xadd` | Read-modify-write that no other core can interleave; the basis of `AtomicInteger` and every lock |
| Privileged / system | `syscall`, `hlt`, `in`/`out`, `mov cr3` | Talk to hardware or switch modes; all but `syscall` work only in kernel mode |

- **Cores**: a chip has several independent cores; each runs one thread at a time (two with hyper-threading), with its own registers and its own L1/L2 cache. The kernel decides which thread runs on which core ([[cpu]]).
- **Cache**: small, fast memory on the chip that keeps recently used data close to the core. Data moves in 64-byte **cache lines**.

| Level | Size (typical server) | Latency | Shared? |
|---|---|---|---|
| Registers | ~1 KB | < 1 ns | per core |
| L1 | 32–64 KB | ~1 ns | per core |
| L2 | 0.5–2 MB | ~3–5 ns | per core |
| L3 | 16–100+ MB | ~10–20 ns | all cores on the chip |
| RAM | GBs | ~80–100 ns | whole machine |

A miss that goes all the way to RAM costs ~300 cycles, during which the core can do almost nothing. This is why data layout often matters more than instruction count ([[cpu-caches-and-cache-lines]]).

- **Processor vs core**: the word "CPU" is overloaded. A *processor* (socket, package) is the chip plugged into the motherboard; it contains one or more *cores*, the units that actually execute instructions. Servers often have two sockets. `lscpu` shows sockets, cores per socket and threads per core.
- **Cache coherence**: if two cores cache the same variable and one of them writes it, the other copy must be invalidated before it is read again (the MESI protocol). Constant invalidation, from two threads writing one variable or different variables on the same 64-byte line (false sharing), is one of the most expensive things in multithreaded code ([[cpu]]).
- **RISC vs CISC**: ARM is RISC (reduced instruction set: simple, fixed-length instructions); x86 is CISC (complex, variable-length instructions, some of which read memory, compute and write back in one go). The lecture says CISC instructions take several cycles and therefore more power. On modern chips the gap is smaller: x86 decodes each instruction into simple RISC-like micro-ops, and both kinds run several instructions per cycle. ARM's power efficiency comes mostly from simpler decoding and from designs aimed at phones from the start.
- **Machine code is per architecture**: a binary compiled for x86-64 does not run on ARM64. Compiled languages (C, Go, Rust) build one binary per target. Interpreted and VM languages ship a runtime compiled for each platform: one `python` and one JVM per OS and CPU, so your `.jar` of bytecode runs anywhere. The platform comes back in native pieces: JNI libraries (Netty's epoll transport, RocksDB), GraalVM native images and Docker images, which need separate `linux/amd64` and `linux/arm64` variants (an M-series Mac builds arm64 by default).

```anim
cpu-memory-hierarchy
```

### Memory (RAM)
- **Random access**: any byte can be read in the same time, unlike a disk. **Fast but volatile**: about 1,000× faster than an SSD for a random read, but everything is lost when the power goes. That is why databases write to storage and call `fsync` before they confirm a commit.
- **Stores process state and data**: the code, globals, heap and stacks of every running process, and the kernel's own structures, including each process's descriptor, its page tables and the saved registers of threads that are not running right now.
- **Limited**: when RAM runs out the kernel first drops file cache, then swaps pages to disk if swap exists, and finally the OOM killer terminates a process. In a container the limit is the cgroup's, not the machine's ([[memory-management]]).
- **Slower than CPU cache**: see the table above; RAM is the slow tier from the CPU's point of view.
- **Why "random access"**: a tape is sequential: to reach the end you wind through everything before it (think of rewinding a VHS cassette). RAM reaches any address directly. SSDs are random-access too, so the name no longer sets RAM apart.
- **Every access is a round trip**: the CPU asking RAM for data is like a frontend calling a backend over REST. One call is cheap; a chatty loop of them adds up. That is why caches exist, and why Apple's M-series chips put the RAM in the same package as the CPU.
- **Databases live in RAM, but durably**: Postgres's shared buffers and InnoDB's buffer pool update pages in memory and write them to disk later. Durability comes from the **write-ahead log**: before a commit returns, only the change is appended sequentially to a log on disk, and after a crash the log is replayed. Sequential appends are what disks, SSDs especially, do best.
- **A running process lives entirely in memory**: its machine code, data, heap and stacks. When the power goes the process is gone, but the program it came from is still on disk. The kernel may move pages that have not been used for a while to swap and bring them back on the next access through a page fault; the process never notices, because it only sees virtual addresses.
- Each process sees memory laid out from a **minimum** to a **maximum address**:

```anim
process-memory-layout
```

The details of each region are in [[anatomy-of-a-process]].

### Storage
- **Persistent**: survives power-off. **HDD** and **SSD** are the two kinds. Both are much slower than memory: an NVMe SSD random read takes tens of microseconds, an HDD random read 5–10 ms (the head has to move and the platter has to rotate), against ~100 ns for RAM.
- **HDD geometry**: a stack of spinning **platters**, a **head** per surface, concentric **tracks** split into **sectors** (traditionally 512 bytes). A **cylinder** is the same track on all platters. Old operating systems addressed the disk by **CHS** (cylinder / head / sector) and had to know its physical layout. A *geometrical* sector is a pie slice of the platter; a *disk* sector is the piece of one track inside it, the unit that is actually read.
- **LBA**: modern disks hide their geometry and expose a flat array of numbered **logical blocks** (LBA 0, 1, 2, …), usually 512 bytes or 4 KiB each. The drive maps each LBA to its physical place; on an SSD a flash translation layer does this and also spreads writes for wear.
- **SSDs never overwrite in place**: flash is written in pages (for example 16 KiB) and erased only in much larger blocks. An update writes the new data to a free page and marks the old one invalid; later the drive's garbage collection copies the valid pages out of a block and erases it. Each block survives a limited number of program/erase cycles, so SSDs wear out. An HDD rewrites a sector in place as often as you like; it wears mechanically instead ([[storage-management]]).
- **The controller lives in the drive**: early disks had their controller on the host, on a separate card, so the OS had to know every detail and vendors could not change the hardware without breaking it. IDE ("integrated drive electronics") moved the controller onto the drive. Today an NVMe SSD accepts a standard **command set** (NVMe 2.0) and hides flash management behind it: the kernel speaks the protocol, and vendors are free to change everything below.
- **Speed ladder**: CPU caches > RAM > SSD > HDD, each step one to three orders of magnitude slower.
- Sequential I/O is still much faster than random I/O on both kinds. That is why databases append to logs (Postgres WAL, Kafka segments) instead of updating in place.

```anim
disk-lba-partitions
```

### Network
- The **NIC** (network interface controller) sends and receives frames on the wire. Received packets are written straight into RAM by DMA into ring buffers, and the NIC raises an interrupt.
- The **protocol implementations** (Ethernet, IP, TCP, UDP) live in the kernel's network stack. Programs see only **sockets** ([[osi-and-tcp-ip-models]], [[tcp-vs-udp]]).
- On receive, signals on the wire (electrical on copper, light in fibre) become bits, bits become Ethernet **frames** (layer 2), frames carry IP **packets** (layer 3), and packets carry TCP **segments** (layer 4). The driver and the kernel handle layers 2–4; the application gets only the bytes.
- TCP dates from 1981 (RFC 793) and lives entirely in the kernel, for efficiency: parsing, acknowledgements, retransmission, flow and congestion control. Modern NICs offload part of the work: checksums, cutting large sends into segments (TSO), merging received packets (GRO), spreading packets across cores (RSS) ([[socket-management]]).
- Through it the machine **communicates with other hosts**: your service with Postgres, Kafka and its clients.

### The kernel
- The **core of the OS**: the one program that always runs in privileged mode and **manages the resources** above. CPU time goes through the scheduler, memory through page tables, storage through file systems, the network through the stack and devices through drivers.
- **The OS is more than the kernel**: shells, a GUI, command-line tools and package managers are ordinary user programs. `top` is just a program that reads process information the kernel publishes in `/proc`. A **distribution** (Ubuntu, Debian, Alpine) is the Linux kernel plus a chosen set of this tooling. In a container, `FROM ubuntu` vs `FROM alpine` changes only that tooling, because every container on the host runs on the host's kernel ([[docker-fundamentals]]).
- `/proc` is not a real directory: it is a virtual file system that the kernel generates when you read it, with one directory per process. Anyone can write their own `top` on top of it.
- The kernel works with almost nothing on top: you can build it from source and boot straight into the one program you need. (The lecture says Linus Torvalds runs a bare kernel without a distribution; he has in fact used Fedora for years.) Container images built `FROM scratch` or distroless are the same idea: the host's kernel plus just your binary. Ubuntu, based on Debian, became the most popular distribution by removing friction: an installer, sane defaults and ready tools on top of the same kernel.
- **Everything is an API**: the kernel exposes system calls upward to programs and consumes APIs downward: the CPU's instruction set, the NVMe command set, each device's registers through its driver. Every layer can evolve as long as its API stays stable.

### User space and kernel space
- **User space**: where applications run, such as a browser, Postgres or the JVM. Each process has its own address space and cannot see the others'.
- **Kernel space**: the kernel's code, device drivers and the TCP/IP stack. It is protected: user-mode code that touches a kernel address gets a fault.
- Each process's virtual address space is split in two. On 32-bit Linux the default is 3 GB for the user and the top 1 GB (from `0xC0000000` to `0xFFFFFFFF`) for the kernel. On 64-bit, user space is the lower half (up to `0x00007FFF FFFF FFFF`) and the kernel the upper half. Since the Meltdown fixes (KPTI, 2018), most kernel pages are not even mapped while user code runs. This is part of why system calls became more expensive.
- **Isolated, with one deliberate exception**: `io_uring` shares two ring buffers between a process and the kernel. The application queues I/O requests and reads completions from shared memory, without a system call per operation. It is fast (Postgres 18 can use it for asynchronous I/O), but it breaks the strict separation and it is a large new attack surface. In 2023 Google disabled it for Android apps, on ChromeOS and on its production servers, after io_uring bugs made up 60% of the kernel exploits submitted to its bug bounty in 2022; Docker's default seccomp profile blocks it too.

### Programs and processes
- A **program** is an executable file on disk; a **process** is a running instance of it in memory, with its own state: which instruction it is on, its variables, its open files. One program can run as many processes: Postgres starts a separate backend process for every client connection, all from the same `postgres` binary.
- A process changes as it runs: its stack grows and shrinks, its heap grows (forever, if it leaks) ([[anatomy-of-a-process]]).
- An executable has a format with headers that tell the kernel how to load it:

| OS | Format | Inspect with |
|---|---|---|
| Linux, BSDs | ELF (Executable and Linkable Format) | `file`, `readelf -h` |
| Windows | PE (Portable Executable: `.exe`, `.dll`) | `dumpbin /headers` |
| macOS | Mach-O | `otool -h` |

The header records the target CPU, which is why `file ./app` says `x86-64` or `ARM aarch64` before you even try to run it.

### Process management
- The kernel **creates processes** (`fork`/`clone` + `exec`) and keeps a descriptor for each process and thread: its state, its memory map, its open files and its saved registers.
- It **schedules processes onto CPUs**: on every core it picks which runnable thread runs next and for how long ([[process-management]]).
- It **switches one process for another**: a context switch saves the running thread's registers into its descriptor and loads the next one's. This happens thousands of times a second; the indirect cost is cold caches ([[process-management]]).
- It switches when the time slice runs out (preemption, which fixed the Windows 3.1 freezes from [[operating-system-introduction]]) or when the process blocks on IO. Even a fast NVMe read takes 10–100 µs, tens of thousands of cycles, so the kernel parks the waiting thread and gives the core to another one: like a clerk who sends you out of the queue to fill in a form and calls the next person.
- It **grants access to resources** and enforces who may use what: file permissions, user IDs, capabilities, cgroup limits.

### Memory management and virtual memory
- Each process gets its own **virtual address space**, cut into 4 KiB **pages**; a per-process **page table** maps each page to a physical frame anywhere in RAM. This gives isolation, hides physical fragmentation, lets processes share code and libraries, and makes `fork` cheap through copy-on-write.
- The full picture, with animations (how the CPU reads RAM, fragmentation, page tables, swap, copy-on-write, DMA), is in [[memory-management]].

### File systems and partitions
- **Storage is just blocks of bytes**, but programs want **files** and directories. A **file system** is the abstraction that maps one onto the other. It records which blocks belong to which file, keeps metadata (size, owner, permissions, timestamps) in a structure such as an inode, maps names to inodes through directories, tracks free space, and usually keeps a journal so a crash cannot corrupt that bookkeeping.
- **Why LBA exists, a lesson in API design**: the OS once addressed disks by cylinder, head and sector, so the physical design leaked into every kernel and vendors could not change it without the OS changing too. Hiding it behind a flat array of blocks let drives evolve freely, at the cost of the OS no longer knowing where data physically sits. It is the same trade-off as an API that other services consume: once someone depends on it, you need an abstraction to keep evolving.
- **Space is allocated in whole blocks**: a 3-byte `test.txt` still takes one 4 KiB block. `ls -l` shows 3 bytes, `du -h` shows `4.0K`. Millions of tiny files waste both space and inodes.
- **Compatibility**: an OS can read a file system only if it has a driver for it, because the layout and metadata are specific to it. For years FAT32 was the common format for removable drives; Linux read NTFS only through a separate driver (ntfs-3g, and an in-kernel driver since 5.15 in 2021). Today exFAT is the usual choice for USB drives.
- **Partitions**: the disk is exposed as one big array of LBAs; a partition is a contiguous range of them (for example LBA 1–4 as partition 1). A partition table at the start of the disk (GPT, formerly MBR) records the ranges. Partitions give **logical segmentation**: each can hold its own file system with its own **block size** (cluster). One disk might hold an EFI partition with FAT32, a root partition with ext4 using 4 KiB blocks, and a swap partition.
- **Common file systems**:

| File system | Where | Notes |
|---|---|---|
| ext4 | Linux default | Journaled, mature; the usual choice for servers |
| xfs | Linux (RHEL default) | Good with large files and parallel I/O; common for databases |
| btrfs | Linux | Copy-on-write, snapshots, checksums |
| NTFS | Windows | Journaled, permissions (ACLs) |
| FAT32 | USB sticks, EFI boot partitions | No permissions; 4 GB maximum file size |
| tmpfs | Linux `/tmp`, `/dev/shm`, `/run` | Lives in RAM; fast; gone on reboot |

- Reads and writes go through the **page cache** in RAM. A `write` returns once the data is in the cache; it reaches the disk later, unless the program calls `fsync`.

### Device drivers
- **Drivers are software in the kernel** that **manage one kind of hardware device**: the NVMe controller, the NIC, the keyboard, the GPU. Each knows its device's registers and protocol and presents a uniform interface upwards, such as a block device for disks or a network interface for NICs.
- When a device needs attention (a packet arrived, a read finished) it raises an interrupt and the CPU jumps to the driver's **interrupt service routine** (ISR). The ISR does the minimum, such as acknowledging the device and queuing the data, and defers the rest so the CPU can return quickly.
- Most Linux drivers are loadable modules (`lsmod` lists them). Because they run inside the kernel, a buggy driver can crash the whole machine.

### System calls and the other ways into the kernel
- A **system call** is how a program **jumps from user mode to kernel mode** to ask for a service: `read()`, `write()`, `open()`, `mmap()`, `clone()`, `epoll_wait()`. The CPU instruction `syscall` performs the **mode switch**; the kernel checks the arguments, does the work and switches back ([[syscalls-and-file-descriptors]]).
- `malloc()` is not a system call. It is a libc function that manages the heap inside the process and calls `brk` or `mmap` only when it needs more memory from the kernel. The same holds for the JVM heap.
- There are three ways into the kernel:

| Entry | Caused by | Example | Typical cost |
|---|---|---|---|
| System call | The program, deliberately | `read`, `write`, `futex` | 100–300 ns plus the work |
| Interrupt | A device, asynchronously | NIC received a packet; timer tick; disk read finished | Preempts whatever runs; the ISR must be short |
| Exception / trap | The CPU, on the current instruction | Page fault, divide by zero, illegal instruction | A page fault that needs disk I/O: milliseconds |

A page fault is how lazy memory works: the JVM reserves 4 GB, and only the first touch of each page makes the kernel map a real frame. A timer interrupt is how preemption works: the kernel regains control every few milliseconds whether the running thread wants it or not.

### Kernel designs
| Design | Idea | Trade-off | Examples |
|---|---|---|---|
| Monolithic (with modules) | Drivers, file systems and the network stack all run inside the kernel | Fast: one mode switch per request; a driver bug can crash everything | Linux, BSDs |
| Microkernel | The kernel only schedules, manages memory and passes messages; drivers and file systems are user processes | Isolated and restartable, but every I/O crosses processes | QNX, seL4, Minix |
| Hybrid | Monolithic core with some services outside | Pragmatic middle | Windows NT, macOS XNU |

## Example
A Java thread blocks on a socket read and a packet arrives, touching every part of the picture:

```anim
syscall-walkthrough
```

```text
1. JVM thread calls SocketInputStream.read()                       user space, CPU core 2
2. libc executes the `syscall` instruction → mode switch            system call
3. Kernel: socket buffer empty → thread state = sleeping;
   scheduler runs another thread on core 2                          process management
4. NIC receives a frame, DMA-copies it into a ring buffer in RAM,
   raises an INTERRUPT                                              network + memory
5. Core 0 jumps to the NIC driver's interrupt service routine;
   the ISR acknowledges the NIC and schedules the rest              device driver
6. Network stack (softirq): IP and TCP checks, bytes appended to
   the socket buffer, our thread marked runnable                    kernel network stack
7. Scheduler puts our thread back on a core                         process management
8. Kernel copies the bytes from the socket buffer into the
   JVM's byte[] (one copy, kernel space → user space)               memory
9. `sysret` → user mode; read() returns the byte count              system call returns
```

The Java code pays for two mode switches (steps 2 and 9). Steps 4–6 run on whichever core handles the NIC's interrupts, which is why a busy network server shows CPU time under `si` (softirq) in `top` rather than under the JVM.

## Pitfalls
- "User space" and "kernel space" describe privilege, not a place in RAM. With KPTI, most kernel memory is not even mapped while user code runs; without it, it is mapped but inaccessible.
- A system call is not a function call: it is a mode switch, argument validation and possibly a context switch. Batching (`writev`, `epoll_wait` returning many events, `io_uring`) exists to amortise that cost.
- `free` in Linux shows little "free" memory on a healthy machine, because the page cache uses spare RAM. The number to watch is "available".
- Data written with `write` is in RAM, not on disk. Without `fsync`, a power loss can lose it even though the call succeeded.
- Building a Docker image on an M-series Mac and running it on an x86 server: `exec format error`. Build multi-arch images (`docker buildx build --platform linux/amd64,linux/arm64`) or set the platform explicitly.
- Two threads updating counters that sit on the same cache line: every write invalidates the other core's copy (false sharing). Java has `LongAdder` and `@Contended` for this.
- Related: [[operating-system-introduction]] for why an OS exists, [[linux-architecture-and-kernel]] for the Linux-specific layers and `/proc`, [[cpu]] for what a core does with each instruction.
