---
title: Sockets and connection pooling
tags: [networking, sockets, connection-pool, java]
learned: 2026-10-27
---
## TL;DR
A socket is a file descriptor the kernel ties to a 4-tuple and two buffers; creating one for TCP costs a handshake (plus TLS), slow start and an ephemeral port that is locked for 60 seconds after close. Connection pools exist to pay that cost once and reuse the socket for many requests, and to put a hard cap on how many connections a service opens. Every client library you use (HikariCP, Apache HttpClient, Lettuce, Kafka clients, JDK `HttpClient`) is a pool with slightly different knobs; the failure modes are the same: leaks, stale connections after idle, and pool exhaustion masquerading as slowness.

## Key points
### Socket lifecycle
| Step | Server side | Client side | Kernel state |
|---|---|---|---|
| create | `socket()` | `socket()` | fd allocated, no address |
| name | `bind(0.0.0.0:8080)`, `listen(backlog)` | implicit bind to ephemeral port | `LISTEN` / none |
| connect | `accept()` returns a new fd per client | `connect()` runs the handshake | `ESTABLISHED` |
| transfer | `read()`/`write()` on the socket buffers | same | send/receive buffers, window |
| end | `close()` or `shutdown()` | `close()` | FIN exchange, `TIME_WAIT` on the active closer |

- The listening socket and the per-connection sockets are different fds; a server holding 10k clients has 10k fds. `ulimit -n` (default 1024 on some images) is a real ceiling; Spring Boot in Kubernetes should run with 65536.
- Each socket has kernel send/receive buffers; a slow reader fills the sender's buffer and `write()` blocks (or returns `EAGAIN` in non-blocking mode). This is backpressure at the lowest level ([[backpressure-and-flow-control]], [[io-models-blocking-nonblocking-epoll]]).
- In Java: `java.net.Socket` (blocking, one thread per connection, fine with [[virtual-threads]]), `SocketChannel` + `Selector` ([[java-nio]], what Netty and Tomcat NIO use).

### Why pools exist
1. **Latency**: TCP handshake 1 RTT, TLS 1.3 another RTT, Postgres authentication a few more round trips; 5–50 ms per connection inside a data centre, 100+ ms across regions. A pooled connection is ready in microseconds.
2. **Ports**: unpooled clients burn an ephemeral port per request and hit `TIME_WAIT` exhaustion around a few hundred requests per second to one upstream ([[tcp-handshake-and-connection-states]]).
3. **Server-side cost**: a Postgres backend is a process with ~10 MB of memory; `max_connections=100` is a hard limit, and the pool is what protects the database from 20 pods each opening 50 connections ([[hikaricp-pool-sizing]]).
4. **Admission control**: a bounded pool turns an overload into a queue with a timeout instead of an unbounded fan-out that takes the upstream down.

### Pool parameters that matter everywhere
- **Max size**: total connections; for databases `cores * 2 + spindles` is the classic starting point, not "as many as threads".
- **Min idle / warm-up**: keep some open so the first request after a quiet period is not slow.
- **Max lifetime**: recycle connections before infrastructure kills them (load balancer idle timeout, DNS change, certificate rotation). HikariCP default 30 min; set it below any NAT/LB idle timeout.
- **Idle timeout / keep-alive**: detect half-open connections ([[tcp-handshake-and-connection-states]]).
- **Acquire timeout**: how long a caller waits for a free connection before failing fast. Without it, pool exhaustion becomes an invisible hang; with it you get a clear exception and a metric.
- **Validation**: a cheap check on borrow (`isValid`, JDBC4) or a background check; test-on-borrow is wasted RTT if keep-alive is in place.

### Pools you already run
| Client | Pool knob names | Notes |
|---|---|---|
| HikariCP (JDBC) | `maximumPoolSize`, `connectionTimeout`, `maxLifetime`, `keepaliveTime` | one physical TCP connection per pooled connection |
| JDK `HttpClient` (Java 11+) | internal pool, `jdk.httpclient.connectionPoolSize`, `keepalive.timeout` | HTTP/2 multiplexes many requests on one connection, so "pool size" means less |
| Apache HttpClient 5 / `RestClient` | `maxConnTotal`, `maxConnPerRoute`, `connectionRequestTimeout`, TTL | the default per-route of 5 is too low for real services ([[rest-clients]]) |
| Reactor Netty `WebClient` | `ConnectionProvider` max connections, `maxIdleTime`, `pendingAcquireTimeout` | ([[spring-reactive-webclient-sse]]) |
| Kafka producer/consumer | one connection per broker, no pool to tune | long-lived; `connections.max.idle.ms` ([[kafka-producer]]) |
| Lettuce (Redis) | single multiplexed connection by default | pooling only for blocking commands/transactions |

## Example
A Java 21 `HttpClient` configured as a shared, pooled bean with explicit timeouts, which is what most teams get wrong by creating a new client per call (each client owns its own pool and executor).
```java
@Configuration
public class SmdpHttpConfig {

    @Bean
    HttpClient smdpHttpClient() {
        return HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_2)          // falls back to 1.1 if the server lacks h2
                .connectTimeout(Duration.ofSeconds(3))        // handshake budget
                .executor(Executors.newVirtualThreadPerTaskExecutor())
                .build();                                     // ONE instance: owns the connection pool
    }

    @Bean
    SmdpClient smdpClient(HttpClient client) {
        return new SmdpClient(client, URI.create("https://smdp.example.com/gsma/rsp2/es2plus/"));
    }
}

public record SmdpClient(HttpClient client, URI base) {
    public String confirmOrder(String iccid) throws IOException, InterruptedException {
        var req = HttpRequest.newBuilder(base.resolve("confirmOrder"))
                .timeout(Duration.ofSeconds(10))               // per-request read budget
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{\"iccid\":\"" + iccid + "\"}"))
                .build();
        return client.send(req, HttpResponse.BodyHandlers.ofString()).body(); // body fully read: connection returns to pool
    }
}
```
Start the JVM with `-Djdk.httpclient.keepalive.timeout=60` when the upstream's idle timeout is shorter than the JDK default of 1200 s for HTTP/1.1, otherwise the first call after a pause gets a reset.

## Pitfalls
- Creating a client/pool per request (`new RestTemplate()`, `HttpClient.newHttpClient()` inside a method) defeats the purpose and leaks threads and sockets.
- Not consuming or closing the response body; the connection is never returned and the pool starves while `ss` shows `CLOSE-WAIT`.
- Pool size equal to thread count "to avoid waiting": 200 pods × 200 connections kills Postgres; size for the upstream, queue in the pool.
- Holding a pooled DB connection during a remote HTTP call inside `@Transactional` ([[spring-transactional]]): the slow upstream now exhausts the DB pool too.
- Stale DNS: a pool keeps connections to the old IP after a failover; cap `maxLifetime` and keep the JVM DNS cache TTL short ([[dns-resolution]]).
- Related: [[tcp-vs-udp]] for what a connection costs, [[circuit-breaker]] for what to do when the pool's acquire timeout starts firing.
