---
title: Socket management
tags: [operating-systems, sockets, tcp, kernel, networking, linux]
learned: 2026-10-28
order: 8
aliases: [io-models-blocking-nonblocking-epoll]
---
## TL;DR
A **socket** is a kernel data structure (a `struct sock` in Linux) that the process sees as a **file descriptor**. A server creates a **listening socket** on an IP and port; the kernel attaches two queues to it: the **SYN queue** for half-finished handshakes and the **accept queue** for finished connections, whose size is the `listen()` **backlog**. The kernel runs the whole TCP handshake itself; the application only calls `accept()`, which takes a connection out of the accept queue and gives the process a new file descriptor with its own **send** and **receive queues**. If the application does not accept fast enough, the accept queue fills up and new clients time out; if attackers never finish their handshakes, the SYN queue fills up (SYN flood, answered by SYN cookies). To accept on many cores, processes either share one listening socket through `fork`, or each opens its own socket on the same port with `SO_REUSEPORT` and lets the kernel load-balance (**socket sharding**). Data then flows through the buffers: the kernel ACKs incoming bytes, `read()` copies them out, `send()` copies into the send buffer, and a slow reader shrinks the TCP window until the sender stops. `read`, `write` and `accept` block, so serving many sockets on few threads needs either **readiness** notification (`select`, `epoll`, `kqueue`: "which fds can I use now?") or **completion** (`io_uring`, IOCP: "the kernel did the IO, here is the result").

## Key points
### What a socket is
- In the kernel a socket is a struct holding its addresses, state, queues and options. To the process it is a **file descriptor** in its PCB's file table ([[process-management]], [[syscalls-and-file-descriptors]]): in Linux everything is a file, so `read`, `write`, `close`, `epoll` work on sockets like on files. On Windows a socket is a handle to a separate object (`SOCKET`).
- The process **owns** the socket: its fd lives in that process's table, and when the process exits the kernel closes it.
- A socket can be shared with child processes through `fork`, since the child inherits a copy of every fd ([[signals-and-process-lifecycle]]).
- Two kinds share the same structure:
  - a **listening socket**: bound to a local IP and port, no peer. It produces connections but carries no data.
  - a **connection socket**: one end of an established connection, identified by the 4-tuple (source IP, source port, destination IP, destination port). A client only ever has connection sockets.

### Listening on an IP and a port
- A machine has several IP addresses: one per network interface (NIC), plus any number of virtual interfaces, plus the **loopback** `127.0.0.1` (IPv4) and `::1` (IPv6) that points back to the machine itself ([[ip-addressing-and-nat]]).
- You listen on a specific **IP + port**. Binding to `127.0.0.1:8080` accepts only local connections, which is what development servers should do.
- `0.0.0.0` (IPv4) or `::` (IPv6) means **all interfaces**. Many frameworks default to it because the IP may change and it is easier. It is dangerous: deploy that "local" tool to a server where one interface has a public address, and it is on the internet. Mass breaches of MongoDB and Elasticsearch came exactly from this: databases listening on all interfaces, found by attackers who scan the entire IPv4 space for their ports. Bind to the interface you mean; in Spring Boot that is `server.address`.

### The socket lifecycle in system calls
| Step | Server side | Client side | Kernel state |
|---|---|---|---|
| create | `socket()` returns an fd | `socket()` | fd allocated, no address |
| name | `bind(0.0.0.0:8080)`, `listen(fd, backlog)` | implicit bind to an ephemeral port | `LISTEN` / none |
| connect | `accept()` returns a new fd per client | `connect()` runs the handshake | `ESTABLISHED` |
| transfer | `read()`/`write()` through the connection's queues | same | send/receive buffers, window |
| end | `close()` or `shutdown()` | `close()` | FIN exchange, `TIME_WAIT` on the active closer ([[tcp-handshake-and-connection-states]]) |
- Strictly, `socket()` creates the fd and `listen()` only marks it as passive (it returns 0); the course's "listen returns the socket" folds the three calls into one. Frameworks do all of this for you: `app.listen(8080)` in Node.js, Tomcat's connector in Spring Boot.
- The listening socket and the per-connection sockets are different fds: a server holding 10,000 clients has 10,000 fds plus one. `ulimit -n` (1024 by default on some images) is a real ceiling; give a server process 65,536.

- **A connection is identified by four values**: source IP, source port, destination IP, destination port. When one client opens a thousand connections to the same server, only the source port differs, so the kernel can still tell them apart. That also limits a single client: the ephemeral port range (`net.ipv4.ip_local_port_range`, 32768–60999 by default, about 28,000 ports) caps connections from one IP to one destination IP:port, and a service that opens and closes connections fast can run out of ports while old ones sit in `TIME_WAIT`. Connection pools avoid this.

### SYN queue and accept queue
- Creating a listening socket gives it two queues:
  - **SYN queue**: incoming SYNs whose handshake is not finished yet (`SYN_RECV`).
  - **Accept queue**: completed connections waiting for the application to call `accept()`.
- Not really queues but **hash tables**: the kernel must find an entry by its 4-tuple when the ACK arrives. We call them queues because connections go through them in order.
- Their size comes from the **backlog** argument of `int listen(int sockfd, int backlog)`. On Linux the backlog limits the accept queue, capped by `net.core.somaxconn` (4096 by default since kernel 5.4, 128 before). The SYN queue is limited separately by `net.ipv4.tcp_max_syn_backlog`. Frameworks expose the backlog: `server.listen(port, backlog)` in Node.js, `server.tomcat.accept-count` in Spring Boot (default 100), `ChannelOption.SO_BACKLOG` in Netty, `new ServerSocket(port, backlog)` in plain Java.
- When a SYN arrives, the kernel first looks up which listening socket owns the destination IP and port; this lookup has to be very fast, so the listener tables are hash tables kept hot in the CPU caches. No match means the kernel answers with RST: "connection refused".

### Connection establishment
1. The server listens on an address:port; the kernel creates the socket and its SYN and accept queues.
2. The client sends a SYN.
3. The kernel adds it to the SYN queue and replies with a SYN-ACK.
4. The client replies with an ACK.
5. The kernel finishes the connection: it removes the entry from the SYN queue and adds the full connection to the accept queue.
6. The backend calls `accept()`; the connection is removed from the accept queue.
7. A file descriptor is created for the connection in the process that called `accept()`, together with the connection's own two queues:
   - the **send queue** for outgoing data: a response written by the server waits here until the kernel pushes it to the NIC.
   - the **receive queue** for incoming data: a request from the client waits here until the process `read()`s it.
- The client has the same pair of queues on its side, and no SYN or accept queue: it has no listening socket.
- Steps 2–5 are entirely the kernel's work. The application never sees a SYN; it only sees `accept()` return.
- These queues live in kernel memory. The process cannot touch them directly, only through system calls, which switch into kernel mode (`io_uring` relaxes this with queues shared between user space and the kernel).

```anim
socket-accept-queues
```

### Problems with accepting connections
- **The backend does not accept fast enough**: a busy single-threaded server, a stop-the-world GC pause, a thread pool that is full. Completed connections wait in the accept queue until it is full, and then the kernel drops new handshakes. Clients see connection timeouts, not refusals, which makes this hard to diagnose. `ss -ltn` shows it: for a listening socket `Recv-Q` is the current accept-queue length and `Send-Q` the backlog; `nstat -az TcpExtListenOverflows TcpExtListenDrops` counts the drops.
- **Clients who never send the ACK**: each half-open handshake holds a SYN queue slot until it times out (SYN-ACK retries, `tcp_synack_retries`). A **SYN flood** sends SYNs from spoofed addresses to fill the queue; a single machine can take a server down this way. Linux answers with **SYN cookies** (`net.ipv4.tcp_syncookies = 1`): when the SYN queue is full, the kernel encodes the connection's parameters in the sequence number of its SYN-ACK and stores nothing, rebuilding the entry when a valid ACK returns. Large-scale filtering (Cloudflare and others) happens before the server.
- **A small backlog** overflows under bursts even when the application is fast on average. Raise `somaxconn` and the framework's backlog together.
- `accept()` on an empty accept queue **blocks** the calling thread, which is why servers use non-blocking sockets with `epoll` (below).

### Scaling accept: shared socket vs socket sharding
- **Shared listening socket**: the parent listens, then forks; every child inherits an fd pointing to the **same** socket, with one SYN queue and one accept queue. All processes call `accept()` on it and each connection goes to one of them. Multi-threaded servers do the same with threads. With many waiters the kernel may wake all of them for one connection (thundering herd); `EPOLLEXCLUSIVE` helps.
- **Socket sharding with `SO_REUSEPORT`**: normally, listening on an IP and port that is already in use fails with `EADDRINUSE`. With `SO_REUSEPORT` set on each socket, several processes or threads each get their **own** socket on the same IP and port, each with its own accept queue. The kernel hashes each new connection's 4-tuple to pick a socket, so it does the load balancing, and the acceptors never contend for one queue. NGINX (`listen 8080 reuseport`), Envoy and HAProxy use it.
- Security: otherwise any process could join port 80 and steal traffic. Linux only lets a socket join the group if it has the same effective user ID as the first one. (The course describes this as needing to know a "key" set by the first process; the actual check is the user ID.)
- Balance is only per new connection and only over the sockets alive at that moment. When a listener closes, the connections waiting in its accept queue are reset and the hash changes; Linux 5.14 added `net.ipv4.tcp_migrate_req` to move them to another listener, and eBPF programs can steer connections explicitly.

```anim
socket-sharding
```

### Send and receive buffers
- **Receive**: the client sends data on a connection; the kernel puts it into the connection's **receive queue** (receive buffer), **ACKs** it (possibly delayed, up to ~40 ms, so the ACK can ride on the response) and updates the **window** it advertises: the free space left in the buffer. The app calls `read()` to copy the data from the kernel into its own memory. Unless you use zero-copy techniques (`sendfile`, `MSG_ZEROCOPY`, `io_uring` registered buffers), every byte is copied once between kernel and user space.
- **Send**: `send()`/`write()` copies the app's bytes into the **send buffer** and returns; returning means "the kernel has it", not "the peer got it". The kernel cuts the data into segments of at most one MSS, sends as much as the peer's window allows, and keeps each segment until it is ACKed, in case it has to be retransmitted. Nagle's algorithm may hold back small writes until earlier data is ACKed; `TCP_NODELAY` turns it off for latency-sensitive protocols.
- Buffer sizes are auto-tuned per connection between the limits in `net.ipv4.tcp_rmem` / `tcp_wmem`; `SO_RCVBUF` / `SO_SNDBUF` fix them by hand (and turn autotuning off). On an established connection, `ss -tn` shows the bytes waiting: `Recv-Q` not yet read by the app, `Send-Q` not yet ACKed by the peer.

### Zero-copy: sendfile
- Serving a static file with `read()` and `write()` moves the bytes disk → page cache (DMA) → app buffer (copy) → socket send buffer (copy) → NIC (DMA): two system calls and two CPU copies of data the app never changes.
- `sendfile(out_fd, in_fd, offset, count)` asks the kernel to move file data to a socket directly: one system call, nothing copied into user space, and with a scatter-gather NIC no CPU copy at all. Bun advertised this for static files; Node.js and nginx (`sendfile on;`) use it too. In Java it is `FileChannel.transferTo()`, which Kafka uses to send log segments to consumers straight from the page cache.
- It only works when the bytes go out unchanged. HTTPS must encrypt them with a key held by the TLS library in the app, and gzip compression happens in the app, so the data has to come up into user space after all. **Kernel TLS** (kTLS, Linux 4.13+, used by nginx and OpenSSL 3) moves the symmetric encryption into the kernel so `sendfile` works with HTTPS again; compression still breaks it. Kafka with TLS between brokers and clients loses its zero-copy path for the same reason.
- Every byte a server receives is copied at least twice before the app sees it: NIC to kernel memory (DMA), then kernel to user space on `read()`. Copies are not free: each passes through the CPU caches and memory bus.

```anim
sendfile-zero-copy
```

### Nagle's algorithm and delayed ACK
- **Nagle's algorithm** holds back a small write while earlier data is still unacknowledged, to avoid sending many tiny packets, each carrying 40 bytes of IP and TCP headers. **Delayed ACK** on the other side holds back the acknowledgement for up to ~40 ms, hoping to send it with a response. Together they can stall a request–response exchange: the sender waits for an ACK, the receiver waits to send one.
- Daniel Stenberg traced unexplained delays in curl to exactly this during the TLS handshake, where small messages sat in the send buffer waiting; curl has set `TCP_NODELAY` (Nagle off) by default since 2016. Most latency-sensitive software does the same: Netty, gRPC, many database drivers. In Java: `socket.setTcpNoDelay(true)`, or `ChannelOption.TCP_NODELAY` in Netty.
- The trade-off: Nagle saves bandwidth on chatty protocols, `TCP_NODELAY` saves latency. With `TCP_NODELAY`, batch your own writes (one `write()` per response, not per header line).

### Problems with reading and sending
- **The backend does not read fast enough**: the receive queue fills, the advertised window shrinks to zero, and the client has to stop: its send buffer fills and its `write()` blocks. The client slows down. This is TCP flow control: backpressure all the way to the sender ([[backpressure-and-flow-control]]).
- The same in reverse: a slow client that does not read its responses fills the server's send buffer, and the server's `write()` blocks (or returns `EAGAIN` on a non-blocking socket). A thread-per-connection server can lose all its threads to a handful of slow clients; this is how "slowloris"-style attacks work. Write timeouts and non-blocking IO protect against it.

```anim
socket-buffers
```

### Blocking operations
- `read`, `write` and `accept` are **blocking** by default: the calling thread sleeps inside the system call, and the process cannot move its program counter until the call returns.
  - `read` blocks when there is no data in the receive buffer.
  - `accept` blocks when there are no connections in the accept queue.
  - `write` blocks when the send buffer is full.
- A single thread looping over ten connections and calling `read()` on each gets stuck on the first empty one, although the others already have data. One thread per connection avoids that, but each blocked thread costs a kernel task, a stack (1 MiB reserved for a platform thread), scheduler work and a context switch per wakeup ([[process-management]]): 10,000 idle keep-alive connections = 10,000 sleeping threads. This was called the **C10K problem**.
- With multiplexing, idle connections cost only an fd and a kernel socket buffer, and the thread count follows the CPU count, not the connection count. The price is programming style: per-connection state must be explicit (a state machine or a reactive pipeline), because no thread holds it on its stack. Virtual threads and Go goroutines hide this: the runtime parks the lightweight thread on a blocking call and uses epoll underneath, so you write blocking code and get multiplexed IO ([[virtual-threads]]).

### Asynchronous IO: readiness vs completion
- **Readiness approach**: ask the OS to tell you when a file descriptor is ready (data to read, space to write, a connection to accept), then call `read`/`write`/`accept` on it without blocking: `select`, `poll`, `epoll` (Linux), `kqueue` (BSD/macOS).
- **Completion approach**: ask the OS (or a worker thread doing the blocking IO) to perform the whole operation and notify you when it is done; the data is already in your buffer: IOCP (Windows), `io_uring` (Linux).
- Readiness does not work with storage files: a regular file is always "ready", and the `read()` can still stall on the disk ([[storage-management]]). File IO needs a thread pool, `O_DIRECT` + `libaio` (databases), or `io_uring`.

| Model (Stevens) | Waiting for data | Copying data to user space | Threads per connection | System calls |
|---|---|---|---|---|
| Blocking IO | thread sleeps | thread blocks | 1 | `read` |
| Non-blocking IO | app polls (`EAGAIN`) | thread blocks briefly | fewer, but the CPU spins | `read` with `O_NONBLOCK` |
| IO multiplexing | thread sleeps in `select`/`epoll_wait` on many fds | thread does the `read` | few | `epoll_wait` + `read` |
| Signal-driven | `SIGIO` when ready | thread does the `read` | few | rarely used |
| Asynchronous IO | kernel does both and signals completion | none in the app thread | few | `io_uring`, POSIX AIO, IOCP |
- Only the last one is truly asynchronous: in the first four the application thread does the copy. "Async" in Java libraries (Netty, WebFlux, [[spring-reactive-webclient-sse]]) usually means multiplexing with callbacks, not kernel AIO.

### select (polling)
- `select()` takes a collection of file descriptors (bitmaps `fd_set` for read, write and exceptions) for the kernel to monitor. It blocks, with a timeout, until any of them is ready, then returns. The process checks which ones are ready by looping over all of them with `FD_ISSET`, and calls `read`/`write`/`accept` on those.
- Pros: never blocks on an unready fd; one thread serves many connections.
- Cons: slow, O(n) per call even if one fd is ready: the whole set is copied from user space to the kernel and back, the kernel checks every fd, and the app scans every fd again. The set has a fixed size: fds numbered 1024 (`FD_SETSIZE`) and above cannot be watched at all. `poll()` removes the size limit (an array of `pollfd`), but keeps the O(n) copying and scanning.

### epoll (eventing)
- Register an **interest list** of fds in the kernel **once**: `epoll_create1()` returns an fd for a new epoll instance, `epoll_ctl(ADD)` adds connections to it.
- The kernel keeps working while the process does other things: when packets arrive and a connection becomes readable, the kernel itself puts it on the instance's **ready list**.
- The process calls `epoll_wait()`; the kernel builds an events array from the ready list and returns only the ready fds. The cost follows the number of ready connections, not the number watched: this is how NGINX, Node.js (libuv), Redis, Netty and Java NIO serve 100,000 connections with a few threads ([[java-nio]]).

| | `select` | `poll` | `epoll` |
|---|---|---|---|
| Interest set | fixed bitmap, `FD_SETSIZE` = 1024 | array of `pollfd`, any size | kernel object; `epoll_ctl` adds and removes fds once |
| Cost per call | O(max fd): copy the bitmap in and out, scan | O(n): copy the array in and out, scan | O(ready): the kernel keeps a ready list |
| Result | you scan all fds for readiness | you scan all entries | only the ready fds |
| Triggering | level | level | level (default) or edge (`EPOLLET`) |
| Portability | everywhere | POSIX | Linux only (`kqueue` on BSD/macOS, IOCP on Windows) |
- epoll drawbacks:
  - **Complex**: level-triggered vs edge-triggered. Level-triggered (default) keeps reporting an fd while unread data remains: simple and safe, what Java NIO uses. Edge-triggered reports once per change, so you must drain the fd until `EAGAIN` or lose the wakeup; it needs fewer system calls (NGINX and Netty's native transport use it) and is easier to get wrong.
  - **Linux only**: portable libraries wrap it (libuv, Netty, Java NIO pick epoll, kqueue or IOCP per OS).
  - **Too many system calls**: one `epoll_ctl` per change, one `epoll_wait`, then one `read` or `write` per ready fd.
  - **Doesn't work on files**: regular files are always ready.
- Thundering herd: many threads waiting on the same listening socket all wake for one connection; `EPOLLEXCLUSIVE` or `SO_REUSEPORT` (one listening socket per thread) fix it.

### The reactor loop
```c
// C-like pseudocode of what Netty's EventLoop and Java's Selector do
int ep = epoll_create1(0);
epoll_ctl(ep, EPOLL_CTL_ADD, listen_fd, &(struct epoll_event){.events = EPOLLIN, .data.fd = listen_fd});
for (;;) {
    int n = epoll_wait(ep, events, MAX, -1);          // sleeps until something is ready
    for (int i = 0; i < n; i++) {
        int fd = events[i].data.fd;
        if (fd == listen_fd) {                          // new connection in the accept queue
            int c = accept4(listen_fd, NULL, NULL, SOCK_NONBLOCK);
            epoll_ctl(ep, EPOLL_CTL_ADD, c, &(struct epoll_event){.events = EPOLLIN, .data.fd = c});
        } else {
            ssize_t r = read(fd, buf, sizeof buf);      // non-blocking: returns what is there
            if (r == 0) { close(fd); continue; }        // peer closed
            if (r < 0 && errno == EAGAIN) continue;     // spurious, nothing yet
            handle(fd, buf, r);                         // MUST NOT block, or every connection stalls
        }
    }
}
```
The invariant in the last comment is the whole discipline of reactive programming: a blocking JDBC call or `Thread.sleep` inside the loop freezes all connections on that event loop. This is why [[spring-reactive-webclient-sse|WebFlux]] needs R2DBC and why Netty warns about blocking handlers.

- **libuv** (Node.js) picks the native mechanism per OS: epoll on Linux, kqueue on macOS and BSD, event ports on Solaris, IOCP (a completion API) on Windows. What cannot be made non-blocking that way runs on its thread pool (4 threads by default, `UV_THREADPOOL_SIZE`): file system calls, DNS lookups with `getaddrinfo`, and CPU-heavy crypto and zlib work. That is what people mean by "Node is single-threaded but uses threads".

### io_uring
- Based on **completion**: the kernel does the work. The process and the kernel share memory holding two ring buffers: the process puts "jobs" (submission queue entries: read this socket into this buffer, write this file at this offset, accept, send…) into the **submission queue**; the kernel does the work and writes the results into the **completion queue**.
- Many operations per system call (`io_uring_enter`), or none at all with kernel-side polling (`SQPOLL`); it covers sockets **and** files, so it fixes epoll's blind spot for disk IO. Available since Linux 5.1; Netty, Postgres 18 and newer databases have `io_uring` backends.
- The shared rings and the large new kernel surface have produced many security bugs, so some environments turn it off. In 2023 Google reported that 60% of the kernel exploits submitted to its bug bounty in 2022 targeted io_uring (about $1 million in rewards); it disables it on production servers and in ChromeOS and Android apps, and Docker's default seccomp profile blocks it.

```anim
io-multiplexing
```

### A web server in C, line by line (the demo)
```c
int server_fd = socket(AF_INET, SOCK_STREAM, 0);       // IPv4, stream (TCP), default protocol
struct sockaddr_in addr = { .sin_family = AF_INET,
                            .sin_addr.s_addr = INADDR_ANY,   // 0.0.0.0: all interfaces (demo only)
                            .sin_port = htons(8080) };        // port in network byte order
bind(server_fd, (struct sockaddr *)&addr, sizeof addr); // name the socket; fails if the port is taken
listen(server_fd, 10);                                  // now the kernel builds the queues; backlog 10
for (;;) {
    int client_fd = accept(server_fd, NULL, NULL);     // blocks until a connection is in the accept queue
    char buf[1024] = {0};
    read(client_fd, buf, sizeof buf - 1);              // blocks until request bytes arrive; may be partial
    const char *resp = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                       "Content-Length: 13\r\n\r\nHello, world!";
    write(client_fd, resp, strlen(resp));             // copies into the send buffer and returns
    close(client_fd);                                 // one request per connection: no keep-alive
}
```
- Every framework runs this loop for you: Tomcat's acceptor thread, Node's `server.listen`, Netty's boss group. Seeing it in C shows that "an HTTP request" is just bytes in the receive buffer; HTTP is parsed in user space.
- `htons()` converts the port to **network byte order** (big-endian); x86 and ARM are little-endian, so forgetting it binds port 36895 instead of 8080. `INADDR_ANY` is all zeros, which is why it happens to work without conversion.
- An `AF_INET` socket accepts only IPv4: in the demo `curl http://[::1]:8080` failed while `127.0.0.1` worked. Listening on `::` with `IPV6_V6ONLY` off serves both.
- `read()` returns whatever has arrived, up to the buffer size: a large or slow request needs a loop until the end of the headers and `Content-Length` bytes of body. If the response's `Content-Length` is larger than the bytes actually sent, the client keeps waiting for the rest; the lecture uses that to simulate slow backends.
- Everything here blocks: one slow client stalls every other one. Real servers use threads, epoll or both.

### What the kernel does per packet
- The kernel manages the whole TCP/IP part of networking. For every packet received from the NIC it allocates a **socket buffer** (`sk_buff`) with metadata, and merges packets (GRO) to reduce the overhead. On a busy machine this is real CPU work, and network-heavy servers often reserve a core or two for interrupts and the network stack (IRQ affinity, RSS queues).

## Example
```bash
# Who listens where? -l listening, -t TCP, -n numeric, -p process
ss -ltnp
#   State   Recv-Q Send-Q  Local Address:Port  Peer Address:Port  Process
#   LISTEN  0      100     127.0.0.1:8080      0.0.0.0:*          users:(("java",pid=4123,fd=42))
#   LISTEN  0      4096    0.0.0.0:5432        0.0.0.0:*          users:(("postgres",pid=900,fd=6))   <- all interfaces!
# For LISTEN sockets: Recv-Q = connections waiting in the accept queue, Send-Q = backlog

# Accept-queue overflows since boot (should stay 0)
nstat -az TcpExtListenOverflows TcpExtListenDrops TcpExtSyncookiesSent

# Kernel limits
sysctl net.core.somaxconn net.ipv4.tcp_max_syn_backlog net.ipv4.tcp_syncookies

# Spring Boot: listen only where you mean to, with a backlog that fits bursts
#   server.address=10.0.0.1
#   server.tomcat.accept-count=1024     # the listen() backlog
#   server.tomcat.max-connections=8192  # connections Tomcat keeps open at once
```
A listening socket whose `Recv-Q` sits at its `Send-Q` while `ListenOverflows` grows is a server that cannot accept fast enough: clients are timing out in the handshake.

```bash
# Which IO model does a running server use?
strace -f -e trace=epoll_wait,epoll_ctl,accept4,read,futex -p $(pgrep -f esim-provisioning.jar) 2>&1 | head -20
#   [pid 4121] epoll_wait(89, [{EPOLLIN, {u32=104}}], 1024, -1) = 1    <- Tomcat NIO poller thread
#   [pid 4121] read(104, ..., 8192) = 517                                <- request bytes
#   [pid 4137] futex(0x7f..., FUTEX_WAIT_PRIVATE, ...)                   <- worker thread parked, waiting for work
#   [pid 4140] read(97, ..., 8192) = -1 EAGAIN                           <- Kafka client, non-blocking socket

# Bytes waiting in the buffers of established connections
ss -tn state established '( sport = :8080 )'
#   Recv-Q 0       Send-Q 2097152  ...   <- a client is not reading its response: our send buffer is full
```
Tomcat is a hybrid: one or two poller threads run `epoll_wait` and hand ready connections to a bounded worker pool that then does blocking JDBC. Netty/WebFlux keep everything on the event loops. Virtual threads make the worker pool unbounded and cheap while the JDK's scheduler multiplexes them over epoll.

## Pitfalls
- Leaving Nagle on for a request–response protocol with small messages: 40 ms stalls from Nagle meeting delayed ACK. Set `TCP_NODELAY`, and batch writes yourself.
- Expecting `sendfile`/`transferTo` to stay zero-copy behind TLS or compression: the bytes go through user space unless kernel TLS is on.
- Binding databases, admin panels and debug ports to `0.0.0.0` on a host with a public interface. Bind to `127.0.0.1` or a private IP and keep a firewall in front anyway.
- Debugging "random connection timeouts" by looking only at the application: the drops happen in the kernel's accept queue before any log line is written. Check `ss -ltn` and `ListenOverflows`.
- Raising the framework backlog but not `net.core.somaxconn` (or the reverse): the kernel silently uses the smaller value.
- Blocking work on the accept thread or the event loop (NGINX worker, Netty boss group, Node.js): connections queue in the kernel while the thread is busy.
- `fork` after opening sockets without meaning to share them: the child keeps the parent's listening socket and connections open. Mark fds close-on-exec (`SOCK_CLOEXEC`) when spawning helpers.
- Restarting `SO_REUSEPORT` workers all at once: connections waiting in the closed sockets' accept queues are reset. Restart them one by one, or use the runtime's graceful reload.
- Running out of file descriptors (`EMFILE: Too many open files`) under load: every accepted connection is an fd; raise `ulimit -n` / `LimitNOFILE` and watch for connection leaks in `CLOSE-WAIT`.
- Blocking inside an event loop (JDBC, `RestTemplate`, file IO, a contended `synchronized` block) stalls every connection on that loop; the symptom is latency spikes with low CPU.
- Still thinking in `select` limits: `select` breaks at fd 1024 even if only a few fds are open; modern code should not use it.
- Edge-triggered epoll without draining to `EAGAIN`: connections hang silently with unread data.
- Confusing "non-blocking" with "fast": a non-blocking `read` that returns `EAGAIN` in a tight loop burns a core. Always pair it with a readiness notification.
- Expecting epoll to help with disk-bound work: file fds are always ready; use a thread pool or `io_uring`.
- Treating a returned `write()`/`send()` as delivered: it only means the bytes are in the kernel's send buffer. Delivery is confirmed by the application protocol's response, not by TCP.
- Related: [[tcp-handshake-and-connection-states]] for the TCP protocol itself, [[sockets-and-connection-pooling]] for the client side, [[syscalls-and-file-descriptors]] for the fd table that epoll watches, [[storage-management]] for file IO, [[load-balancers-l4-vs-l7]] for balancing across machines.
