---
title: JVM, JRE и JDK
tags: [jvm, jdk, jre, bytecode, jit]
learned: 2026-10-22
---
## Кратко
**JVM** выполняет байткод; **JRE** — это JVM плюс стандартная библиотека классов, этого достаточно, чтобы запустить программу; **JDK** — это JRE плюс компилятор и инструменты, то, что нужно, чтобы её собрать. Начиная с Java 11 Oracle не поставляет отдельный JRE: устанавливается JDK, а если нужно что-то меньше, с помощью `jlink` собирается урезанная среда выполнения.

## Основное
### Три уровня
- **JVM** (Java Virtual Machine): загружает файлы `.class`, проверяет их, интерпретирует байткод и JIT-компилирует горячие методы в машинный код, управляет памятью ([[jvm-memory-areas]], [[garbage-collection]]) и потоками. Это спецификация; обычная реализация — HotSpot (OpenJDK), есть и другие — OpenJ9 и GraalVM. На ней работает любой язык, компилируемый в байткод (Kotlin, Scala, Groovy, Clojure).
- **JRE** (Java Runtime Environment): JVM + `java.base` и другие стандартные модули (`java.sql`, `java.net.http`, ...) + лаунчер. Достаточно для запуска, но не для компиляции.
- **JDK** (Java Development Kit): JRE + `javac`, `jar`, `javadoc`, `jshell`, `jlink`, `jpackage`, средства диагностики (`jcmd`, `jstack`, `jmap`, `jfr`, JConsole/VisualVM через плагины).

### От исходного кода до работающей программы
1. `javac` компилирует `.java` в платформенно-независимый байткод (`.class`). Это и есть «write once, run anywhere»: байткод один и тот же, а JVM своя для каждой платформы.
2. Лаунчер `java` запускает JVM; иерархия загрузчиков классов загружает классы лениво при первом использовании ([[class-initialization-order]]): bootstrap (`java.base`), platform, application (classpath/module path), плюс пользовательские загрузчики (загрузчик fat-jar в Spring Boot, загрузчик на каждое веб-приложение в Tomcat).
3. Верификатор байткода проверяет типобезопасность и дисциплину стека, чтобы некорректные классы не могли повредить VM.
4. Интерпретатор выполняет байткод; JIT-компилятор (C1 — быстро, C2 — для горячего кода, «многоуровневая компиляция» (tiered compilation)) компилирует методы в машинный код после того, как они превысят пороги вызовов, используя данные профилирования для инлайнинга, escape analysis и девиртуализации. Код может быть **деоптимизирован**, если допущение (например, «у этого интерфейса одна реализация») перестаёт выполняться.
- Альтернатива — GraalVM native-image: компиляция заранее (ahead-of-time) в нативный бинарник без JIT, быстрый старт, более низкая пиковая пропускная способность, допущение замкнутого мира (closed world), которое ломает рефлексию без настройки (Spring Boot 3 поддерживает это через AOT-обработку).

### Версии и вендоры
- OpenJDK — эталонный исходный код; сборки выпускают Oracle, Eclipse Temurin (Adoptium), Amazon Corretto, Azul Zulu, Microsoft, Red Hat. Функционально они одинаковы; выбирают по политике поддержки.
- Ритм релизов: функциональный релиз каждые шесть месяцев, LTS каждые два года: 8, 11, 17, 21, 25. В продакшене обычно следуют за LTS.
- Важные флаги запуска: `-Xms`/`-Xmx` — куча, `-XX:+UseZGC`/`-XX:+UseG1GC`, `-XX:MaxMetaspaceSize`, `-XX:+HeapDumpOnOutOfMemoryError`, `-XX:+UseContainerSupport` (по умолчанию начиная с 10: JVM читает лимиты cgroup внутри Docker, см. [[docker-containers-and-resources]]).

## Пример
```bash
# Сборка и запуск без системы сборки (Java 11+ может даже запустить одиночный исходный файл напрямую)
javac -d out --release 21 src/main/java/kg/beeline/esim/Main.java
java -cp out kg.beeline.esim.Main

java src/main/java/kg/beeline/esim/Main.java      # режим исходного файла, компилирует в памяти

# Собрать минимальный образ среды выполнения (заменяет старый отдельный JRE)
jlink --add-modules java.base,java.sql,java.net.http \
      --strip-debug --no-man-pages --output build/runtime
build/runtime/bin/java -version

# Заглянуть внутрь JVM работающего сервиса
jcmd <pid> GC.heap_info
jstack <pid> | grep -A5 'http-nio'
```

## Подводные камни
- Компиляция более новым JDK, чем среда выполнения, даёт `UnsupportedClassVersionError`; всегда задавай `--release N` (или в Maven `maven.compiler.release`, см. [[maven]]), а не `-source/-target`, потому что `--release` проверяет ещё и API.
- `JAVA_HOME`, указывающий на JRE, ломает Maven и IDE, которым нужен `javac`; указывай его на JDK.
- Прогрев без JIT объясняет, почему первые запросы к сервису на Spring Boot медленные; бенчмарки должны прогреваться (JMH делает это за тебя).
- Связанные заметки: [[jvm-memory-areas]], [[garbage-collection]], [[reference-types]].
