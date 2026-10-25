---
title: Java NIO: channels, buffers, selectors and NIO.2
tags: [java-nio, channels, buffers, selector, non-blocking-io]
learned: 2026-10-22
---
## TL;DR
NIO ("New I/O", Java 1.4) replaced the stream-per-connection model with **channels** that read and write **buffers**, and added **selectors** so one thread can watch many channels for readiness: non-blocking, event-driven I/O. NIO.2 (Java 7) added the `Path`/`Files` API, asynchronous channels and `WatchService`. Netty, Tomcat's NIO connector and Spring WebFlux are built on it; you rarely write raw selectors yourself.

## Key points
### Streams vs channels
| `java.io` streams | NIO channels |
|---|---|
| One direction per stream | A channel is usually bidirectional (`SocketChannel` reads and writes) |
| Blocking: thread waits per call | Blocking or non-blocking (`configureBlocking(false)`) |
| Data goes byte by byte or via arrays | Data goes through `ByteBuffer`s; direct buffers avoid a copy into the Java heap |
| One thread per connection | One selector thread multiplexes thousands of connections |

- The "N" officially means "New", but "non-blocking" is the property everyone cares about. Non-blocking means a `read` returns immediately with whatever is available (possibly 0 bytes); the thread is never parked inside the kernel.

### Buffers
- `ByteBuffer` (plus `CharBuffer`, `IntBuffer`, ...): a fixed-size array with three indices: **position** (next element), **limit** (first element not to touch), **capacity**. Workflow: write into it (`put`/`channel.read(buf)`), `flip()` to switch to reading (limit = position, position = 0), read (`get`/`channel.write(buf)`), then `clear()` or `compact()` to write again.
- `allocate(n)` is a heap buffer (backed by `byte[]`); `allocateDirect(n)` is off-heap memory the OS can DMA into directly, more expensive to allocate and freed by a `Cleaner` ([[reference-types]]), so pool and reuse direct buffers. Netty's `PooledByteBufAllocator` exists for this.
- `MappedByteBuffer` from `FileChannel.map()` maps a file into memory: random access to huge files without read calls (Kafka's log segments, Lucene).

### Channels
- `FileChannel` (positional reads, `transferTo`/`transferFrom` for zero-copy file-to-socket, locks), `SocketChannel`/`ServerSocketChannel` (TCP), `DatagramChannel` (UDP), `Pipe`. `FileChannel` cannot be non-blocking; files are always "ready".
- `AsynchronousFileChannel` and `AsynchronousSocketChannel` (NIO.2) start an operation and deliver the result through a `Future` or a `CompletionHandler` callback, on a thread pool behind the scenes.

### Selector: the reactor pattern
1. Register channels with a `Selector` for interest ops: `OP_ACCEPT`, `OP_CONNECT`, `OP_READ`, `OP_WRITE`; each registration is a `SelectionKey` with an optional attachment (your per-connection state).
2. `selector.select()` blocks until at least one channel is ready (epoll on Linux, kqueue on macOS).
3. Iterate `selectedKeys()`, handle each, **remove the key from the set**.
- One event loop thread handles I/O; CPU-heavy work is handed to a worker pool ([[executors-and-thread-pools]]). This is how Netty, Vert.x and Reactor Netty (WebFlux, [[spring-reactive-webclient-sse]]) scale to many connections with few threads.
- Compare with virtual threads ([[virtual-threads]], Java 21): the same scalability with blocking-style code; the JDK parks the virtual thread on the same epoll machinery underneath.

### NIO.2: Path, Files, WatchService
- `Path` is an immutable, filesystem-aware path; `Files` has static helpers for reading, writing, copying, walking, attributes and atomic moves (`StandardCopyOption.ATOMIC_MOVE`). Prefer them to `java.io.File` ([[java-io-streams]]).
- `WatchService` subscribes to directory changes (create/modify/delete) using inotify; polling-based on macOS.
- `FileSystems.newFileSystem(zipPath)` treats a zip/jar as a file system.

## Example
```java
// Minimal single-threaded echo server with a selector (the shape Netty hides from you).
public static void serve(int port) throws IOException {
    try (Selector selector = Selector.open();
         ServerSocketChannel server = ServerSocketChannel.open()) {
        server.bind(new InetSocketAddress(port));
        server.configureBlocking(false);
        server.register(selector, SelectionKey.OP_ACCEPT);

        while (true) {
            selector.select();                                   // blocks until something is ready
            Iterator<SelectionKey> it = selector.selectedKeys().iterator();
            while (it.hasNext()) {
                SelectionKey key = it.next();
                it.remove();                                     // mandatory
                if (key.isAcceptable()) {
                    SocketChannel client = server.accept();
                    client.configureBlocking(false);
                    client.register(selector, SelectionKey.OP_READ, ByteBuffer.allocateDirect(4096));
                } else if (key.isReadable()) {
                    SocketChannel client = (SocketChannel) key.channel();
                    ByteBuffer buf = (ByteBuffer) key.attachment();
                    int n = client.read(buf);                     // returns immediately
                    if (n == -1) { client.close(); continue; }
                    buf.flip();
                    while (buf.hasRemaining()) client.write(buf); // echo back
                    buf.clear();
                }
            }
        }
    }
}

// Day-to-day NIO.2: atomic replace of a config file
Path tmp = Files.createTempFile(dir, "tariffs", ".json");
Files.writeString(tmp, json);
Files.move(tmp, dir.resolve("tariffs.json"), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
```

## Pitfalls
- Forgetting `flip()` before writing a buffer out sends nothing (position == limit); forgetting `clear()`/`compact()` after makes the next read fail with 0 bytes. Most NIO bugs are buffer-index bugs.
- Not removing keys from `selectedKeys()` makes the loop process stale events forever; registering `OP_WRITE` permanently spins the CPU because sockets are almost always writable.
- Direct buffers are outside the heap: `-Xmx` does not limit them (`-XX:MaxDirectMemorySize` does), and leaking them shows up as native memory growth, not as heap OOM ([[jvm-memory-areas]]).
- Blocking code (JDBC, a sleeping call) on the event-loop thread stalls every connection; this is the cardinal sin of WebFlux/Netty applications.
- Related: [[java-io-streams]], [[virtual-threads]], [[spring-reactive-webclient-sse]].
