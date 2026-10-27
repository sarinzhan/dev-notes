---
title: Inter-process communication mechanisms
tags: [operating-systems, ipc, sockets, pipes, shared-memory]
learned: 2026-10-27
---
## TL;DR
Processes have separate address spaces, so they talk through the kernel: **pipes** (a byte stream between parent and child or between shell commands), **Unix domain sockets** (bidirectional, same-host, used by Docker, Postgres and most sidecars), **TCP sockets** (the same API across hosts), **shared memory** (the fastest, zero copies, but you bring your own synchronization), **message queues** and **signals** (small notifications). The choice is about coupling, speed and whether the other side may be on another machine. Nearly every backend system you touch is IPC: the JDBC connection, the Kafka client, the OTel exporter, the `docker.sock`. Within one process threads just share memory ([[processes-and-threads]]); between services over the network it becomes [[service-communication-sync-vs-async]].

## Key points

### The mechanisms
| Mechanism | Direction | Scope | Data model | Copies | Typical use |
|---|---|---|---|---|---|
| Anonymous pipe (`pipe()`) | one-way | related processes (fork) | byte stream, 64 KiB buffer | 2 (user→kernel→user) | shell `a \| b`, `ProcessBuilder` stdin/stdout |
| Named pipe (FIFO) | one-way | same host, by path | byte stream | 2 | log forwarding between unrelated daemons |
| Unix domain socket | two-way | same host, by path | stream or datagram, can pass fds and credentials | 2 | `/var/run/docker.sock`, Postgres `/var/run/postgresql/.s.PGSQL.5432`, systemd socket activation, Envoy/Istio sidecars |
| TCP/UDP socket | two-way | any host | stream / datagram | 2 + network stack | everything remote: HTTP, gRPC, Kafka, JDBC over TCP |
| Shared memory (`shm_open`+`mmap`, SysV `shmget`) | n-way | same host | raw bytes | 0 | Postgres shared buffers between backends, Chrome renderer ↔ GPU, Aeron/Chronicle in trading |
| Memory-mapped file | n-way | same host | raw bytes, persistent | 0 (page cache) | Kafka log segments, Lucene, `MappedByteBuffer` |
| POSIX/SysV message queue | n-way | same host | discrete messages with priority | 2 | rare in modern backends; replaced by brokers |
| Signals | one-way | same host | a number (plus `siginfo`) | 0 | `SIGTERM`, `SIGHUP` reload ([[signals-and-process-lifecycle]]) |
| eventfd / futex | one-way | same host | counter | 0 | wakeups between threads/processes; Netty, JVM locks |
| D-Bus / gRPC over UDS | two-way | same host | typed RPC | 2 | desktop services; Kubernetes CRI/CSI plugins talk to kubelet over UDS |

### Streams vs messages
- Pipes and stream sockets deliver **bytes**, not messages: two `write(100)` calls may arrive as one `read(200)` or three partial reads. Any protocol on top needs framing: length prefix (gRPC, Kafka, Postgres wire protocol), delimiter (HTTP/1.1 headers, newline-delimited JSON), or fixed size ([[http-fundamentals]], [[grpc-and-protobuf]]).
- Datagram sockets and message queues preserve boundaries; UDP additionally drops and reorders ([[tcp-vs-udp]]).
- Pipe atomicity: writes up to `PIPE_BUF` (4 KiB) are atomic, which is why many processes can append single log lines to one pipe without interleaving, but longer lines can interleave.

### Unix domain sockets vs localhost TCP
- Same `socket()`/`connect()`/`read()` API, so a client library often supports both (`jdbc:postgresql://localhost/db` vs a UDS path via `socketFactory`).
- UDS skips the TCP/IP stack: no checksums, no loopback routing, no port numbers, ~2× throughput and lower latency for small messages; the kernel can pass **file descriptors** (`SCM_RIGHTS`) and the peer's uid/pid (`SO_PEERCRED`) over it, which is how Docker authorises clients by group membership on `docker.sock` ([[docker-socket-dind-dood]]).
- Access control is filesystem permissions on the path instead of firewall rules. Kubernetes sidecars and CNI/CSI plugins use UDS mounted via `hostPath`/`emptyDir`.

### Shared memory: fastest, most dangerous
- The kernel maps the same physical pages into two address spaces; after that, communication is a memory write, no syscall. Throughput is bounded by memory bandwidth, latency by cache coherence ([[cpu-caches-and-cache-lines]]).
- No synchronization comes with it: you need a mutex or semaphore in the shared segment (`PTHREAD_PROCESS_SHARED`), or a lock-free ring buffer with memory barriers ([[lock-free-and-cas]], [[memory-models-and-visibility]]). A crashed writer can leave the structure half-updated; robust mutexes exist precisely for this.
- Postgres is the example you already run: every backend process attaches to `shared_buffers`, WAL buffers and lock tables in shared memory; `huge_pages=try` is about this segment ([[postgres-performance-checklist]]).
- Java: `FileChannel.map` on a file in `/dev/shm` gives a `MappedByteBuffer` shared across processes (Chronicle Queue, Aeron).

### Choosing
- Parent–child, one direction: pipe (`ProcessBuilder`). Same host, unrelated processes: Unix domain socket; localhost TCP only when the peer may move to another host. Same host, high volume and low latency: shared memory with a ring buffer. Different hosts: TCP, then pick the protocol ([[service-communication-sync-vs-async]], [[message-queues-and-streams]]).

## Example
```bash
# 1. Pipes: an anonymous pipe is just two fds
strace -e trace=pipe2,dup2,clone,execve sh -c 'ps aux | grep java' 2>&1 | head -6
#   pipe2([3, 4], 0) = 0
#   clone(...) = 5120                 <- ps:   dup2(4, 1) then execve
#   clone(...) = 5121                 <- grep: dup2(3, 0) then execve

# 2. Unix domain sockets on a typical node
ss -xlp | grep -E 'docker|postgres|otel'
#   u_str LISTEN 0 4096 /var/run/docker.sock 23011 * 0  users:(("dockerd",pid=1210,fd=5))
#   u_str LISTEN 0 224 /var/run/postgresql/.s.PGSQL.5432 ... users:(("postgres",pid=2231,fd=6))

# 3. Shared memory: who is attached to the Postgres segment?
ls -l /dev/shm | head            # POSIX shm objects (PostgreSQL.xxxx, otel, JVM perf data)
ipcs -m                           # SysV segments, nattch column = attached processes
grep -E 'Shmem|Mapped' /proc/meminfo
```
`ls /proc/<pid>/fd -l` shows the same categories for one process: `pipe:[inode]`, `socket:[inode]`, and `/dev/shm/...` mappings appear in `/proc/<pid>/maps` ([[syscalls-and-file-descriptors]]).

## Pitfalls
- Reading only stdout of a child process: when its stderr pipe fills (64 KiB), the child blocks on `write` and your `waitFor()` hangs forever. Use `redirectErrorStream(true)` or drain both.
- Treating a stream socket as message-based: "it worked locally" because small writes rarely split on loopback; it breaks under load or across the network. Always frame.
- Mounting `docker.sock` into a container for convenience: whoever can write to that socket is root on the host; it is IPC with no authorisation beyond file permissions.
- Shared memory without a cleanup owner: SysV segments and `/dev/shm` files outlive the processes that created them and quietly consume RAM until a reboot (`ipcrm`, or `shm_unlink`).
- Named pipes with one reader and many writers above `PIPE_BUF`: interleaved garbage in the output.
- Related: [[sockets-and-connection-pooling]] for the TCP flavour in depth, [[io-models-blocking-nonblocking-epoll]] for waiting on many of these fds at once.
