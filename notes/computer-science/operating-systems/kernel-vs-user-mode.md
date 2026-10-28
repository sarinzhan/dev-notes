---
title: Kernel vs user mode
tags: [operating-systems, kernel, syscalls, cpu, linux, performance]
learned: 2026-10-28
order: 9
---
## TL;DR
The CPU is always in one of two modes. In **user mode** (ring 3 on x86, EL0 on ARM) a process runs its own code and can touch only its own memory. In **kernel mode** (ring 0, EL1) the same CPU can execute privileged instructions and access everything: devices, page tables, every process's memory. The kernel is not a separate process that gets switched in: it is the **same process, elevated**. Every process's virtual address space has the kernel mapped into its upper half, marked supervisor-only, so user code faults if it touches it. A thread enters kernel mode in three ways: a **system call** (it asks), an **interrupt** (hardware), or an **exception** such as a **page fault**. Each switch saves the user's registers on the kernel stack, runs security checks and a system-call-table lookup, does the work, restores the registers and returns, which costs on the order of 100 ns plus the work. Time spent in the kernel on behalf of a process is counted separately, as **system time**. Many small system calls, page faults and context switches are why "sys" CPU climbs.

## Key points
### Two modes, one process
- A flag in the CPU says which mode it is in. **User mode** executes the program's own code: its text, heap, stack and libraries. **Kernel mode** can do everything: run privileged instructions (load the page-table register, disable interrupts, talk to devices) and access any memory.
- Kernel code is code like any other, but it lives in kernel pages and can only run in kernel mode. To execute even a system call, the CPU must switch modes first.
- A common myth (the lecturer had it too): "the kernel is a process that gets switched in." It is not. When a process makes a system call, that **same process and thread** continue in kernel mode, on the thread's own kernel stack, with the same page table. A different process only runs after a **context switch** ([[process-management]]).

| | User space | Kernel space |
|---|---|---|
| What runs there | Shell, systemd, Docker daemon, libraries, every application | Kernel, drivers, kernel modules, scheduler, network stack, file systems |
| CPU mode | Unprivileged: ring 3 / EL0 | Privileged: ring 0 / EL1 |
| Memory it can access | Only the process's own user pages | User pages of the current process and all kernel pages |
| Hardware access | None; every hardware operation goes through a system call | Full |
| Effect of a crash | Only that process dies | Kernel panic: the whole OS goes down |
| How to get in | — | `syscall` instruction, interrupts, exceptions (page faults) |

### The address space split
- Every process's virtual address space is split in two. On x86-64 Linux the user half is `0` … `0x0000_7fff_ffff_ffff` (128 TiB): text, data, heap, mmaps, stacks ([[anatomy-of-a-process]]). The kernel half starts at `0xffff_8000_0000_0000`: kernel code, the system call table, page tables, process control blocks, socket buffers, the page cache, kernel stacks.
- Kernel pages are marked **supervisor-only** in the page tables. In user mode, any access to them makes the MMU raise a fault, and the process gets `SIGSEGV`. That is the protection: a process cannot read or corrupt the kernel, or other processes through the kernel.
- The user half maps differently in every process (address 1000 points to different physical memory in each), which is why a process switch changes the page table ([[memory-management]]). The kernel half is **the same in every process's page table**: some kernel data is specific to one process (its PCB, its kernel stack), most is shared.
- Inside the kernel half, Linux maps all physical memory linearly at a fixed offset (the "direct map"): kernel virtual address = physical address + a constant. The course calls this a 1:1 mapping; strictly the addresses still go through the page tables and the TLB, but the translation is fixed and uses huge pages.
- Because the kernel half is identical everywhere, its TLB entries are marked **global** and survive context switches, and a mode switch does not change the page table at all, so nothing is flushed. Since Meltdown (2018), many CPUs use **kernel page-table isolation** (KPTI): user mode runs on a page table with almost no kernel mappings, so every mode switch does change the page table, and PCID tags keep that from flushing the TLB.

### Three ways into kernel mode
| Entry | Who triggers it | Examples |
|---|---|---|
| **System call** | the program, deliberately | `read`, `write`, `open`, `mmap`, `futex`, `epoll_wait`, `clone` ([[syscalls-and-file-descriptors]]) |
| **Interrupt** | hardware, asynchronously, whatever is running | timer tick (the scheduler may preempt the thread), NIC received packets, disk finished a request |
| **Exception** | the current instruction | page fault, divide by zero, invalid instruction, breakpoint |
- A **page fault** is the sneaky one: a plain load from your own heap becomes kernel work if the page is not mapped yet (minor fault: allocate and map a frame, ~1 µs) or was swapped out (major fault: read it back from disk, milliseconds). Swap space, page allocation and the page tables are all kernel-only, so the user can do nothing about it but wait. That is why page faults are expensive ([[memory-management]]).

### What happens in a system call
1. User code calls `read(fd, buf, n)`. The libc wrapper puts the system call number in a register (`rax` = 0 for `read` on x86-64) and the arguments in others (`rdi`, `rsi`, `rdx`), then executes the `syscall` instruction.
2. The CPU switches to kernel mode and jumps to the kernel's fixed entry point; the kernel switches to the thread's **kernel stack** (the system call's own frames live in the kernel area).
3. It **saves the user's state** on the kernel stack: the return address (user program counter), stack and base pointers, and the other registers it is going to use. These are writes to memory.
4. **Security checks**: is the fd one of this process's open files? Is `buf` really a user address, so the kernel will not be tricked into writing over its own memory? Does the process have permission?
5. **System call number lookup**: the number indexes the **system call table**, `sys_call_table[0]` → `sys_read`. Programs are compiled against stable numbers (the ABI), not against kernel addresses, which differ between kernels and boots.
6. `sys_read` runs on the kernel stack. Kernel mode can access both halves, so it copies data from the socket's receive buffer or the page cache into `buf`. If no data is ready it puts the thread to sleep ([[socket-management]]).
7. It **restores** the user registers (memory reads), puts the result in `rax` and executes `sysret`: the CPU drops back to user mode at the instruction after `syscall`, and execution resumes.
- Also part of the switch: the process's statistics. CPU time used so far is accounted to the process in its PCB as user time, and the time inside the kernel as system time.

```anim
mode-switch-syscall
```

### What a mode switch costs
- **Direct cost**: saving and restoring registers (memory writes and reads), the security checks, the table lookup and the entry/exit instructions. Roughly 50–100 ns for a trivial system call like `getpid` on a modern CPU, a few hundred ns with Spectre and Meltdown mitigations (KPTI, retpolines, flushing CPU buffers).
- **Indirect cost**: the kernel's code and data displace the program's own lines in the L1/L2 caches and the branch predictor, so the user code runs slower for a while after returning.
- Compared with a **context switch** (one process or thread replaced by another: a few µs plus cold caches, and for processes a TLB flush), a mode switch is cheaper, but it happens far more often: a busy server makes hundreds of thousands of system calls per second.

| | Mode switch | Context switch |
|---|---|---|
| What changes | privilege level, stack; same thread | the running thread (and for processes, the page table) |
| Triggered by | system call, interrupt, exception | scheduler: time slice over, thread blocked, higher priority woke up |
| Saves | user registers on the kernel stack | all registers into the TCB/PCB |
| TLB | kept (kernel pages are global; PCID with KPTI) | kept for threads of the same process; flushed or tagged for a different process |
| Typical cost | ~0.1–0.5 µs | ~1–5 µs plus cold caches |

- How to pay less:
  - **Bigger IO per call**: buffered streams instead of one-byte writes ([[java-io-streams]]), `writev`/`readv`, `sendmmsg`, bulk `read` into a large `ByteBuffer`.
  - **Fewer calls per event**: `epoll` instead of `select`; `io_uring` batches many operations into one system call or none ([[socket-management]]).
  - **No call at all**: the **vDSO** is a small piece of kernel code mapped into every process so that `clock_gettime` and `gettimeofday` run in user mode; Java's `System.nanoTime()` and `currentTimeMillis()` never enter the kernel. `mmap` turns file reads into memory accesses (but page faults instead of calls).
  - **Fewer page faults**: pre-touch the heap (`-XX:+AlwaysPreTouch`), avoid swap.

### User time vs system time
- The kernel keeps per-process statistics in the PCB. CPU time is split by mode: **user time** (the process's own code) and **system time** (kernel code running on behalf of the process: its system calls and page faults). The course says kernel time is "not counted" for the process; it is counted, just separately.
- `time` prints `real` (wall clock), `user` and `sys`; `top` shows `%us`, `%sy`, plus `%hi`/`%si` for hardware and software interrupts (not charged to any process) and `%wa` for IO wait; `/proc/<pid>/stat` has `utime` and `stime`.
- Time spent **asleep** in the kernel (waiting for the disk, the network, a lock) is neither user nor system time: that is why `real` can be far bigger than `user + sys`.
- Reading it: high `%sy` means the CPU is busy in the kernel: too many small system calls, lock contention ending in `futex`, page faults, or network processing. High `%us` means your own code. `perf top` and `strace -c` tell which.

```anim
kernel-entries
```

## Example
```bash
# How much of the CPU time is kernel time?
time java -jar batch-import.jar
#   real 41.2s   user 22.8s   sys 9.6s     <- sys is a third of the CPU: look at system calls

# Which system calls, how many, how long (short runs only: strace slows the process down a lot)
strace -c -f -p $(pgrep -f batch-import.jar) -- sleep 5
#   % time   seconds  usecs/call     calls  syscall
#    61.30  1.402113          3    412340  write      <- 412k writes in 5 s: unbuffered output
#    20.11  0.459812         11     40102  futex

# The same with low overhead: perf counts mode switches and faults
perf stat -e syscalls:sys_enter_write,page-faults,context-switches -p $(pgrep -f batch-import.jar) -- sleep 5

# Per-process user vs system time
pidstat -u -p $(pgrep -f batch-import.jar) 1 3      # %usr %system per second
```
412,340 `write` calls in 5 seconds is an unbuffered stream writing a few bytes at a time: wrapping it in a `BufferedOutputStream` cuts the calls by a factor of a thousand and most of the `sys` time with them.

## Pitfalls
- Thinking a system call is "just a function call". It is a mode switch with register saving, checks and cache pollution; in a hot loop the switches, not the work, dominate.
- One-byte reads and writes, `flush()` after every line, logging synchronously to a file on every request: each is a system call.
- Ignoring `%sy` in dashboards. A service at 70% CPU with 40% `sy` will not get faster from optimising Java code; it needs fewer system calls, less lock contention or less swapping.
- Assuming `System.currentTimeMillis()` is a system call worth avoiding: through the vDSO it is a few nanoseconds. (In some VMs with an unstable clock source the vDSO falls back to a real system call; check `/sys/devices/system/clocksource/clocksource0/current_clocksource` for `tsc`.)
- Running `strace` on a production process for long: it stops the process on every system call through `ptrace` and can slow it down 10–100×. Use `strace -c` briefly, `perf trace`, or eBPF tools.
- Believing kernel mode is "another process": a blocked `read()` means the same thread sleeps inside the kernel; thread dumps show it as `RUNNABLE` in a native method while it sleeps ([[threads-basics]]).
- Related: [[syscalls-and-file-descriptors]] for the system call families and file descriptors, [[cpu]] for registers and privilege levels, [[operating-system-architecture]] for the overview, [[linux-architecture-and-kernel]] for the Linux-specific picture.
