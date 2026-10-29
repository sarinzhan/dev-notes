---
title: What an operating system is and why it exists
tags: [operating-systems, kernel, abstraction, resources, fundamentals]
learned: 2026-10-27
order: 1
---
## TL;DR
An operating system is the program that sits between the hardware and every other program. It does three jobs: it **abstracts** the hardware into simple ideas (a file, a process, a socket) so that programs do not talk to disks and network cards directly; it **shares** the CPU, memory, disk and network between many programs that all believe they own the machine; and it **protects** programs from each other and from breaking the machine. Your Spring Boot service never touches hardware: every byte it reads or sends goes through the OS. Strictly, an application does not *need* an OS: on one box you fully control, a thin custom layer can be faster. A general-purpose OS exists because real software has to run on thousands of different CPUs, disks and devices, and that generality costs some performance, some compatibility breaks and some leaky abstractions.

## Key points
### What a computer is without an operating system
- Bare hardware runs one program that has to drive every device itself: write bytes to the disk controller, poll the network card, set up timers. Early computers and small microcontrollers still work this way.
- Only one program can run at a time, and a bug in it hangs the whole machine. Nothing stops it from overwriting any memory address.
- Every program would re-implement the same drivers, the same file layout and the same scheduling. The OS exists so this is written once.

### Do we actually need an OS?
- Not strictly. If you build the whole box yourself (CPU, keyboard, monitor, disks) and your app only ever runs there, you can write a very thin layer that drives exactly those devices, or run the app directly on the hardware. It can be several times faster than a general-purpose OS, which has to handle every vendor and every corner case, so its code is bigger, more complex and slower in places.
- The price: that app runs **only on that box**. Real examples of the trade-off:
  - **Embedded firmware** and real-time OSes (microcontrollers, routers, car ECUs) run one program on known hardware.
  - **Unikernels** (Unikraft, MirageOS) compile one application together with just the OS pieces it needs into a single image for a VM.
  - **Kernel bypass**: DPDK (network) and SPDK (NVMe) let an application drive the NIC or SSD from user space, skipping the kernel for the hot path; used by trading systems, packet processors, ScyllaDB-style databases.
  - **Vertical integration**: Apple's M-series chips put the CPU, GPU and RAM in one package because Apple controls the hardware and the OS together, which a PC built from parts cannot do.
- Many companies take the Linux kernel and extend it for their needs. That works, but they stay bound by architectural decisions made for a general-purpose system.
- So the OS is a choice, not a law. In practice we deal with many devices, which is why general-purpose systems like Linux and Windows exist.

### A tiny example: printing 1 + 1 = 2
- Even this needs hardware: the CPU to add, and the screen to show the result. Without an OS your program would have to know whether the monitor is on VGA, HDMI or DisplayPort, which version of the protocol it speaks (HDMI 1.4, 2.0, 2.1), and follow that spec byte by byte; and notice when a new monitor is plugged in.
- With an OS, the program calls `write(1, "2\n", 2)`; the terminal, the graphics driver and the display stack do the rest, and the OS detects hot-plugged devices and loads the right driver.
- The same is true for storage: sending IO to an NVMe SSD is different from a Toshiba hard disk or a shingled (SMR) drive. The OS hides this behind layers: the **virtual file system** (one API for ext4, XFS, NFS, `/proc`), the **block layer** (one interface for all block devices) and the device **drivers** ([[storage-management]]).
- And memory: a program uses virtual addresses; the OS maps them to physical addresses; the memory controller then decodes a physical address into DRAM coordinates (channel, rank, bank, row, column) and opens that row ([[memory-management]]). Each layer hides the one below.

### The three roles
| Role | What it means | What you see as a backend developer |
|---|---|---|
| Abstraction | Hardware details hidden behind uniform ideas: a file instead of disk sectors, a socket instead of network frames, a process instead of CPU registers, virtual memory instead of physical addresses | `FileInputStream`, `ServerSocket`, a `Thread`: the JVM calls the OS, the OS calls the driver |
| Resource management | Decides which process gets the CPU and for how long, how much memory each one holds, in which order disk and network requests are served | Load average, CPU throttling in a container, `OutOfMemoryError` vs the OOM killer, a pod that is "pending" |
| Protection and isolation | Each process gets its own address space and cannot read another's memory; the hardware is reachable only through the kernel; users and permissions limit what a process may do | One crashed service does not take down the others; a process cannot bind port 80 without privileges; a container cannot see files outside its namespace |

### Kernel and the rest
- The **kernel** is the part of the OS that runs with full hardware privileges all the time: scheduler, memory manager, file systems, network stack, drivers. It is loaded at boot and never exits.
- Everything else that ships with the OS (shell, `ls`, `systemd`, `ssh`) is an ordinary user program with no special powers. "Linux" strictly means the kernel; a distribution adds the rest.
- A program asks the kernel for a service through a **system call** ([[syscalls-and-file-descriptors]]): open a file, create a thread, send bytes. That is the only door between a program and the hardware; the CPU switches from user mode to kernel mode to go through it ([[kernel-vs-user-mode]]).

### What the OS gives a running program
- A **process** ([[process-management]], [[anatomy-of-a-process]]): a private address space, one or more threads of execution, a table of open files, an identity (user, group, PID).
- **Time on the CPU** ([[process-management]]): the illusion that your 200 threads run at once on 8 cores.
- **Memory on demand** ([[memory-management]]): the JVM asks for 4 GB of heap and gets it page by page, when first touched.
- **I/O that looks uniform** ([[socket-management]]): a file, a pipe, a socket and a terminal are all read with the same call.
- **Signals and lifecycle** ([[signals-and-process-lifecycle]]): how the OS tells a process to stop, and how `SIGTERM` becomes a graceful shutdown.

### Why a backend developer needs this
- Every performance problem that is not in your code is in how your code uses the OS: too many threads, blocking I/O, running out of file descriptors, memory that the JVM thinks is free but the kernel has not reclaimed.
- Containers and Kubernetes are OS features (namespaces, cgroups) with an API on top ([[docker-containers-and-resources]], [[kubernetes-basics]]). Limits, probes and OOM kills make sense only through the OS.
- Databases and Kafka are built directly on OS primitives: the page cache, `fsync`, `sendfile`, `epoll`. Their tuning guides are OS vocabulary.

### Everything is a file, everything is an object
- Linux (like Unix) exposes almost everything as a **file**: disks (`/dev/nvme0n1`), terminals, the mouse, sockets, pipes, kernel state (`/proc`, `/sys`). One set of calls, `open`/`read`/`write`/`close`, works on all of them, and that is why shell tools compose so well ([[syscalls-and-file-descriptors]]).
- Windows models resources as **objects** reached through handles (files, processes, events, registry keys), with type-specific APIs. Both are abstractions with trade-offs: "a file" fits a disk well and a GPU or a network card less well (hence `ioctl`).

### Resource management is scheduling
- Processes are instances of programs, and they compete for **CPU, memory and IO**. The OS has to give each a fair share, and life is not fair: someone always waits.
- **CPU**: Windows 3.1 used cooperative multitasking. Once a process got the CPU it ran until it gave it back; a CPU-hungry program (or an infinite loop) froze everything, even the mouse. Modern kernels are **preemptive**: a timer interrupt takes the CPU away when the time slice ends ([[process-management]]).
- **IO is a resource too**: a disk serves only so many requests per second (that is physics), and anyone beyond that waits. Databases expose this: MySQL's `innodb_io_capacity` caps InnoDB's background page flushing so it does not saturate the disk and starve the important IO, such as the redo log (write-ahead log) writes on commit.
- Scheduling is hard because a general-purpose OS **does not know the workload**. If you know all your jobs are long-running batch work, scheduling is easy; a server mixing short requests, batch jobs and background flushing is not. The application often knows best, which is why apps get knobs: `nice`, cgroups, IO priorities, settings like `innodb_io_capacity`. People write PhDs on scheduling, and backends face the same problem in job queues.

### APIs and compatibility
- The OS gives applications a stable API that hides the hardware. The best known is **POSIX** (Portable Operating System Interface): `read`, `write`, `open`, `fork`, threads, signals. Code written against it runs on Linux, macOS and the BSDs.
- Every abstraction leaks. POSIX is often criticised as too limited (no good async file IO, `fsync` semantics that databases fight with), and the same `read()` can take 100 ns from the page cache or 10 ms from a disk. Hiding complexity behind one interface is like the strategy pattern in a logging library: the call looks identical whether it writes to a file or over the network, until it is suddenly slow and you do not know why.
- **The cost of being general-purpose**: to move forward, an OS sometimes drops old hardware or old APIs. Windows 11 refuses CPUs without TPM 2.0; Linux rarely drops anything, but it happens (i386 support was removed in 2012).
- **32-bit vs 64-bit** was the biggest break. A 64-bit OS on x86-64 runs 32-bit programs natively in a compatibility mode (with a 4 GiB address space each), but a 32-bit OS cannot run 64-bit programs. Running code for a *different* architecture needs emulation or translation, like Apple's Rosetta 2 for x86 apps on ARM Macs.

### Why open the black box
- The goal is to see the OS as a transparent box: not magic, just code, often a linked list where you expected something exotic. Knowing how it works makes you appreciate it and spot its inefficiencies.
- **The kernel can be improved by anyone who looks closely.** An example from the lecture: on servers with many NVMe drives, shutdown took a long time because the kernel shut devices down one by one, waiting for each to flush its cache (a few hundred ms each) before starting the next. Starting all the shutdowns at once and waiting once turned the sum of waits into roughly a single wait. This became a 2024 patch series to the Linux driver core for asynchronous device shutdown. (The lecturer was not sure whether it came from ByteDance or Google.)
- Small things decide performance:
  - A 64-byte cache line: a 65-byte struct spills into a second line, and touching it can cost an extra miss to RAM (~100 ns) ([[cpu-caches-and-cache-lines]]).
  - A process that waits for IO is useless to the CPU, so it is switched out, and saving and restoring its state costs time ([[process-management]]).
  - Python and `fork`: CPython stores a reference count inside every object, including `None`. A forked worker that merely *reads* shared objects increments their counts, which is a write, which breaks copy-on-write, so the "shared" pages get copied in every child. Instagram worked around it with `gc.freeze()`; Python 3.12 made `None` and other core objects **immortal** (PEP 683) so their counts never change ([[memory-management]]).
- You will not see any of this without knowing how the OS works, and once you do, the way you build applications changes.

## Example
A single HTTP request, traced through the layers it crosses:

```text
client ──TCP──▶ network card
                 │ interrupt
              kernel: network stack assembles the packet,
                      finds the socket bound to :8080,
                      queues the bytes, wakes the waiting thread
                 │ epoll/accept returns (system call completes)
              JVM: Tomcat thread reads the request, calls your controller
                 │ your code: SELECT ... from Postgres
              JVM: writes bytes to the DB socket           ── system call
              kernel: sends the packet, later wakes the thread with the reply
              JVM: controller returns, Tomcat writes the response
              kernel: copies bytes to the socket buffer, drives the card
```

Your code is two lines in this picture; the kernel is in every other one. Each crossing of the line between the JVM and the kernel costs a few hundred nanoseconds, which is why "fewer, larger I/O calls" is a universal performance rule.

## Pitfalls
- "The OS is slow" is almost never the diagnosis. The OS is doing what the program asked; the fix is to ask for less (fewer syscalls, fewer threads, bigger buffers).
- The JVM is not an operating system. It manages its own heap and threads, but every thread is still an OS thread (until virtual threads, [[virtual-threads]]) and every allocation beyond the current heap is an OS request.
- Writing code against a single machine's quirks (a specific disk, CPU feature or kernel version) because "it is faster here": it is, until the hardware or kernel changes. Know when you are trading portability for speed.
- Containers do not have their own OS. All containers on a node share one kernel; the isolation is namespaces and cgroups, not virtualisation ([[docker-fundamentals]]).
- Related: [[operating-system-architecture]] for how the layers and privilege modes fit together, [[linux-architecture-and-kernel]] for the Linux-specific picture.
