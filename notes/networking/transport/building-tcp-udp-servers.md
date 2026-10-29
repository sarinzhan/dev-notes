---
title: Building TCP and UDP servers
tags: [networking, sockets, tcp, udp, java]
learned: 2026-10-29
---
## TL;DR
A TCP server is `socket → bind → listen → accept → read/write → close`: the kernel runs handshakes and queues finished connections, and each `accept()` returns a new file descriptor for one client. A UDP server is `socket → bind → recvfrom/sendto`: one socket, no connections, every datagram arrives with the sender's address. Writing both once in C and in plain Java shows what Tomcat, Netty and Node actually do for you: concurrency, framing, timeouts, TLS and backpressure on top of the same handful of system calls.

## Key points
### System calls side by side
| Step | TCP | UDP |
|---|---|---|
| create | `socket(AF_INET, SOCK_STREAM, 0)` | `socket(AF_INET, SOCK_DGRAM, 0)` |
| name it | `bind(ip, port)` | `bind(ip, port)` |
| accept clients | `listen(fd, backlog)`, then `accept()` per connection | nothing: no connections |
| receive | `read(conn_fd)`: any number of bytes from the stream | `recvfrom(fd)`: one whole datagram + sender address |
| reply | `write(conn_fd)` | `sendto(fd, peer)` |
| finish | `close(conn_fd)` sends FIN | nothing to close per client |

The listening socket, SYN and accept queues, backlog and buffers behind these calls are covered in [[socket-management]]; the syscall mechanics in [[syscalls-and-file-descriptors]].

### Minimal servers in C
TCP echo: one client at a time, loops until the client closes.
```c
int lfd = socket(AF_INET, SOCK_STREAM, 0);
int on = 1; setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof on); // rebind despite TIME_WAIT
struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(7000), .sin_addr.s_addr = htonl(INADDR_ANY) };
bind(lfd, (struct sockaddr *)&a, sizeof a);
listen(lfd, 128);
for (;;) {
    int cfd = accept(lfd, NULL, NULL);            // blocks until the accept queue has a connection
    char buf[4096]; ssize_t n;
    while ((n = read(cfd, buf, sizeof buf)) > 0)  // 0 = peer sent FIN, -1 = error/RST
        write(cfd, buf, n);
    close(cfd);
}
```
UDP echo: one socket answers everyone.
```c
int fd = socket(AF_INET, SOCK_DGRAM, 0);
bind(fd, (struct sockaddr *)&a, sizeof a);        // same port 7000: TCP and UDP port spaces are separate
for (;;) {
    char buf[1500]; struct sockaddr_in peer; socklen_t len = sizeof peer;
    ssize_t n = recvfrom(fd, buf, sizeof buf, 0, (struct sockaddr *)&peer, &len);
    sendto(fd, buf, n, 0, (struct sockaddr *)&peer, len);
}
```
- Node's `net.createServer()` and `dgram.createSocket("udp4")` are the same calls driven by libuv's epoll loop on one thread.
- Test with `nc localhost 7000` (TCP) and `nc -u localhost 7000` (UDP).

### What breaks in the naive version
- **One client at a time**: while the TCP loop serves client A, client B's finished handshake waits in the accept queue. Fixes: thread per connection, a thread pool, or non-blocking sockets with epoll ([[socket-management]]).
- **Partial reads**: `read()` returns whatever has arrived. A protocol needs framing (length prefix, delimiter, `Content-Length`) and a read loop ([[tcp-vs-udp]]).
- **No timeouts**: a client that connects and goes silent holds a thread forever ([[failure-modes-and-timeouts]]).
- **SIGPIPE**: writing to a socket the peer has reset kills a C process unless you pass `MSG_NOSIGNAL` or ignore the signal. The JVM ignores SIGPIPE and throws `IOException: Broken pipe` instead.
- **UDP truncation**: a buffer smaller than the datagram silently cuts it ([[udp-in-depth]]).

### What Tomcat and Netty add on top
- **Tomcat (NIO connector)**: an acceptor thread calls `accept()`, a poller registers sockets with a `Selector` (epoll on Linux), and a worker pool (`server.tomcat.threads.max`, default 200) parses HTTP and runs your controller ([[spring-mvc-request-lifecycle]]). `accept-count` is the `listen()` backlog, `max-connections` the cap on open sockets.
- **Netty**: a boss event loop accepts, worker event loops (2 × cores by default) each own an epoll instance; a `ChannelPipeline` of handlers does framing (`LengthFieldBasedFrameDecoder`), TLS, codecs and idle timeouts, with pooled direct buffers ([[java-nio]]). Kafka clients, gRPC-Java and WebFlux run on this model ([[spring-reactive-webclient-sse]]).
- Both handle keep-alive, graceful shutdown, metrics and backpressure: stop reading a socket when the application cannot keep up, so TCP flow control pushes back on the client ([[tcp-flow-control]]).

## Example
Both echo servers in Java 21, one class. Virtual threads make the blocking thread-per-connection model cheap again ([[virtual-threads]]).
```java
import java.io.*;
import java.net.*;
import java.nio.ByteBuffer;
import java.nio.channels.DatagramChannel;
import java.util.concurrent.Executors;

public class EchoServers {
    public static void main(String[] args) throws IOException {
        Thread.ofPlatform().name("udp-echo").start(EchoServers::udp);
        try (var server = new ServerSocket(7000, 128);                 // socket + bind + listen(128)
             var pool = Executors.newVirtualThreadPerTaskExecutor()) {
            while (true) {
                Socket client = server.accept();                       // one fd per connection
                client.setSoTimeout(30_000);                           // read timeout: drop silent clients
                pool.submit(() -> tcpEcho(client));
            }
        }
    }

    static void tcpEcho(Socket client) {
        try (client; var in = client.getInputStream(); var out = client.getOutputStream()) {
            in.transferTo(out);                                        // copy until FIN (read returns -1)
        } catch (IOException e) {
            System.err.println("client " + client.getRemoteSocketAddress() + ": " + e.getMessage());
        }
    }

    static void udp() {
        try (var ch = DatagramChannel.open().bind(new InetSocketAddress(7000))) {
            var buf = ByteBuffer.allocate(1500);
            while (true) {
                buf.clear();
                SocketAddress peer = ch.receive(buf);                  // one datagram + sender address
                buf.flip();
                ch.send(buf, peer);                                    // reply goes back to that sender
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
```

## Pitfalls
- Binding to `127.0.0.1` inside a container: the server is unreachable from outside it; bind `0.0.0.0` ([[docker-networking]]).
- Forgetting `htons()` in C binds the wrong port (8080 becomes 36895 on little-endian CPUs).
- Thread-per-connection with platform threads: 10,000 idle keep-alive clients means 10,000 threads and their stacks. Use virtual threads or an event loop.
- Building your own TCP protocol when HTTP/gRPC would do: you inherit framing, versioning, timeouts and TLS yourself.
- Related: [[tcp-segment-structure]] for what these writes look like on the wire, [[sockets-and-connection-pooling]] for the client side.
