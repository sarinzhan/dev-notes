---
title: IO models: blocking, non-blocking, multiplexing and async
tags: [operating-systems, io, epoll, linux, networking]
learned: 2026-10-27
---
## TL;DR
There are four ways a program can wait for data on a socket. **Blocking**: the thread sleeps in `read` until bytes arrive (one thread per connection). **Non-blocking**: `read` returns `EAGAIN` immediately and you poll in a loop (wastes CPU alone). **Multiplexing**: one thread asks the kernel "tell me which of these N fds are ready" with `select`/`poll`/`epoll`, then does non-blocking reads on the ready ones; this is how Netty, nginx, Node and Java NIO handle 100 k connections on a few threads. **Asynchronous**: the kernel performs the IO itself and notifies on completion (Windows IOCP, Linux `io_uring`). Java's `Selector` API over epoll is in [[java-nio]]; [[virtual-threads]] give blocking-style code on top of multiplexing.

## Key points

### The five models (Stevens' classification)
| Model | Waiting for data | Copying data to user space | Threads per connection | Syscalls |
|---|---|---|---|---|
| Blocking IO | thread sleeps | thread blocks | 1 | `read` |
| Non-blocking IO | app polls (`EAGAIN`) | thread blocks briefly | fewer, but CPU spin | `read` with `O_NONBLOCK` |
| IO multiplexing | thread sleeps in `epoll_wait` on many fds | thread does the `read` | few | `epoll_wait` + `read` |
| Signal-driven | `SIGIO` when ready | thread does the `read` | few | rarely used |
| Asynchronous IO | kernel does both and signals completion | none in app thread | few | `io_uring`, POSIX AIO, IOCP |
- Only the last one is truly asynchronous: the first four all have the application thread perform the copy. "Async" in Java libraries (Netty, WebFlux, [[spring-reactive-webclient-sse]]) usually means multiplexing with callbacks, not kernel AIO.
- Readiness vs completion: epoll tells you an fd is **ready** (there are bytes to read or buffer space to write); `io_uring` tells you an operation **completed** and the data is already in your buffer.

### Why thread-per-connection stops scaling
- Each blocked thread costs a kernel task, a stack (1 MiB reserved), scheduler work and a context switch per wakeup ([[processes-and-threads]], [[cpu-scheduling]]). 10 k idle keep-alive connections = 10 k sleeping threads doing nothing but occupying memory. This is the C10K problem.
- With multiplexing, idle connections cost only an fd and a kernel socket buffer; the thread count follows the CPU count, not the connection count.
- The price is programming style: state per connection must be explicit (a state machine or a reactive pipeline) because there is no thread holding it on its stack. Virtual threads and Go goroutines hide this: the runtime parks the "thread" on a blocking call and uses epoll underneath, so you write blocking code and get multiplexed IO.

### select vs poll vs epoll
| | `select` | `poll` | `epoll` |
|---|---|---|---|
| Interest set | fixed bitmap, `FD_SETSIZE` = 1024 | array of `pollfd`, any size | kernel object; `epoll_ctl` adds/removes fds once |
| Per call cost | O(max fd): copy bitmap in and out, scan | O(n): copy array in and out, scan | O(ready): kernel keeps a ready list |
| Result | you scan all fds for readiness | you scan all entries | returns only ready fds |
| Triggering | level | level | level (default) or edge (`EPOLLET`) |
| Portability | everywhere | POSIX | Linux only (`kqueue` on BSD/macOS, IOCP on Windows) |
- **Level-triggered**: `epoll_wait` keeps reporting the fd while data remains unread. Simple, safe, what Java NIO uses.
- **Edge-triggered**: reported once when state changes; you must drain the fd until `EAGAIN` or you lose the wakeup. Fewer syscalls (nginx, Netty's native `epoll` transport use it), easier to get wrong.
- Thundering herd: many threads waiting on the same listening socket all wake on one connection; `EPOLLEXCLUSIVE` or `SO_REUSEPORT` (one listening socket per thread) fix it.

### The reactor loop
```c
// C-like pseudocode of what Netty's EventLoop and Java's Selector do
int ep = epoll_create1(0);
epoll_ctl(ep, EPOLL_CTL_ADD, listen_fd, &(struct epoll_event){.events = EPOLLIN, .data.fd = listen_fd});
for (;;) {
    int n = epoll_wait(ep, events, MAX, -1);          // sleeps until something is ready
    for (int i = 0; i < n; i++) {
        int fd = events[i].data.fd;
        if (fd == listen_fd) {                          // new connection
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

### Disk IO is different
- Regular files are always "ready" for epoll; the read may still stall on disk. Options: blocking reads on a thread pool (Netty, Node, Tomcat), `O_DIRECT` + `libaio` (databases), or `io_uring` (Linux 5.1+): true async submission/completion queues that also cover sockets; Postgres 18 and Netty have `io_uring` backends.

## Example
```bash
# See the model a running server uses
strace -f -e trace=epoll_wait,epoll_ctl,accept4,read,futex -p $(pgrep -f esim-provisioning.jar) 2>&1 | head -20
#   [pid 4121] epoll_wait(89, [{EPOLLIN, {u32=104}}], 1024, -1) = 1    <- Tomcat NIO poller thread
#   [pid 4121] read(104, ..., 8192) = 517                                <- request bytes
#   [pid 4137] futex(0x7f..., FUTEX_WAIT_PRIVATE, ...)                   <- worker thread parked, waiting for work
#   [pid 4140] read(97, ..., 8192) = -1 EAGAIN                           <- Kafka client, non-blocking socket
```
Tomcat is a hybrid: one or two poller threads run `epoll_wait`, hand ready connections to a bounded worker pool that then does blocking JDBC. Netty/WebFlux keep everything on the event loops. Virtual threads make the worker pool unbounded and cheap while the JDK's scheduler multiplexes them over epoll.

## Pitfalls
- Blocking inside an event loop (JDBC, `RestTemplate`, file IO, a `synchronized` block with contention) stalls every connection on that loop; symptoms are latency spikes with low CPU.
- Setting `FD_SETSIZE`-era limits in your head: `select` dies at fd 1024 even if only a few are open; modern code should never use it.
- Edge-triggered epoll without draining to `EAGAIN`: connections silently hang with unread data.
- Confusing "non-blocking" with "fast": a non-blocking `read` that returns `EAGAIN` in a tight loop burns a core. Always pair it with a readiness notification.
- Expecting epoll to help with disk-bound workloads: file fds are always ready; use a thread pool or `io_uring`.
- Related: [[syscalls-and-file-descriptors]] for the fd table that epoll watches, [[sockets-and-connection-pooling]] and [[tcp-handshake-and-connection-states]] for what "ready" means on a socket.
