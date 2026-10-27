---
title: Operating system architecture
tags: [operating-systems, kernel, user-space, interrupts, architecture]
learned: 2026-10-27
---
## TL;DR
An OS is built as layers with one hard boundary. Below the boundary the **kernel** runs in privileged CPU mode and owns the hardware; above it, every program runs in **user mode** inside its own process and can reach the kernel only through system calls. The kernel itself is entered three ways: a system call from a program, an **interrupt** from a device, or an **exception** (page fault, division by zero) raised by the CPU. How much lives inside the kernel is a design choice: Linux is a monolithic kernel with loadable modules, and that choice is why a driver bug can crash the machine but a syscall is cheap.

## Key points
### The layers, bottom up
| Layer | What is there | Mode |
|---|---|---|
| Hardware | CPU, memory, disk and network controllers, timers, interrupt controller | — |
| Kernel | Scheduler, memory manager, virtual file system, file systems, network stack, device drivers, security modules | privileged |
| System call interface | A few hundred numbered entry points; the only way up and down | boundary |
| System libraries | `libc` wraps syscalls in C functions (`open`, `read`, `pthread_create`); the JVM is built on it | user |
| Runtime and application | JVM, your Spring Boot service, Postgres, `nginx` | user |

The JVM sits two layers above the kernel: `socket.read()` → JNI → `libc read()` → syscall → kernel → driver. Each layer adds a copy or a check, which is why the number of crossings matters more than the number of lines of Java ([[operating-system-introduction]]).

### Privilege modes
- The CPU has at least two modes. In **kernel mode** (ring 0 on x86) every instruction and every address is allowed. In **user mode** (ring 3) I/O instructions and privileged registers are forbidden, and memory is limited to the process's own address space.
- Switching modes is done by the hardware, never by the program: a `syscall` instruction, an interrupt or an exception raises the privilege, and `iret`/`sysret` lowers it when the kernel returns.
- This is the whole basis of protection: a user program cannot "decide" to touch the disk; it can only ask.

### Three ways into the kernel
| Entry | Caused by | Example | Typical cost |
|---|---|---|---|
| System call | The program, deliberately | `read`, `write`, `futex`, `epoll_wait` | 100–300 ns plus the work |
| Interrupt | A device, asynchronously | Network card received a packet; timer tick; disk finished a read | Preempts whatever runs; handler must be short |
| Exception / trap | The CPU, synchronously, on the current instruction | Page fault (touching a page not yet mapped), divide by zero, illegal instruction | Page fault with disk I/O: milliseconds |

A page fault is how lazy memory works: the JVM "allocates" 4 GB, nothing happens; the first write to each page traps into the kernel, which maps a real page and returns ([[virtual-memory-and-paging]]). A timer interrupt is how preemption works: the kernel gets control every few milliseconds whether the running thread wants it or not ([[cpu-scheduling]]).

### Kernel designs
| Design | Idea | Trade-off | Examples |
|---|---|---|---|
| Monolithic | Everything (drivers, file systems, network) runs inside one kernel address space | Fast calls between subsystems; one driver bug can crash the whole system | Linux, BSDs |
| Monolithic with modules | Same, but drivers and file systems load at runtime | Flexibility without a mode switch per call | Linux (`.ko` modules) |
| Microkernel | Kernel does only scheduling, memory and message passing; drivers and file systems are user processes | Isolation and restartable drivers; every I/O crosses processes, so slower | QNX, seL4, Minix |
| Hybrid | Monolithic core with some services outside | Pragmatic middle | Windows NT, macOS XNU |

Linux chose speed: the network stack, file systems and drivers are all in-kernel, so a `read` on a socket is one mode switch, not three process hops.

### Inside the kernel, what matters to a service
- **Scheduler**: picks the next runnable thread per CPU; load balancing between cores ([[cpu-scheduling]]).
- **Memory manager**: page tables, the page cache (file data kept in RAM), swap, the OOM killer.
- **VFS**: the layer that makes `ext4`, `xfs`, `/proc`, a socket and a pipe all look like a file descriptor ([[syscalls-and-file-descriptors]]).
- **Network stack**: TCP/IP, socket buffers, `epoll` readiness ([[io-models-blocking-nonblocking-epoll]], [[tcp-vs-udp]]).
- **Namespaces and cgroups**: the isolation and limits that containers are made of ([[docker-containers-and-resources]]).

## Example
What happens, layer by layer, when a Java thread blocks on a socket read and a packet arrives:

```text
1. JVM thread calls SocketInputStream.read()
2. libc issues the `read` syscall → CPU switches to kernel mode
3. Kernel: no data in the socket buffer → marks the thread "sleeping", schedules another thread
   ... time passes, other threads run ...
4. Network card writes the packet to RAM and raises an INTERRUPT
5. CPU jumps to the kernel's interrupt handler (whatever was running is paused)
6. Handler: TCP stack validates the segment, appends bytes to the socket buffer,
   marks our thread "runnable"; handler returns (microseconds)
7. Scheduler, at the next tick or when the current thread blocks, picks our thread
8. Kernel copies the bytes from the socket buffer to the JVM's byte[] (one copy)
9. `sysret` → user mode; read() returns the byte count; Java code continues
```

Steps 2 and 9 are the only mode switches the Java code pays for. Steps 4–6 happen on whichever core is handling interrupts, which is why a busy network server shows CPU time attributed to `softirq` rather than to the JVM.

## Pitfalls
- "User space" and "kernel space" are about privilege, not about location in RAM. Kernel memory is mapped into every process's address space but is inaccessible in user mode.
- A system call is not a function call. It is a mode switch plus argument validation plus a possible context switch; batching (`writev`, `epoll_wait` returning many events) exists to amortise it.
- Interrupt handlers cannot block or sleep. Work that needs to wait is deferred (softirqs, work queues); that deferred work is what `top` shows as `si`.
- A driver crash in a monolithic kernel is a kernel panic. That is the price of speed, and it is why Linux reviews drivers so strictly.
- Related: [[linux-architecture-and-kernel]] for the Linux layers, init systems and `/proc`; [[how-cpu-executes-code]] for what the CPU does between those steps.
