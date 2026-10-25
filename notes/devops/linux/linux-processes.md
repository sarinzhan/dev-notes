---
title: Linux processes, signals and systemd
tags: [linux, processes, signals, systemd, scheduling]
learned: 2026-10-22
---
## TL;DR
A process is a running program with its own address space, file descriptors and PID; threads are schedulable units that share the process's memory. New processes come from `fork()` (clone the parent) followed by `execve()` (replace the image with a new program). The kernel talks to processes with **signals** (`SIGTERM` = please stop, `SIGKILL` = die now), and `systemd` is the service manager that starts, supervises and schedules them.

## Key points
### Processes and threads
- Process: PID, parent PID, uid/gid, virtual address space, open file descriptors, cwd, environment, signal handlers, cgroup and namespaces ([[linux-architecture-and-kernel]]).
- Thread: in Linux a thread is a *task* created with `clone()` that shares memory, fds and signal handlers with its siblings; the kernel schedules tasks, not processes. `ps -eLf` or `top -H` show them. JVM platform threads map 1:1 to these tasks ([[threads-basics]]); virtual threads do not ([[virtual-threads]]).
- Every process has a parent; PID 1 (`systemd`, or your entrypoint inside a container) is the ancestor of all. `pstree -p` shows the tree.

### fork() and execve()
- `fork()` duplicates the calling process: same code, copy-on-write copy of memory, same open fds. Returns twice: child PID in the parent, 0 in the child.
- `execve(path, argv, envp)` replaces the current process image with a new program, keeping PID and (by default) open fds.
- A shell runs `java -jar app.jar` by `fork()` + child `execve("/usr/bin/java")` + parent `waitpid()`. `ProcessBuilder` in Java does the same (via `posix_spawn` or `vfork`).
- Exit status: a process ends with a code 0–255; the parent collects it with `wait()`. Until collected the child is a **zombie** (`Z` in `ps`): dead, but still in the process table. If the parent dies, the orphan is re-parented to PID 1, which must reap it.

### Signals
| Signal | Number | Meaning | Default action |
|---|---|---|---|
| `SIGHUP` | 1 | Terminal closed; by convention "reload config" for daemons (`nginx -s reload`) | terminate |
| `SIGINT` | 2 | Interrupt from keyboard, `Ctrl+C` | terminate |
| `SIGTERM` | 15 | Polite termination request; default for `kill`, `docker stop`, `systemctl stop` | terminate |
| `SIGKILL` | 9 | Immediate kill by the kernel; cannot be caught, blocked or ignored | terminate |
| `SIGSTOP` / `SIGCONT` | 19 / 18 | Pause / resume; `SIGSTOP` also uncatchable | stop / continue |
| `SIGSEGV` | 11 | Invalid memory access | core dump |
| `SIGQUIT` | 3 | `Ctrl+\`; the JVM prints a thread dump instead of dying | core dump |
- `kill -15 <pid>` (or just `kill`) asks; `kill -9` forces. Give the app time to flush, close connections and finish in-flight requests between the two.
- The JVM handles `SIGTERM` by running shutdown hooks; Spring Boot's graceful shutdown (`server.shutdown=graceful`, `spring.lifecycle.timeout-per-shutdown-phase=30s`) stops accepting requests and waits for active ones ([[spring-boot-properties-and-profiles]]).
- Docker sends `SIGTERM`, waits `--stop-timeout` (10 s default), then `SIGKILL`. If the entrypoint is a shell script that does not `exec` the java process, the signal never reaches the JVM ([[docker-containers-and-resources]]).

### Observing and prioritising
- `ps aux` (all processes, BSD style), `ps -ef`, `ps -o pid,ppid,stat,%cpu,%mem,cmd -p <pid>`.
- `top` / `htop`: live CPU and memory; `htop` adds tree view (`F5`), per-thread view (`H`), kill with `F9`.
- Process states in `ps`: `R` running, `S` sleeping (waiting for an event), `D` uninterruptible sleep (usually disk IO; cannot be killed until IO returns), `Z` zombie, `T` stopped.
- Load average (`uptime`) counts runnable *and* `D` tasks; a load above the core count with low CPU usage means IO wait, not CPU shortage.
- `nice -n 10 cmd` starts with lower priority (range -20 highest to 19 lowest); `renice -n 5 -p <pid>` changes a running one. The scheduler (CFS, EEVDF since kernel 6.6) weights CPU time by nice value. `ionice` does the same for disk IO.

### systemd units
- A **service** unit (`/etc/systemd/system/esim.service`) describes how to start a daemon: `ExecStart`, `User`, `Restart=on-failure`, `Environment`, resource limits (`MemoryMax`, `CPUQuota` — cgroups again).
- A **timer** unit replaces cron entries: `OnCalendar=*-*-* 03:00:00` triggers a matching `.service`; `systemctl list-timers` shows the schedule, logs go to the journal.
- `systemctl daemon-reload` after editing a unit; `systemctl enable --now esim`; `journalctl -u esim -f` for logs; `systemctl status esim` shows the cgroup tree and the last log lines.
- Also targets (`multi-user.target`), sockets (socket activation), mounts.

## Example
```ini
# /etc/systemd/system/esim-api.service
[Unit]
Description=eSIM provisioning API
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
User=esim
WorkingDirectory=/opt/esim
EnvironmentFile=/etc/esim/env
ExecStart=/usr/bin/java -XX:MaxRAMPercentage=75 -jar /opt/esim/app.jar
ExecReload=/bin/kill -HUP $MAINPID
KillSignal=SIGTERM
TimeoutStopSec=40            # must exceed spring.lifecycle.timeout-per-shutdown-phase
Restart=on-failure
RestartSec=5
MemoryMax=2G
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now esim-api
journalctl -u esim-api -f --since "10 min ago"
```

## Pitfalls
- `kill -9` on a JVM skips shutdown hooks: unflushed logs, half-written files, Kafka consumer group rebalance delay until session timeout ([[kafka-consumer]]).
- A process in state `D` cannot be killed; the fix is the stuck IO (NFS, dying disk), not another `kill -9`.
- Zombies in a container accumulate when the entrypoint is not a real init; use `--init` or `tini`.
- `Restart=always` on a service that fails in 100 ms makes systemd give up after `StartLimitBurst`; check `systemctl reset-failed`.
- Related: [[linux-architecture-and-kernel]], [[linux-commands]], [[docker-containers-and-resources]].
