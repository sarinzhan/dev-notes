---
title: System calls and file descriptors
tags: [operating-systems, syscalls, linux, file-descriptors]
learned: 2026-10-27
---
## TL;DR
User code cannot touch hardware, other processes or the kernel's data structures directly; it asks the kernel through **system calls**, which switch the CPU from user mode to kernel mode, run kernel code and switch back. Every open resource (file, socket, pipe, timer, epoll instance) is represented to the process as a small integer, a **file descriptor**, indexed into a per-process table that points at kernel objects. "Everything is a file" means you can `read`, `write`, `close` and `poll` most of them with the same calls. `strace` shows the syscalls a process makes, and the fd table is visible in `/proc/<pid>/fd`.

## Key points

### User/kernel boundary
- CPU privilege rings: user mode (ring 3) cannot execute privileged instructions or access kernel memory; kernel mode (ring 0) can. The boundary is what makes [[processes-and-threads|process isolation]] and [[virtual-memory-and-paging|virtual memory]] enforceable.
- A syscall is a controlled entry: put the syscall number and arguments in registers, execute `syscall` (x86-64), the CPU jumps to the kernel entry point, the kernel validates arguments (user pointers are checked, never trusted), does the work, returns a value or `-errno`.
- Cost: around 100–300 ns for the trap itself after Spectre/Meltdown mitigations, plus whatever the call does. Thousands of tiny `read(fd, buf, 1)` calls are slow for the trap, not the bytes; that is why buffered streams exist ([[java-io-streams]]) and why `vDSO` maps `clock_gettime` into user space so `System.nanoTime()` does not trap at all.
- Blocking syscalls (`read` on an empty socket, `accept`, `futex` wait) put the thread to sleep; the kernel wakes it when the event arrives. This is what "a thread is blocked" means at the OS level ([[io-models-blocking-nonblocking-epoll]]).
- Libraries wrap syscalls: `fopen` → `openat`, Java `FileInputStream.read` → JNI → `read`. `strace -f -p <pid>` shows the real calls under any abstraction.

### Common syscall families
| Area | Calls |
|---|---|
| Files | `openat`, `read`, `write`, `pread64`, `lseek`, `fstat`, `fsync`, `close`, `mmap` |
| Processes | `clone`, `execve`, `wait4`, `exit_group`, `kill`, `getpid` ([[linux-processes]]) |
| Sockets | `socket`, `bind`, `listen`, `accept4`, `connect`, `sendto`, `recvfrom`, `setsockopt`, `shutdown` ([[sockets-and-connection-pooling]]) |
| Multiplexing | `poll`, `epoll_create1`, `epoll_ctl`, `epoll_wait`, `io_uring_*` |
| Sync | `futex` (every `synchronized`/`ReentrantLock` contention ends here), `nanosleep` |
| Memory | `brk`, `mmap`, `munmap`, `madvise`, `mprotect` |

### File descriptors
- Per-process **fd table**: index → pointer to an **open file description** (offset, flags, mode) → pointer to the underlying inode/socket. 0, 1, 2 are stdin, stdout, stderr; the next `open` returns the lowest free number.
- `fork()` copies the table: parent and child share the same open file descriptions, including the file offset. `dup2(fd, 1)` redirects stdout; shells implement `>` and `|` with `open`/`pipe` + `dup2` before `execve`.
- `FD_CLOEXEC` / `O_CLOEXEC`: close this fd automatically on `exec`, so a child program does not inherit your database socket. Java sets it on everything it opens.
- Limits: `ulimit -n` (soft, often 1024 on old defaults, 1 048 576 on modern distros and in containers) and `fs.file-max` system-wide. Every socket, every open log file, every `epoll` instance, every JAR the JVM has mapped counts. "Too many open files" (`EMFILE`) is the classic symptom of a leaked `HttpClient`, a connection pool without limits or unclosed `Files.list` streams.
- Sockets are fds too: an HTTP server with 10 k keep-alive connections holds 10 k fds. `ss -tnp` maps sockets to processes; `lsof -p <pid>` or `ls -l /proc/<pid>/fd` lists everything.
- Special fds: `eventfd` (wake-up counter, used by Netty), `timerfd`, `signalfd`, `inotify` (`WatchService` in [[java-nio]]), `memfd`. They exist so that one `epoll_wait` can wait on all of them together.

### "Everything is a file" and where it breaks
- Uniform interface: `cat /proc/cpuinfo`, `echo 1 > /sys/...`, `read` on a socket, `write` on a pipe. Tools compose because of this.
- Not everything fits: sockets need `connect`/`accept`, `ioctl` is the escape hatch for device-specific control, and regular files are always "ready" for `epoll`, so file IO on Linux cannot be truly non-blocking without AIO or `io_uring`.

## Example
```bash
# What does the JVM actually do when it opens a Postgres connection?
strace -f -e trace=socket,connect,setsockopt,write,read -p $(pgrep -f esim-provisioning.jar) 2>&1 | head
#   socket(AF_INET, SOCK_STREAM|SOCK_CLOEXEC, IPPROTO_TCP) = 142
#   setsockopt(142, SOL_TCP, TCP_NODELAY, [1], 4) = 0
#   connect(142, {sa_family=AF_INET, sin_port=htons(5432), sin_addr=...}, 16) = 0
#   write(142, "\0\0\0\10\4\322\26/", 8) = 8            <- SSLRequest / startup message
#   read(142, "N", 1) = 1

# Which fds does the process hold, grouped by type?
ls -l /proc/$(pgrep -f esim-provisioning.jar)/fd | awk '{print $NF}' | sed 's/:.*//' | sort | uniq -c | sort -rn
#   312 socket      <- Hikari pool + Kafka + inbound HTTP keep-alives
#    86 /opt/app/lib/...jar
#     4 anon_inode  <- epoll, eventfd
#     3 /dev/null

# Count syscalls per type over 5 s: find the chatty code path
strace -c -f -p $(pgrep -f esim-provisioning.jar) -- sleep 5 2>&1 | head -15
```
A `futex` count far above `read`/`write` in `strace -c` means lock contention, not IO ([[synchronization-and-monitors]]).

## Pitfalls
- Running `strace` on a hot production JVM: it uses `ptrace`, which stops the process on every syscall and can slow it 10–50×. Use `-c` briefly, or `perf trace` / eBPF (`bpftrace`, `opensnoop`) instead.
- Reading or writing a few bytes per syscall in a loop (unbuffered `FileOutputStream`, `OutputStream.write(int)`): the trap dominates; wrap in a buffer or use `ByteBuffer` batches.
- fd leaks are slow bombs: the service works for days, then every new socket fails with `EMFILE` and the health check still passes because it uses an existing connection. Alert on `process_open_fds` from the JVM Micrometer binder.
- Assuming the container inherits the host's `ulimit -n`: Docker and Kubernetes set their own (`--ulimit nofile`, or the runtime default); check inside the pod.
- `close()` can fail (`EIO` on NFS, `EINTR`) and that is where delayed write errors surface; ignoring its return value loses data silently. Also, closing an fd that another thread is using races: the number may be reused for a new socket immediately.
- Related: [[io-models-blocking-nonblocking-epoll]] for what happens when the fd is not ready, [[linux-memory-filesystems-io]] for `/proc`, `/sys` and page cache behind file syscalls.
