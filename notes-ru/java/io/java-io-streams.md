---
title: Потоки ввода-вывода Java IO
tags: [java-io, streams, reader-writer, buffering, serialization]
learned: 2026-10-22
---
## Кратко
`java.io` представляет ввод и вывод как **потоки** (streams): последовательность байтов (`InputStream`/`OutputStream`) или символов (`Reader`/`Writer`), идущих из файла, сокета, канала (pipe) или памяти либо туда. Классы складываются как декораторы: «сырой» поток поверх источника, сверху буфер, затем слой преобразования или формата данных. Каждый поток — нативный ресурс, и его нужно закрывать, что за тебя делает `try-with-resources`.

## Основное
### Четыре базовых класса
| | Байты | Символы (UTF-16 `char`) |
|---|---|---|
| Ввод | `InputStream`: `read()`, `read(byte[])`, `readAllBytes()` (9), `transferTo` (9) | `Reader`: `read()`, `read(char[])`, `BufferedReader.readLine()`, `lines()` |
| Вывод | `OutputStream`: `write`, `flush`, `close` | `Writer`: `write(String)`, `append`, `flush` |

- Байтовые потоки — для двоичных данных (изображения, protobuf, zip, сокеты). Символьные потоки — для текста, и в них всегда участвует **кодировка** (charset): мостами служат `InputStreamReader`/`OutputStreamWriter`, и им всегда стоит явно передавать `StandardCharsets.UTF_8`; до Java 18 кодировка по умолчанию зависела от платформы (JEP 400 сделал UTF-8 кодировкой по умолчанию).
- Базовые классы абстрактные; `read()` блокируется, пока не станет доступен хотя бы один байт, и возвращает `-1` в конце потока. Это **блокирующий** ввод-вывод: вызывающий поток ждёт в ядре. Неблокирующие альтернативы — в [[java-nio]]; с виртуальными потоками ([[virtual-threads]]) блокирующий ввод-вывод снова масштабируется.

### Наслоение декораторов
- Источники/приёмники: `FileInputStream`/`FileOutputStream`, `ByteArrayInputStream`, `socket.getInputStream()`, `PipedInputStream`, `Files.newInputStream(path)` (предпочтительно, на основе NIO).
- `BufferedInputStream`/`BufferedOutputStream`/`BufferedReader`/`BufferedWriter`: держат в памяти буфер на 8 КБ, чтобы один системный вызов перемещал тысячи байтов, а не один; без них `read()` по одному байту катастрофически медленный. `BufferedReader` добавляет `readLine()`.
- `DataInputStream`/`DataOutputStream`: примитивы в двоичном виде big-endian (`readInt`, `writeUTF`).
- `ObjectInputStream`/`ObjectOutputStream`: стандартная сериализация Java для объектов `Serializable`. Избегай её для всего, что пересекает границу доверия: десериализация недоверенных байтов — вектор удалённого выполнения кода; используй JSON/protobuf, а если без неё никак — `ObjectInputFilter` (Java 9).
- `PrintStream`/`PrintWriter`: `println`, `printf`; `System.out` — это `PrintStream`. `PrintWriter` проглатывает `IOException` (проверяй `checkError()`).
- `GZIPInputStream`, `CipherInputStream`, `DigestInputStream`: преобразования в той же цепочке. Такое наслоение — хрестоматийный паттерн Decorator ([[structural-patterns]]).

### Закрытие и исключения
- Почти каждый метод бросает проверяемое `IOException` ([[exceptions]]); закрывай в `try-with-resources`, чтобы поток освобождался, даже когда чтение падает. Закрытие самого внешнего декоратора закрывает всю цепочку и сбрасывает буферы; если забыть `flush`/`close` у буферизованного writer, хвост файла потеряется.
- `File` (старый) против `Path`/`Files` (NIO.2, Java 7): `Files.readString`, `Files.writeString` (11), `Files.lines`, `Files.copy`, `Files.walk` покрывают большинство задач с файлами без ручной работы с потоками; `File` оставлен для унаследованных API.

### Scanner и Console
- `Scanner` разбивает текст из любого потока на токены с помощью регулярных выражений; удобно, медленно, для ввода в CLI. `Console.readPassword()` возвращает `char[]` ([[strings-and-string-pool]]).

### Заметки о производительности
- Размеры буферов: 8 КБ по умолчанию; 64 КБ и больше для больших последовательно читаемых файлов. `InputStream.transferTo(OutputStream)` копирует через внутренний буфер и, начиная с 21, может использовать быстрые пути наподобие `sendfile` для файловых каналов.
- Читать файл целиком в память нормально до десятков МБ; при большем размере читай построчно потоком (`Files.lines`, `BufferedReader.lines()`).

## Пример
```java
// Импорт файла CDR (call detail record): текст, построчно, сжат gzip, UTF-8, с буферизацией.
public long importCdrs(Path gzFile, Consumer<Cdr> sink) throws IOException {
    long count = 0;
    try (InputStream raw = Files.newInputStream(gzFile);                        // источник
         InputStream gunzip = new GZIPInputStream(raw, 64 * 1024);              // преобразование
         Reader chars = new InputStreamReader(gunzip, StandardCharsets.UTF_8);  // байты -> символы
         BufferedReader in = new BufferedReader(chars, 64 * 1024)) {            // буфер + readLine
        String line;
        while ((line = in.readLine()) != null) {
            if (line.isBlank() || line.startsWith("#")) continue;
            sink.accept(Cdr.parse(line));
            count++;
        }
    }                                                                           // все четыре закрыты, внутренний первым
    return count;
}

// Запись небольшого текстового файла: ручные потоки не нужны
Files.writeString(Path.of("/var/app/last-run.txt"), Instant.now().toString(),
                  StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
```

## Подводные камни
- `new FileReader(path)` без кодировки до Java 18 использовал кодировку платформы по умолчанию; файлы, записанные в Windows и прочитанные в Linux, получались искажёнными. Всегда передавай `UTF_8`.
- Небуферизованный `FileInputStream.read()` в цикле делает один системный вызов на байт; оберни в `BufferedInputStream` или используй `readAllBytes`/`transferTo`.
- `InputStream.read(byte[])` может вернуть меньше байтов, чем запрошено; читай в цикле до `-1` или используй `readNBytes`.
- `available()` — это не оставшаяся длина потока; это лишь подсказка для неблокирующего чтения.
- Связанные заметки: [[java-nio]], [[exceptions]], [[virtual-threads]].
