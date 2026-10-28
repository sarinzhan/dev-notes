---
title: What an operating system is and why it exists
tags: [operating-systems, kernel, abstraction, resources, fundamentals]
learned: 2026-10-27
order: 1
---
## TL;DR
An operating system is the program that sits between the hardware and every other program. It does three jobs: it **abstracts** the hardware into simple ideas (a file, a process, a socket) so that programs do not talk to disks and network cards directly; it **shares** the CPU, memory, disk and network between many programs that all believe they own the machine; and it **protects** programs from each other and from breaking the machine. Your Spring Boot service never touches hardware: every byte it reads or sends goes through the OS.

## Key points
### What a computer is without an operating system
- Bare hardware runs one program that has to drive every device itself: write bytes to the disk controller, poll the network card, set up timers. Early computers and small microcontrollers still work this way.
- Only one program can run at a time, and a bug in it hangs the whole machine. Nothing stops it from overwriting any memory address.
- Every program would re-implement the same drivers, the same file layout and the same scheduling. The OS exists so this is written once.

### The three roles
| Role | What it means | What you see as a backend developer |
|---|---|---|
| Abstraction | Hardware details hidden behind uniform ideas: a file instead of disk sectors, a socket instead of network frames, a process instead of CPU registers, virtual memory instead of physical addresses | `FileInputStream`, `ServerSocket`, a `Thread`: the JVM calls the OS, the OS calls the driver |
| Resource management | Decides which process gets the CPU and for how long, how much memory each one holds, in which order disk and network requests are served | Load average, CPU throttling in a container, `OutOfMemoryError` vs the OOM killer, a pod that is "pending" |
| Protection and isolation | Each process gets its own address space and cannot read another's memory; the hardware is reachable only through the kernel; users and permissions limit what a process may do | One crashed service does not take down the others; a process cannot bind port 80 without privileges; a container cannot see files outside its namespace |

### Kernel and the rest
- The **kernel** is the part of the OS that runs with full hardware privileges all the time: scheduler, memory manager, file systems, network stack, drivers. It is loaded at boot and never exits.
- Everything else that ships with the OS (shell, `ls`, `systemd`, `ssh`) is an ordinary user program with no special powers. "Linux" strictly means the kernel; a distribution adds the rest.
- A program asks the kernel for a service through a **system call** ([[syscalls-and-file-descriptors]]): open a file, create a thread, send bytes. That is the only door between a program and the hardware.

### What the OS gives a running program
- A **process** ([[process-management]], [[anatomy-of-a-process]]): a private address space, one or more threads of execution, a table of open files, an identity (user, group, PID).
- **Time on the CPU** ([[process-management]]): the illusion that your 200 threads run at once on 8 cores.
- **Memory on demand** ([[memory-management]]): the JVM asks for 4 GB of heap and gets it page by page, when first touched.
- **I/O that looks uniform** ([[io-models-blocking-nonblocking-epoll]]): a file, a pipe, a socket and a terminal are all read with the same call.
- **Signals and lifecycle** ([[signals-and-process-lifecycle]]): how the OS tells a process to stop, and how `SIGTERM` becomes a graceful shutdown.

### Why a backend developer needs this
- Every performance problem that is not in your code is in how your code uses the OS: too many threads, blocking I/O, running out of file descriptors, memory that the JVM thinks is free but the kernel has not reclaimed.
- Containers and Kubernetes are OS features (namespaces, cgroups) with an API on top ([[docker-containers-and-resources]], [[kubernetes-basics]]). Limits, probes and OOM kills make sense only through the OS.
- Databases and Kafka are built directly on OS primitives: the page cache, `fsync`, `sendfile`, `epoll`. Their tuning guides are OS vocabulary.

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
- Containers do not have their own OS. All containers on a node share one kernel; the isolation is namespaces and cgroups, not virtualisation ([[docker-fundamentals]]).
- Related: [[operating-system-architecture]] for how the layers and privilege modes fit together, [[linux-architecture-and-kernel]] for the Linux-specific picture.
