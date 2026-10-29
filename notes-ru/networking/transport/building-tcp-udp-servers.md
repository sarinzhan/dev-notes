---
title: Пишем TCP- и UDP-серверы
tags: [networking, sockets, tcp, udp, java]
learned: 2026-10-29
---
## Кратко
TCP-сервер — это `socket → bind → listen → accept → read/write → close`: ядро выполняет рукопожатия и ставит готовые соединения в очередь, а каждый `accept()` возвращает новый файловый дескриптор для одного клиента. UDP-сервер — это `socket → bind → recvfrom/sendto`: один сокет, никаких соединений, каждая датаграмма приходит вместе с адресом отправителя. Если один раз написать оба на C и на чистой Java, видно, что на самом деле делают за тебя Tomcat, Netty и Node: конкурентность, кадрирование, таймауты, TLS и обратное давление (backpressure) поверх той же горстки системных вызовов.

## Основное
### Системные вызовы бок о бок
| Шаг | TCP | UDP |
|---|---|---|
| создать | `socket(AF_INET, SOCK_STREAM, 0)` | `socket(AF_INET, SOCK_DGRAM, 0)` |
| назначить адрес | `bind(ip, port)` | `bind(ip, port)` |
| принимать клиентов | `listen(fd, backlog)`, затем `accept()` на каждое соединение | ничего: соединений нет |
| получить | `read(conn_fd)`: любое число байт из потока | `recvfrom(fd)`: одна целая датаграмма + адрес отправителя |
| ответить | `write(conn_fd)` | `sendto(fd, peer)` |
| завершить | `close(conn_fd)` отправляет FIN | закрывать на клиента нечего |

Слушающий сокет, очереди SYN и accept, backlog и буферы за этими вызовами разобраны в [[socket-management]]; механика системных вызовов — в [[syscalls-and-file-descriptors]].

### Минимальные серверы на C
TCP echo: по одному клиенту за раз, цикл до закрытия клиентом.
```c
int lfd = socket(AF_INET, SOCK_STREAM, 0);
int on = 1; setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof on); // повторный bind несмотря на TIME_WAIT
struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(7000), .sin_addr.s_addr = htonl(INADDR_ANY) };
bind(lfd, (struct sockaddr *)&a, sizeof a);
listen(lfd, 128);
for (;;) {
    int cfd = accept(lfd, NULL, NULL);            // блокируется, пока в очереди accept нет соединения
    char buf[4096]; ssize_t n;
    while ((n = read(cfd, buf, sizeof buf)) > 0)  // 0 = собеседник прислал FIN, -1 = ошибка/RST
        write(cfd, buf, n);
    close(cfd);
}
```
UDP echo: один сокет отвечает всем.
```c
int fd = socket(AF_INET, SOCK_DGRAM, 0);
bind(fd, (struct sockaddr *)&a, sizeof a);        // тот же порт 7000: пространства портов TCP и UDP раздельны
for (;;) {
    char buf[1500]; struct sockaddr_in peer; socklen_t len = sizeof peer;
    ssize_t n = recvfrom(fd, buf, sizeof buf, 0, (struct sockaddr *)&peer, &len);
    sendto(fd, buf, n, 0, (struct sockaddr *)&peer, len);
}
```
- `net.createServer()` и `dgram.createSocket("udp4")` в Node — те же вызовы, управляемые циклом epoll из libuv в одном потоке.
- Проверка: `nc localhost 7000` (TCP) и `nc -u localhost 7000` (UDP).

### Что ломается в наивной версии
- **По одному клиенту**: пока TCP-цикл обслуживает клиента A, завершённое рукопожатие клиента B ждёт в очереди accept. Решения: поток на соединение, пул потоков или неблокирующие сокеты с epoll ([[socket-management]]).
- **Частичные чтения**: `read()` возвращает то, что успело прийти. Протоколу нужно кадрирование (префикс длины, разделитель, `Content-Length`) и цикл чтения ([[tcp-vs-udp]]).
- **Нет таймаутов**: клиент, который подключился и замолчал, держит поток вечно ([[failure-modes-and-timeouts]]).
- **SIGPIPE**: запись в сокет, сброшенный собеседником, убивает процесс на C, если не передать `MSG_NOSIGNAL` или не игнорировать сигнал. JVM игнорирует SIGPIPE и вместо этого бросает `IOException: Broken pipe`.
- **Обрезание UDP**: буфер меньше датаграммы молча её обрезает ([[udp-in-depth]]).

### Что добавляют Tomcat и Netty
- **Tomcat (NIO-коннектор)**: поток-acceptor вызывает `accept()`, poller регистрирует сокеты в `Selector` (epoll в Linux), а пул рабочих потоков (`server.tomcat.threads.max`, по умолчанию 200) разбирает HTTP и запускает твой контроллер ([[spring-mvc-request-lifecycle]]). `accept-count` — это backlog для `listen()`, `max-connections` — предел открытых сокетов.
- **Netty**: boss event loop принимает соединения, рабочие event loop (по умолчанию 2 × ядра) владеют каждый своим экземпляром epoll; `ChannelPipeline` из обработчиков делает кадрирование (`LengthFieldBasedFrameDecoder`), TLS, кодеки и idle-таймауты, с пулом direct-буферов ([[java-nio]]). На этой модели работают клиенты Kafka, gRPC-Java и WebFlux ([[spring-reactive-webclient-sse]]).
- Оба обрабатывают keep-alive, корректное завершение (graceful shutdown), метрики и обратное давление: перестают читать сокет, когда приложение не успевает, и управление потоком TCP притормаживает клиента ([[tcp-flow-control]]).

## Пример
Оба echo-сервера на Java 21 в одном классе. Виртуальные потоки снова делают дешёвой блокирующую модель «поток на соединение» ([[virtual-threads]]).
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
                Socket client = server.accept();                       // один fd на соединение
                client.setSoTimeout(30_000);                           // таймаут чтения: отбросить молчащих клиентов
                pool.submit(() -> tcpEcho(client));
            }
        }
    }

    static void tcpEcho(Socket client) {
        try (client; var in = client.getInputStream(); var out = client.getOutputStream()) {
            in.transferTo(out);                                        // копировать до FIN (read возвращает -1)
        } catch (IOException e) {
            System.err.println("client " + client.getRemoteSocketAddress() + ": " + e.getMessage());
        }
    }

    static void udp() {
        try (var ch = DatagramChannel.open().bind(new InetSocketAddress(7000))) {
            var buf = ByteBuffer.allocate(1500);
            while (true) {
                buf.clear();
                SocketAddress peer = ch.receive(buf);                  // одна датаграмма + адрес отправителя
                buf.flip();
                ch.send(buf, peer);                                    // ответ уходит этому же отправителю
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
```

## Подводные камни
- Привязка к `127.0.0.1` внутри контейнера: сервер недоступен снаружи; привязывай к `0.0.0.0` ([[docker-networking]]).
- Забытый `htons()` в C привязывает не тот порт (8080 превращается в 36895 на little-endian процессорах).
- Поток на соединение с платформенными потоками: 10 000 простаивающих keep-alive-клиентов — это 10 000 потоков и их стеков. Используй виртуальные потоки или event loop.
- Свой протокол поверх TCP там, где хватило бы HTTP/gRPC: кадрирование, версионирование, таймауты и TLS придётся делать самому.
- Связанные заметки: [[tcp-segment-structure]] — как эти записи выглядят на проводе, [[sockets-and-connection-pooling]] — клиентская сторона.
