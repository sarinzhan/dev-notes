---
title: Java NIO: каналы, буферы, селекторы и NIO.2
tags: [java-nio, channels, buffers, selector, non-blocking-io]
learned: 2026-10-22
---
## Кратко
NIO («New I/O», Java 1.4) заменил модель «поток на соединение» **каналами** (channels), которые читают и пишут **буферы** (buffers), и добавил **селекторы** (selectors), чтобы один поток мог следить за готовностью многих каналов: неблокирующий, событийно-ориентированный ввод-вывод. NIO.2 (Java 7) добавил API `Path`/`Files`, асинхронные каналы и `WatchService`. На нём построены Netty, NIO-коннектор Tomcat и Spring WebFlux; сырые селекторы самому писать приходится редко.

## Основное
### Потоки против каналов
| Потоки `java.io` | Каналы NIO |
|---|---|
| Одно направление на поток | Канал обычно двунаправленный (`SocketChannel` и читает, и пишет) |
| Блокирующие: поток ждёт на каждом вызове | Блокирующие или неблокирующие (`configureBlocking(false)`) |
| Данные идут по байту или через массивы | Данные идут через `ByteBuffer`; прямые (direct) буферы избегают копирования в кучу Java |
| Один поток на соединение | Один поток с селектором мультиплексирует тысячи соединений |

- Официально «N» означает «New», но всех волнует именно свойство «non-blocking». Неблокирующий означает, что `read` возвращается сразу с тем, что доступно (возможно, 0 байт); поток никогда не «паркуется» внутри ядра.

### Буферы
- `ByteBuffer` (а также `CharBuffer`, `IntBuffer`, ...): массив фиксированного размера с тремя индексами: **position** (следующий элемент), **limit** (первый элемент, который трогать нельзя), **capacity**. Порядок работы: записать в него (`put`/`channel.read(buf)`), `flip()`, чтобы переключиться на чтение (limit = position, position = 0), прочитать (`get`/`channel.write(buf)`), затем `clear()` или `compact()`, чтобы снова писать.
- `allocate(n)` — буфер в куче (на основе `byte[]`); `allocateDirect(n)` — память вне кучи (off-heap), в которую ОС может писать напрямую через DMA; её дороже выделять, а освобождает её `Cleaner` ([[reference-types]]), поэтому прямые буферы объединяют в пулы и переиспользуют. Для этого существует `PooledByteBufAllocator` в Netty.
- `MappedByteBuffer` из `FileChannel.map()` отображает файл в память: произвольный доступ к огромным файлам без вызовов read (сегменты лога Kafka, Lucene).

### Каналы
- `FileChannel` (чтение по позиции, `transferTo`/`transferFrom` для zero-copy из файла в сокет, блокировки), `SocketChannel`/`ServerSocketChannel` (TCP), `DatagramChannel` (UDP), `Pipe`. `FileChannel` не может быть неблокирующим; файлы всегда «готовы».
- `AsynchronousFileChannel` и `AsynchronousSocketChannel` (NIO.2) запускают операцию и доставляют результат через `Future` или колбэк `CompletionHandler`, за кулисами — на пуле потоков.

### Селектор: паттерн reactor
1. Зарегистрировать каналы в `Selector` с интересующими операциями: `OP_ACCEPT`, `OP_CONNECT`, `OP_READ`, `OP_WRITE`; каждая регистрация — это `SelectionKey` с необязательным вложением (attachment; твоё состояние на соединение).
2. `selector.select()` блокируется, пока хотя бы один канал не станет готов (epoll в Linux, kqueue в macOS).
3. Пройти по `selectedKeys()`, обработать каждый ключ, **удалить ключ из множества**.
- Один поток цикла событий (event loop) обрабатывает ввод-вывод; тяжёлая для CPU работа передаётся пулу рабочих потоков ([[executors-and-thread-pools]]). Так Netty, Vert.x и Reactor Netty (WebFlux, [[spring-reactive-webclient-sse]]) масштабируются на множество соединений с малым числом потоков.
- Сравни с виртуальными потоками ([[virtual-threads]], Java 21): та же масштабируемость с кодом в блокирующем стиле; под капотом JDK паркует виртуальный поток на том же механизме epoll.

### NIO.2: Path, Files, WatchService
- `Path` — неизменяемый путь, знающий о файловой системе; в `Files` есть статические помощники для чтения, записи, копирования, обхода, атрибутов и атомарных перемещений (`StandardCopyOption.ATOMIC_MOVE`). Предпочитай их `java.io.File` ([[java-io-streams]]).
- `WatchService` подписывается на изменения в каталоге (создание/изменение/удаление) с помощью inotify; в macOS — на основе опроса (polling).
- `FileSystems.newFileSystem(zipPath)` работает с zip/jar как с файловой системой.

## Пример
```java
// Минимальный однопоточный эхо-сервер на селекторе (то, что Netty от тебя прячет).
public static void serve(int port) throws IOException {
    try (Selector selector = Selector.open();
         ServerSocketChannel server = ServerSocketChannel.open()) {
        server.bind(new InetSocketAddress(port));
        server.configureBlocking(false);
        server.register(selector, SelectionKey.OP_ACCEPT);

        while (true) {
            selector.select();                                   // блокируется, пока что-нибудь не будет готово
            Iterator<SelectionKey> it = selector.selectedKeys().iterator();
            while (it.hasNext()) {
                SelectionKey key = it.next();
                it.remove();                                     // обязательно
                if (key.isAcceptable()) {
                    SocketChannel client = server.accept();
                    client.configureBlocking(false);
                    client.register(selector, SelectionKey.OP_READ, ByteBuffer.allocateDirect(4096));
                } else if (key.isReadable()) {
                    SocketChannel client = (SocketChannel) key.channel();
                    ByteBuffer buf = (ByteBuffer) key.attachment();
                    int n = client.read(buf);                     // возвращается сразу
                    if (n == -1) { client.close(); continue; }
                    buf.flip();
                    while (buf.hasRemaining()) client.write(buf); // отправить обратно
                    buf.clear();
                }
            }
        }
    }
}

// NIO.2 в повседневной работе: атомарная замена файла конфигурации
Path tmp = Files.createTempFile(dir, "tariffs", ".json");
Files.writeString(tmp, json);
Files.move(tmp, dir.resolve("tariffs.json"), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
```

## Подводные камни
- Если забыть `flip()` перед выводом буфера, ничего не отправится (position == limit); если забыть `clear()`/`compact()` после, следующее чтение вернёт 0 байт. Большинство багов NIO — это баги индексов буфера.
- Если не удалять ключи из `selectedKeys()`, цикл будет вечно обрабатывать устаревшие события; постоянная регистрация `OP_WRITE` раскручивает CPU вхолостую, потому что сокеты почти всегда доступны для записи.
- Прямые буферы находятся вне кучи: `-Xmx` их не ограничивает (ограничивает `-XX:MaxDirectMemorySize`), и их утечка проявляется как рост нативной памяти, а не как OOM кучи ([[jvm-memory-areas]]).
- Блокирующий код (JDBC, вызов со sleep) в потоке event loop останавливает все соединения; это смертный грех приложений на WebFlux/Netty.
- Связанные заметки: [[java-io-streams]], [[virtual-threads]], [[spring-reactive-webclient-sse]].
