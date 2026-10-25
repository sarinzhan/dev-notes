---
title: JVM, JRE and JDK
tags: [jvm, jdk, jre, bytecode, jit]
learned: 2026-10-22
---
## TL;DR
The **JVM** executes bytecode; the **JRE** is the JVM plus the standard class library, enough to run a program; the **JDK** is the JRE plus the compiler and tools, what you need to build one. Since Java 11 Oracle ships no separate JRE: you install a JDK and build a trimmed runtime with `jlink` if you want something smaller.

## Key points
### The three layers
- **JVM** (Java Virtual Machine): loads `.class` files, verifies them, interprets bytecode and JIT-compiles hot methods to native code, manages memory ([[jvm-memory-areas]], [[garbage-collection]]) and threads. It is a specification; HotSpot (OpenJDK) is the usual implementation, OpenJ9 and GraalVM are others. Any language that compiles to bytecode (Kotlin, Scala, Groovy, Clojure) runs on it.
- **JRE** (Java Runtime Environment): JVM + `java.base` and the other standard modules (`java.sql`, `java.net.http`, ...) + launcher. Enough to run, not to compile.
- **JDK** (Java Development Kit): JRE + `javac`, `jar`, `javadoc`, `jshell`, `jlink`, `jpackage`, diagnostics (`jcmd`, `jstack`, `jmap`, `jfr`, JConsole/VisualVM via plugins).

### From source to running code
1. `javac` compiles `.java` to platform-independent bytecode (`.class`). This is "write once, run anywhere": the bytecode is the same, the JVM is per platform.
2. The launcher `java` starts the JVM; the class loader hierarchy loads classes lazily on first use ([[class-initialization-order]]): bootstrap (`java.base`), platform, application (classpath/module path), plus custom loaders (Spring Boot's fat-jar loader, Tomcat's per-webapp loader).
3. The bytecode verifier checks type safety and stack discipline so that malformed classes cannot corrupt the VM.
4. The interpreter runs bytecode; the JIT compiler (C1 quickly, C2 for hot code, "tiered compilation") compiles methods to native code after they cross invocation thresholds, using profiling data for inlining, escape analysis and devirtualization. Code can be **deoptimized** if an assumption (for example "this interface has one implementation") stops holding.
- GraalVM native-image is the alternative: ahead-of-time compile to a native binary with no JIT, fast start, lower peak throughput, closed-world assumptions that break reflection unless configured (Spring Boot 3 supports it via AOT processing).

### Versions and vendors
- OpenJDK is the reference source; builds come from Oracle, Eclipse Temurin (Adoptium), Amazon Corretto, Azul Zulu, Microsoft, Red Hat. They are functionally the same; pick by support policy.
- Release cadence: a feature release every six months, an LTS every two years: 8, 11, 17, 21, 25. Production usually tracks LTS.
- Important runtime flags: `-Xms`/`-Xmx` heap, `-XX:+UseZGC`/`-XX:+UseG1GC`, `-XX:MaxMetaspaceSize`, `-XX:+HeapDumpOnOutOfMemoryError`, `-XX:+UseContainerSupport` (default since 10: the JVM reads cgroup limits inside Docker, see [[docker-containers-and-resources]]).

## Example
```bash
# Build and run without a build tool (Java 11+ can even run a single source file directly)
javac -d out --release 21 src/main/java/kg/beeline/esim/Main.java
java -cp out kg.beeline.esim.Main

java src/main/java/kg/beeline/esim/Main.java      # source-file mode, compiles in memory

# Produce a minimal runtime image (replaces the old separate JRE)
jlink --add-modules java.base,java.sql,java.net.http \
      --strip-debug --no-man-pages --output build/runtime
build/runtime/bin/java -version

# Look inside the JVM of a running service
jcmd <pid> GC.heap_info
jstack <pid> | grep -A5 'http-nio'
```

## Pitfalls
- Compiling with a newer JDK than the runtime gives `UnsupportedClassVersionError`; always set `--release N` (or Maven `maven.compiler.release`, see [[maven]]) rather than `-source/-target`, because `--release` also checks the API.
- `JAVA_HOME` pointing at a JRE breaks Maven and IDEs that need `javac`; point it at the JDK.
- A JIT-less warm-up explains why the first requests of a Spring Boot service are slow; benchmarks must warm up (JMH does it for you).
- Related: [[jvm-memory-areas]], [[garbage-collection]], [[reference-types]].
