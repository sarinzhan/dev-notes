---
title: Linux architecture, kernel space and user space
tags: [linux, kernel, syscalls, namespaces, cgroups]
learned: 2026-10-22
---
## TL;DR
A Linux system is a stack: hardware, the kernel, system libraries (glibc), system daemons, shells and utilities, user applications. Only the kernel runs in privileged CPU mode (ring 0) and touches hardware; everything else runs in user space (ring 3) and reaches the kernel exclusively through **system calls**. Namespaces and cgroups, both kernel features, are what containers are made of ([[docker-fundamentals]]).

## Key points
### Abstraction levels of an ordinary Linux system
1. **Hardware**: CPU, RAM, SSD, GPU, NIC. Executes machine instructions only.
2. **Kernel**: the central layer of the OS. Responsibilities: process management and scheduling, memory management, filesystems, device drivers, networking, security. Ubuntu or Debian are *distributions* on top of the same kernel.
3. **System libraries**: the layer applications use to talk to the kernel. When a program calls `printf()`, glibc eventually issues the `write()` system call. Usual set: glibc (GNU C library, with libm and libpthread) or musl on Alpine.
4. **User-space utilities and system daemons**: background system programs: `systemd` (init and service manager), `udev` (device events), `cron`, `sshd`, `dockerd`.
5. **Shell**: a command interpreter, an ordinary user-space program running on top of the kernel. Interprets commands and launches programs. `sh` is the historical POSIX shell, `bash` the extended GNU version, `zsh`/`fish` alternatives.
6. **Core GNU tools**: `ls`, `cat`, `ps`, `chmod`, `grep` ([[linux-commands]]).
7. **User applications**: your JVM, Postgres, nginx.

### Kernel space vs user space
| | Kernel space | User space |
|---|---|---|
| What runs there | Kernel, drivers, kernel modules, scheduler, network stack, filesystems | Shell, GUI, systemd, terminal, Docker daemon, libraries, every application |
| CPU mode | Privileged, ring 0 | Unprivileged, ring 3 |
| Hardware access | Full | None; every hardware operation goes through a syscall |
| Effect of a crash | Kernel panic, the whole OS goes down | Only that process dies |
| How to get in | Cannot be entered directly by a normal program; only via `read()`, `write()`, `open()`, `fork()`, `mmap()`, ... | — |

### System calls
- The kernel's API: a few hundred functions (`open`, `read`, `write`, `socket`, `clone`, `execve`, `mmap`, `futex`). Each one switches the CPU to kernel mode, runs kernel code, switches back; this context switch costs roughly a microsecond, which is why batching (buffers, `epoll`, [[java-nio]]) matters.
- Libraries wrap them: `FileInputStream.read()` -> JNI -> glibc `read()` -> `SYS_read`.
- `strace -f -p <pid>` shows every syscall a process makes; `strace -c` aggregates them. It is the fastest way to see *why* a program hangs (blocked in `futex`, `read` on a socket, `connect` timeout).

### Init systems and roles
- The kernel starts exactly one user process, PID 1 (**init**), which starts everything else. `systemd` is the init on nearly all distros; `OpenRC` (Alpine, Gentoo) and `runit` (Void) are alternatives.
- PID 1 reaps orphaned zombie processes. In a container the entrypoint becomes PID 1, which is why `tini` or `--init` exists ([[docker-containers-and-resources]]).

### Inside the kernel
- **Device drivers / kernel modules**: loaded on demand; `lsmod` lists, `modprobe <mod>` loads with dependencies, `insmod` loads a single `.ko`, `rmmod` unloads.
- **sysfs (`/sys`) and procfs (`/proc`)**: in-memory pseudo-filesystems that expose kernel objects as files. `cat /proc/<pid>/status`, `/proc/meminfo`, `/sys/class/net/eth0/`; writing to `/proc/sys/...` (or `sysctl -w`) tunes the kernel at runtime.
- **Scheduler**: CFS (Completely Fair Scheduler) since 2.6.23 gives each runnable task a fair share of CPU time weighted by nice value; replaced by **EEVDF** in kernel 6.6 (2023), same interface. See [[linux-processes]].
- **Namespaces**: isolate what a process *sees*. PID (own process tree), NET (own interfaces, routing table, ports), MNT (own mount table), IPC (own shared memory and semaphores), UTS (own hostname), USER (uid mapping, root inside = unprivileged outside), plus cgroup and time. The basis of containers.
- **cgroups** (control groups): limit and account what a process *uses*: CPU shares and quotas, memory, block IO, pids. cgroups v2 (unified hierarchy, default since systemd 248 / Ubuntu 22.04). The basis of `docker run --memory` and Kubernetes requests/limits.
- A container = a process started with `clone()` into new namespaces, placed in a cgroup, with a different root filesystem (`pivot_root`) and a reduced capability set.

## Example
```bash
# See the syscalls behind a JVM that "hangs" on startup
sudo strace -f -tt -e trace=network,connect,openat -p "$(pgrep -f app.jar)" 2>&1 | head -40
# 12:03:11.118 connect(27, {sa_family=AF_INET, sin_port=htons(5432), ...}, 16) = -1 EINPROGRESS
# 12:03:41.120 ... ETIMEDOUT   -> the DB host is unreachable, not the JVM

# Namespaces of a running container's main process
PID=$(docker inspect -f '{{.State.Pid}}' esim-api)
sudo ls -l /proc/$PID/ns/            # pid, net, mnt, ipc, uts, user, cgroup
sudo nsenter -t $PID -n ss -ltnp      # run ss inside the container's NET namespace
cat /sys/fs/cgroup/system.slice/docker-*.scope/memory.max   # cgroup v2 memory limit
```

## Pitfalls
- "The container is a lightweight VM" is wrong: it shares the host kernel, so a kernel bug or a privileged container affects the host. Kernel version differences also explain why an image works on one host and not another.
- `strace` slows the traced process significantly; do not leave it attached in production.
- Changing `/proc/sys` values is not persistent; put them in `/etc/sysctl.d/*.conf`.
- The JVM reads cgroup limits for its default heap size only since JDK 10 (and cgroup v2 since 11.0.16 / 15); older JVMs see host memory and get OOM-killed ([[jvm-memory-areas]], [[garbage-collection]]).
- Related: [[linux-basics]], [[linux-memory-filesystems-io]], [[docker-containers-and-resources]].
