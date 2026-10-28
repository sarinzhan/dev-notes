---
title: Signals and the process lifecycle
tags: [operating-systems, signals, processes, containers, linux]
learned: 2026-10-27
---
## TL;DR
A Unix process is born by `fork()` (clone the parent) and usually `execve()` (replace itself with a new program), lives until it calls `exit()` or is killed by a signal, and stays a **zombie** until its parent `wait()`s for its exit status. **Signals** are the kernel's asynchronous notifications: `SIGTERM` asks a process to shut down and can be handled, `SIGKILL` destroys it and cannot. In containers the process you start is PID 1, which changes two things: default signal handling is off for it, and it must reap orphans. Graceful shutdown of a JVM service is a chain: orchestrator sends `SIGTERM` → JVM runs shutdown hooks → Spring stops accepting requests and drains → process exits 0 before the kill timeout. The Linux command-level view (`ps`, `kill`, signal table, `systemd`) is in [[linux-processes]].

## Key points

### Lifecycle
```c
// C-like pseudocode: what every shell and ProcessBuilder does
pid_t pid = fork();                 // returns twice: child pid in parent, 0 in child
if (pid == 0) {
    dup2(logfd, STDOUT_FILENO);     // redirect before exec; fds survive exec
    execve("/usr/bin/java", argv, envp);   // never returns on success
    _exit(127);                     // only reached if exec failed
}
int status;
waitpid(pid, &status, 0);           // blocks; reaps the zombie, gets the exit code or killing signal
```
- `fork` is cheap thanks to copy-on-write pages ([[memory-management]]); `posix_spawn`/`vfork` skip even that, which is what the JDK uses for `ProcessBuilder` since JDK 12.
- Exit status: 8-bit code from `exit(n)`; if killed by a signal the shell reports `128 + signum`. Exit 137 = `SIGKILL` (OOM killer or kill timeout), 143 = `SIGTERM`, 139 = `SIGSEGV`.
- **Zombie**: exited but not yet waited for; holds only a PID and the status. Harmless in small numbers, a PID-table leak if the parent never reaps. **Orphan**: parent died first; re-parented to PID 1 (or the nearest subreaper), which must reap it.
- `D` (uninterruptible sleep, usually disk/NFS IO) is the one state where even `kill -9` waits until the syscall returns; other `ps` states are in [[linux-processes]].

### Signals
- Delivery: the kernel sets a pending bit; the signal is handled when the target thread next returns to user mode. In a multithreaded process a process-directed signal goes to **one** thread that does not block it; the JVM dedicates a "Signal Dispatcher" thread.
- Dispositions: default action (terminate, core dump, stop, ignore), ignore (`SIG_IGN`), or a handler. `SIGKILL` and `SIGSTOP` cannot be caught, blocked or ignored.
- Handlers run in the middle of whatever the thread was doing, so only async-signal-safe functions are allowed inside (no `malloc`, no locks). Real programs set a flag or write to a pipe/`signalfd` and handle the event on the main loop; the JVM's native handler enqueues and the dispatcher thread runs Java code.
- Syscalls interrupted by a signal return `EINTR`. Java's `Thread.interrupt()` is unrelated to signals: it is a flag plus a wakeup of parked threads ([[threads-basics]]).
- JVM uses of signals: `SIGSEGV` for implicit null checks and safepoint polls, `SIGQUIT` (`kill -3`) for a thread dump to stdout, `SIGTERM`/`SIGINT`/`SIGHUP` to trigger shutdown hooks. `-Xrs` disables the latter.

### SIGTERM vs SIGKILL and graceful shutdown
| | `SIGTERM` (15) | `SIGKILL` (9) |
|---|---|---|
| Can be handled | yes | no |
| What the JVM does | runs `Runtime.addShutdownHook` hooks, then exits 143 | nothing; the kernel frees everything, exit 137 |
| In-flight work | can be drained | lost; connections reset, files half-written |
| Who sends it | `kill`, `docker stop`, kubelet on pod deletion, `systemctl stop` | the same callers after a timeout; the OOM killer |
- Spring Boot: `server.shutdown=graceful` and `spring.lifecycle.timeout-per-shutdown-phase=20s` stop accepting new connections, finish active requests, then close pools and Kafka consumers in reverse bean order ([[spring-bean-lifecycle]], [[spring-kafka-consumer]]).
- Kubernetes sequence: pod marked Terminating → endpoints removal and `SIGTERM` happen **in parallel** → `terminationGracePeriodSeconds` (30 s default) → `SIGKILL`. Because endpoint removal lags, a `preStop` sleep of 5–10 s before `SIGTERM` avoids sending new requests to a server that is already draining ([[kubernetes-basics]]).
- Budget: `preStop` + graceful timeout + Kafka rebalance and hook time must fit inside `terminationGracePeriodSeconds`, or the JVM is killed mid-drain anyway.

### PID 1 in containers
- The kernel does not apply default signal actions to PID 1: a `SIGTERM` to a PID 1 without a handler is **ignored**. The JVM installs handlers, so `java` as PID 1 is fine; a shell script entrypoint is not.
- `sh -c "java -jar app.jar"` makes `sh` PID 1 and `java` its child; `docker stop` signals `sh`, which ignores it, and after 10 s everything is `SIGKILL`ed. Fix: `exec java -jar app.jar` in the script, or `ENTRYPOINT ["java", "-jar", "app.jar"]` exec form ([[dockerfile-and-images]]).
- PID 1 must also reap orphaned zombies; `java` never calls `wait()` for processes it did not spawn. If the app forks helpers, use `tini` or `docker run --init` as PID 1.

## Example
```bash
# Observe a graceful shutdown end to end
PID=$(pgrep -f esim-provisioning.jar)
strace -f -e trace=rt_sigaction,shutdown,close,exit_group -p $PID 2>&1 &
kill -TERM $PID
#   --- SIGTERM {si_signo=SIGTERM, si_pid=3301} ---
#   [pid 4120] shutdown(104, SHUT_RD)      <- Tomcat stops reading new requests
#   [pid 4155] close(142)                  <- Hikari closes Postgres sockets
#   exit_group(143)                        <- shutdown hooks finished before the kill timeout

# Confirm which process is PID 1 in the container (the whole graceful story depends on it)
kubectl exec esim-provisioning-7c9d -- cat /proc/1/cmdline | tr '\0' ' '
#   java -XX:MaxRAMPercentage=70 -jar /app/app.jar       <- good
#   /bin/sh -c java -jar /app/app.jar                      <- SIGTERM is ignored

# Zombies piling up under a non-reaping PID 1
ps -eo pid,ppid,stat,cmd | awk '$3 ~ /Z/'
```
Exit code 143 in the pod's last-state means the JVM ended on its own after `SIGTERM`; 137 means it was still running when the grace period expired or the cgroup ran out of memory.

## Pitfalls
- Shell-form `ENTRYPOINT`/`CMD` or a wrapper script without `exec`: every deploy becomes a `SIGKILL` after the timeout, in-flight requests fail and Kafka rebalances take the full session timeout.
- Doing slow work in a shutdown hook without a deadline (flush to a remote service, wait for a lock): if it exceeds the grace period, `SIGKILL` arrives anyway and the half-finished state is worse than a fast stop.
- Relying on `finally` blocks or `@PreDestroy` running on `SIGKILL`: nothing runs. Design for crash-safety (idempotent consumers, [[outbox-pattern]]) rather than for clean exits.
- `kill -9` as the first reflex on a `D`-state process: it does nothing until the IO returns, and it hides the real problem (a dead NFS mount or a failing disk).
- Forgetting that readiness probes and endpoint removal are asynchronous: without `preStop` delay, a draining pod still receives traffic for a few seconds and returns connection refused.
- Related: [[linux-processes]] for the signal table and `systemd`, [[docker-containers-and-resources]] for `--stop-timeout`, `--init` and exit codes in practice.
