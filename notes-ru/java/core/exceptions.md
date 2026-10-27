---
title: Исключения
tags: [exceptions, checked, unchecked, try-with-resources, error-handling]
learned: 2026-10-22
---
## Кратко
Исключение — это событие, которое прерывает нормальный ход выполнения и раскручивает стек вызовов, пока его кто-нибудь не поймает. У `Throwable` две ветви: `Error` (проблемы уровня JVM, которые не обрабатывают) и `Exception`, который делится на **проверяемые** (checked; их обязательно ловить или объявлять, они для ситуаций, после которых можно восстановиться) и **непроверяемые** `RuntimeException` (unchecked; ошибки программирования и, в современной практике, большинство бизнес-сбоев). `finally` выполняется всегда, кроме случаев, когда останавливается сама JVM.

## Основное
### Иерархия
```
Throwable
├── Error                      OutOfMemoryError, StackOverflowError, NoClassDefFoundError, AssertionError
└── Exception
    ├── RuntimeException       NPE, IllegalArgumentException, IllegalStateException, ClassCastException,
    │                          ArithmeticException, IndexOutOfBounds, UnsupportedOperationException
    └── (checked)              IOException, SQLException, InterruptedException, ClassNotFoundException
```
- **Ошибки (Error)** — серьёзные проблемы платформы (куча исчерпана, переполнение стека, сломана загрузка классов); спецификация говорит, что программа не должна пытаться их обрабатывать. На практике им позволяют убить запрос или процесс и полагаются на перезапуск.
- **Исключения (Exception)** — проблемы в собственной логике программы или её окружении, которые можно предвидеть и обработать: файла нет, число делится на ноль, входные данные некорректны.

### Проверяемые и непроверяемые
| | Проверяемые | Непроверяемые (`RuntimeException`, `Error`) |
|---|---|---|
| Компилятор | нужно поймать или объявить через `throws` | ничего не требует |
| Смысл | вызывающий код может разумно восстановиться (retry, fallback) | баг или нарушение предусловия; либо сбой, который вызывающий код не может исправить |
| Переопределение | переопределение может объявить те же, более узкие или никаких; никогда новые или более широкие | без ограничений |
| Лямбды/стримы | не подходят: функциональные интерфейсы не объявляют исключений, приходится оборачивать | подходят |

- Современные библиотеки (Spring, Hibernate, клиенты Kafka) почти исключительно используют непроверяемые исключения; именно поэтому Spring оборачивает `SQLException` в свою иерархию `DataAccessException`. Проверяемые исключения сохранились в основном во вводе-выводе ([[java-io-streams]]) и в `InterruptedException` ([[threads-basics]]).
- Практическое правило: бросай непроверяемые для ошибок программирования и для сбоев, при которых у вызывающего кода единственный вариант — прервать работу; бросай проверяемые (или возвращай тип-результат), только когда каждый вызывающий обязан решить, что делать.

### Ключевые слова
- `try`: блок, в котором может возникнуть исключение. `try` сам по себе недопустим: нужен хотя бы один `catch` или `finally` (или ресурсы в `try (...)`).
- `catch (A | B e)`: обработка; multi-catch — с Java 7; порядок от частного к общему, иначе компилятор отвергнет недостижимые catch.
- `throw e`: бросить. `throws X` в заголовке метода: объявить.
- `finally`: выполняется после try/catch независимо от того, было исключение или нет; используется для очистки. Он **не** выполняется, если JVM завершается посередине (`System.exit`, `Runtime.halt`, падение, принудительное убийство процесса) или если поток блокируется навсегда. `return` в `finally` подменяет возвращаемое значение метода и проглатывает ожидающее исключение; никогда так не делай.
- `try-with-resources` (Java 7): любой `AutoCloseable`, объявленный в `try (...)`, закрывается в обратном порядке после блока, даже при исключении; исключения, брошенные `close()`, прикрепляются как *подавленные* (suppressed, `e.getSuppressed()`). Java 9 разрешает использовать объявленную ранее effectively final переменную: `try (conn) { ... }`.

### Цепочки исключений и сообщения
- Оборачивай низкоуровневые исключения с указанием причины: `new PaymentFailedException("charge " + id, e)`. Потеря причины (`catch (SQLException e) { throw new RuntimeException("db error"); }`) теряет стек-трейс, который объясняет сбой.
- Собственные исключения: расширяй `RuntimeException` (или базовый класс домена), делай их неизменяемыми, передавай структурированные данные (код ошибки, id сущности) в полях, а не только в сообщении.
- Дорогая часть исключения — захват стек-трейса (`fillInStackTrace`); для управления потоком выполнения на горячих путях (чего и так стоит избегать) флаг конструктора `writableStackTrace=false` его отключает.

### Куда попадают исключения в Spring-сервисе
- `@ControllerAdvice` + `@ExceptionHandler` отображают доменные исключения на HTTP-статусы ([[spring-mvc-request-lifecycle]], [[rest-and-http]]).
- `@Transactional` по умолчанию откатывает транзакцию при непроверяемых исключениях и **делает commit** при проверяемых, если не задан `rollbackFor` ([[spring-transactional]]).
- Kafka-слушатели: исключение из обработчика запускает политику retry/dead-letter ([[spring-kafka-consumer]]).

## Пример
```java
public class ProfileDownloadException extends RuntimeException {
    private final String iccid;
    public ProfileDownloadException(String iccid, String message, Throwable cause) {
        super("iccid=" + iccid + ": " + message, cause);
        this.iccid = iccid;
    }
    public String iccid() { return iccid; }
}

public byte[] downloadProfile(String iccid, Path cacheDir) {
    Path file = cacheDir.resolve(iccid + ".bin");
    try (InputStream in = smdp.open(iccid);                       // ресурсы AutoCloseable,
         OutputStream out = Files.newOutputStream(file)) {        // закрываются в обратном порядке
        byte[] data = in.readAllBytes();
        out.write(data);
        return data;
    } catch (NoSuchFileException | AccessDeniedException e) {     // multi-catch, частные первыми
        throw new ProfileDownloadException(iccid, "cache dir not writable: " + cacheDir, e);
    } catch (IOException e) {                                     // общий последним
        throw new ProfileDownloadException(iccid, "download failed", e);
    } finally {
        metrics.increment("profile.download.attempts");           // выполняется и при успехе, и при сбое
    }
}
```

## Подводные камни
- `catch (Exception e) {}` (проглатывание) и `catch (Throwable t)` (ловит в том числе `OutOfMemoryError`) скрывают сбои; как минимум логируй с объектом исключения, а не `e.getMessage()`.
- Перехват `InterruptedException` без `Thread.currentThread().interrupt()` теряет флаг прерывания и ломает остановку пула потоков ([[threads-basics]]).
- Использование исключений для ожидаемого хода выполнения (`NumberFormatException`, чтобы проверить «число ли это») медленно и нечитаемо; сначала проверяй или возвращай `Optional`.
- Проверяемые исключения внутри лямбд приводят к уродливым обёрткам; либо превращай их в непроверяемые на границе, либо используй небольшой помощник `ThrowingFunction`.
- Связанные заметки: [[java-io-streams]], [[spring-transactional]], [[object-class-and-reflection]] (разворачивание `InvocationTargetException`).
