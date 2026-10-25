---
title: Exceptions
tags: [exceptions, checked, unchecked, try-with-resources, error-handling]
learned: 2026-10-22
---
## TL;DR
An exception is an event that interrupts the normal flow of execution and unwinds the call stack until something catches it. `Throwable` has two branches: `Error` (JVM-level problems you do not handle) and `Exception`, which splits into **checked** (must be caught or declared, for recoverable conditions) and **unchecked** `RuntimeException` (programming errors and, in modern practice, most business failures). `finally` always runs except when the JVM itself stops.

## Key points
### Hierarchy
```
Throwable
├── Error                      OutOfMemoryError, StackOverflowError, NoClassDefFoundError, AssertionError
└── Exception
    ├── RuntimeException       NPE, IllegalArgumentException, IllegalStateException, ClassCastException,
    │                          ArithmeticException, IndexOutOfBounds, UnsupportedOperationException
    └── (checked)              IOException, SQLException, InterruptedException, ClassNotFoundException
```
- **Errors** are serious problems of the platform (heap exhausted, stack overflow, class loading broken); the spec says a program should not try to handle them. In practice you let them kill the request or the process and rely on a restart.
- **Exceptions** are problems in the program's own logic or environment that can be predicted and dealt with: a file is missing, a number is divided by zero, input is invalid.

### Checked vs unchecked
| | Checked | Unchecked (`RuntimeException`, `Error`) |
|---|---|---|
| Compiler | must be caught or declared with `throws` | nothing required |
| Meaning | caller can reasonably recover (retry, fall back) | bug or precondition violation; or a failure the caller cannot fix |
| Overriding | an override may declare the same, narrower or none; never new or wider | unrestricted |
| Lambdas/streams | do not fit: functional interfaces declare none, so you wrap | fit |

- Modern libraries (Spring, Hibernate, Kafka clients) use unchecked exceptions almost exclusively; Spring wraps `SQLException` into its `DataAccessException` hierarchy for that reason. Checked exceptions survive mainly in I/O ([[java-io-streams]]) and in `InterruptedException` ([[threads-basics]]).
- Rule of thumb: throw unchecked for programming errors and for failures where the caller's only option is to abort; throw checked (or return a result type) only when every caller must decide what to do.

### The keywords
- `try`: the block in which an exception may arise. `try` alone is illegal: it needs at least one `catch` or a `finally` (or resources in `try (...)`).
- `catch (A | B e)`: handle; multi-catch since Java 7; order from specific to general or the compiler rejects unreachable catches.
- `throw e`: raise. `throws X` in a method header: declare.
- `finally`: runs after the try/catch whether an exception occurred or not; used for cleanup. It does **not** run if the JVM exits in the middle (`System.exit`, `Runtime.halt`, a crash, the process being killed) or if the thread blocks forever. A `return` in `finally` overrides the method's return value and swallows a pending exception; never do that.
- `try-with-resources` (Java 7): any `AutoCloseable` declared in `try (...)` is closed in reverse order after the block, even on exception; exceptions thrown by `close()` are attached as *suppressed* (`e.getSuppressed()`). Java 9 allows an effectively final variable declared earlier: `try (conn) { ... }`.

### Exception chaining and messages
- Wrap low-level exceptions with a cause: `new PaymentFailedException("charge " + id, e)`. Losing the cause (`catch (SQLException e) { throw new RuntimeException("db error"); }`) loses the stack trace that explains the failure.
- Custom exceptions: extend `RuntimeException` (or a domain base class), keep them immutable, carry structured data (error code, entity id) as fields, not only in the message.
- Stack-trace capture is the expensive part of an exception (`fillInStackTrace`); for control flow in hot paths, which you should avoid anyway, the constructor flag `writableStackTrace=false` disables it.

### Where exceptions end up in a Spring service
- `@ControllerAdvice` + `@ExceptionHandler` map domain exceptions to HTTP statuses ([[spring-mvc-request-lifecycle]], [[rest-and-http]]).
- `@Transactional` rolls back on unchecked exceptions by default and **commits** on checked ones unless `rollbackFor` is set ([[spring-transactional]]).
- Kafka listeners: an exception from the handler drives the retry/dead-letter policy ([[spring-kafka-consumer]]).

## Example
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
    try (InputStream in = smdp.open(iccid);                       // AutoCloseable resources,
         OutputStream out = Files.newOutputStream(file)) {        // closed in reverse order
        byte[] data = in.readAllBytes();
        out.write(data);
        return data;
    } catch (NoSuchFileException | AccessDeniedException e) {     // multi-catch, specific first
        throw new ProfileDownloadException(iccid, "cache dir not writable: " + cacheDir, e);
    } catch (IOException e) {                                     // general last
        throw new ProfileDownloadException(iccid, "download failed", e);
    } finally {
        metrics.increment("profile.download.attempts");           // runs on success and failure
    }
}
```

## Pitfalls
- `catch (Exception e) {}` (swallowing) and `catch (Throwable t)` (which also catches `OutOfMemoryError`) hide failures; at minimum log with the exception object, not `e.getMessage()`.
- Catching `InterruptedException` without `Thread.currentThread().interrupt()` loses the interrupt and breaks thread-pool shutdown ([[threads-basics]]).
- Using exceptions for expected flow (`NumberFormatException` to test "is it a number") is slow and unreadable; validate first or return `Optional`.
- Checked exceptions inside lambdas lead to ugly wrappers; either convert to unchecked at the boundary or use a small `ThrowingFunction` helper.
- Related: [[java-io-streams]], [[spring-transactional]], [[object-class-and-reflection]] (`InvocationTargetException` unwrapping).
