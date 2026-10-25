---
title: Java IO streams
tags: [java-io, streams, reader-writer, buffering, serialization]
learned: 2026-10-22
---
## TL;DR
`java.io` models input and output as **streams**: a sequence of bytes (`InputStream`/`OutputStream`) or characters (`Reader`/`Writer`) flowing from or to a file, socket, pipe or memory. The classes stack as decorators: a raw stream over the source, a buffer on top, then a converter or data-format layer. Every stream is a native resource and must be closed, which `try-with-resources` does for you.

## Key points
### The four base classes
| | Bytes | Characters (UTF-16 `char`) |
|---|---|---|
| Input | `InputStream`: `read()`, `read(byte[])`, `readAllBytes()` (9), `transferTo` (9) | `Reader`: `read()`, `read(char[])`, `BufferedReader.readLine()`, `lines()` |
| Output | `OutputStream`: `write`, `flush`, `close` | `Writer`: `write(String)`, `append`, `flush` |

- Byte streams are for binary data (images, protobuf, zip, sockets). Character streams are for text and always involve a **charset**: `InputStreamReader`/`OutputStreamWriter` are the bridges and should always be given `StandardCharsets.UTF_8` explicitly; until Java 18 the default charset was platform-dependent (JEP 400 made UTF-8 the default).
- The base classes are abstract; `read()` blocks until at least one byte is available, returns `-1` at end of stream. This is **blocking** I/O: the calling thread waits in the kernel. Non-blocking alternatives are in [[java-nio]]; with virtual threads ([[virtual-threads]]) blocking I/O scales again.

### Decorator stacking
- Sources/sinks: `FileInputStream`/`FileOutputStream`, `ByteArrayInputStream`, `socket.getInputStream()`, `PipedInputStream`, `Files.newInputStream(path)` (preferred, NIO-backed).
- `BufferedInputStream`/`BufferedOutputStream`/`BufferedReader`/`BufferedWriter`: keep an 8 KB buffer in memory so that one system call moves thousands of bytes instead of one; without them `read()` per byte is catastrophically slow. `BufferedReader` adds `readLine()`.
- `DataInputStream`/`DataOutputStream`: primitives in big-endian binary (`readInt`, `writeUTF`).
- `ObjectInputStream`/`ObjectOutputStream`: Java serialization of `Serializable` objects. Avoid for anything crossing a trust boundary: deserialization of untrusted bytes is a remote-code-execution vector; use JSON/protobuf and, if you must, `ObjectInputFilter` (Java 9).
- `PrintStream`/`PrintWriter`: `println`, `printf`; `System.out` is a `PrintStream`. `PrintWriter` swallows `IOException` (check `checkError()`).
- `GZIPInputStream`, `CipherInputStream`, `DigestInputStream`: transformations in the same chain. This layering is the textbook Decorator pattern ([[structural-patterns]]).

### Closing and exceptions
- Nearly every method throws the checked `IOException` ([[exceptions]]); close in `try-with-resources` so that the stream is released even when reading fails. Closing the outermost decorator closes the whole chain and flushes buffers; forgetting `flush`/`close` on a buffered writer loses the tail of the file.
- `File` (old) vs `Path`/`Files` (NIO.2, Java 7): `Files.readString`, `Files.writeString` (11), `Files.lines`, `Files.copy`, `Files.walk` cover most file tasks without manual streams; `File` is kept for legacy APIs.

### Scanner and Console
- `Scanner` tokenizes text from any stream with regexes; convenient, slow, for CLI input. `Console.readPassword()` returns `char[]` ([[strings-and-string-pool]]).

### Performance notes
- Buffer sizes: 8 KB default; 64 KB+ for large sequential files. `InputStream.transferTo(OutputStream)` copies with an internal buffer and, since 21, can use `sendfile`-like fast paths for file channels.
- Reading a whole file into memory is fine up to tens of MB; stream line by line (`Files.lines`, `BufferedReader.lines()`) above that.

## Example
```java
// Import a CDR (call detail record) file: text, line by line, gzip-compressed, UTF-8, buffered.
public long importCdrs(Path gzFile, Consumer<Cdr> sink) throws IOException {
    long count = 0;
    try (InputStream raw = Files.newInputStream(gzFile);                        // source
         InputStream gunzip = new GZIPInputStream(raw, 64 * 1024);              // transformation
         Reader chars = new InputStreamReader(gunzip, StandardCharsets.UTF_8);  // bytes -> chars
         BufferedReader in = new BufferedReader(chars, 64 * 1024)) {            // buffer + readLine
        String line;
        while ((line = in.readLine()) != null) {
            if (line.isBlank() || line.startsWith("#")) continue;
            sink.accept(Cdr.parse(line));
            count++;
        }
    }                                                                           // all four closed, inner first
    return count;
}

// Writing a small text file: no manual streams needed
Files.writeString(Path.of("/var/app/last-run.txt"), Instant.now().toString(),
                  StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
```

## Pitfalls
- `new FileReader(path)` without a charset used the platform default before Java 18; files written on Windows and read on Linux came out garbled. Always pass `UTF_8`.
- Unbuffered `FileInputStream.read()` in a loop makes one syscall per byte; wrap in a `BufferedInputStream` or use `readAllBytes`/`transferTo`.
- `InputStream.read(byte[])` may return fewer bytes than requested; loop until `-1` or use `readNBytes`.
- `available()` is not the remaining length of the stream; it is only a hint for non-blocking reads.
- Related: [[java-nio]], [[exceptions]], [[virtual-threads]].
