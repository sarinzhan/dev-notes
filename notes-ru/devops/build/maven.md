---
title: Жизненный цикл сборки Maven и управление зависимостями
tags: [maven, build, dependencies, java]
learned: 2026-10-22
---
## Кратко
Apache Maven управляет Java-проектом, его зависимостями и сборкой. Он даёт автоматизированную сборку, декларативное управление зависимостями с транзитивным разрешением, стандартную структуру проекта и воспроизводимые сборки, управляемые через `pom.xml`. Сборка — это **жизненный цикл (lifecycle)** из упорядоченных **фаз (phases)** (`validate` -> `compile` -> `test` -> `package` -> `verify` -> `install` -> `deploy`); запуск фазы запускает все предыдущие фазы, а плагины привязывают к фазам **цели (goals)**. Основная альтернатива — Gradle: скрипты сборки на Groovy/Kotlin, инкрементальные сборки и демон; выбор по умолчанию для Android и многих более новых проектов.

## Основное
### Для чего нужен Maven
- Автоматизация сборки: одна команда компилирует, тестирует, упаковывает и публикует.
- Управление зависимостями: объявляешь `groupId:artifactId:version`, Maven скачивает артефакт и его транзитивные зависимости из репозиториев в локальный кэш `~/.m2/repository`.
- Стандартизированная структура кода: `src/main/java`, `src/main/resources`, `src/test/java`, результат — в `target/`. Любой Maven-проект выглядит одинаково.
- Повторяемые, воспроизводимые сборки: тот же pom и те же версии дают тот же артефакт на любой машине или CI-раннере ([[docker-socket-dind-dood]], [[dockerfile-and-images]]).

### Жизненный цикл, фазы, цели
| Фаза (жизненный цикл по умолчанию) | Что происходит |
|---|---|
| `validate` | Проверить, что проект корректен и вся информация доступна |
| `compile` | Скомпилировать `src/main/java` в `target/classes` (`maven-compiler-plugin:compile`) |
| `test-compile` | Скомпилировать тесты |
| `test` | Запустить модульные тесты через Surefire (`*Test`, `Test*`, `*Tests`); падения останавливают сборку |
| `package` | Собрать и упаковать проект в JAR, WAR или другой формат артефакта в `target/` (`maven-jar-plugin`; цель `repackage` в Spring Boot делает толстый исполняемый jar) |
| `verify` | Запустить интеграционные тесты (Failsafe, `*IT`) и проверки качества |
| `install` | Установить собранный артефакт в локальный репозиторий Maven (`~/.m2`), чтобы от него могли зависеть другие проекты на этой машине |
| `deploy` | Загрузить артефакт в удалённый репозиторий (Nexus, Artifactory, GitLab Package Registry) |
- `clean` — отдельный жизненный цикл, удаляющий `target/`. `mvn clean install` = очистить проект, скомпилировать, протестировать, упаковать и установить в локальный репозиторий; обычная команда «полной сборки». `mvn clean verify` — команда получше для CI: делает всё, но не засоряет `~/.m2`.
- Цели можно вызывать напрямую: `mvn dependency:tree`, `mvn spring-boot:run`, `mvn versions:display-dependency-updates`, `mvn help:effective-pom`.
- Полезные флаги: `-DskipTests` (тесты компилируются, но не запускаются), `-Dmaven.test.skip=true` (пропустить и компиляцию), `-pl module -am` (один модуль и то, что ему нужно), `-T 4` (параллельная сборка модулей), `-o` офлайн, `-U` принудительно обновить snapshot, `-B` пакетный режим для CI, `-q` тихий режим, `-X` отладка, `-P prod` активировать профиль.

### Зависимости
- Координаты `groupId:artifactId:version[:packaging[:classifier]]`. **Области видимости (scopes)**: `compile` (по умолчанию, везде, транзитивно), `provided` (только для компиляции; его предоставляет контейнер, например Lombok, servlet API), `runtime` (не нужен для компиляции: JDBC-драйверы, Logback), `test` (JUnit, Testcontainers), `import` (BOM в `dependencyManagement`), `system` (избегать).
- **Транзитивное разрешение** с правилом **«побеждает ближайший» (nearest-wins)**: когда два пути приносят разные версии одного артефакта, побеждает тот, что ближе к корню дерева; при равной глубине — объявленный первым. `mvn dependency:tree -Dverbose` показывает, что было исключено и почему; `<exclusions>` убирает нежелательную транзитивную зависимость.
- `<dependencyManagement>` фиксирует версии, не добавляя зависимостей; дочерние модули и импорты BOM (`spring-boot-dependencies`, `testcontainers-bom`) их наследуют. Родительский pom Spring Boot — это один большой `dependencyManagement` плюс конфигурация плагинов ([[spring-boot-starters-and-autoconfiguration]]).
- Версии: `1.4.2` — релиз (неизменяем в репозиториях), `1.5.0-SNAPSHOT` — изменяющаяся сборка разработки, перекачивается ежедневно. `${project.version}` — для соседних модулей, `<properties>` — для общих версий.
- Репозитории: по умолчанию Maven Central; `<repositories>` или `<mirror>` в `settings.xml`, указывающий на корпоративный прокси Nexus/Artifactory; учётные данные — в `<servers>` файла `~/.m2/settings.xml`, никогда не в pom.

### Многомодульные проекты
- Родительский pom с `<packaging>pom</packaging>` и `<modules>`; дочерние модули объявляют `<parent>`. Reactor собирает модули в порядке зависимостей. Общие версии плагинов и зависимостей живут в родителе.

### Распространённые плагины
- `maven-compiler-plugin` (`<maven.compiler.release>21</maven.compiler.release>`), `maven-surefire-plugin` (модульные тесты), `maven-failsafe-plugin` (интеграционные тесты на `integration-test`/`verify`), `spring-boot-maven-plugin` (`repackage`, `run`, `build-image` через Buildpacks), `maven-enforcer-plugin` (запрет дублирующихся или конфликтующих версий, требование версии Java), `jacoco-maven-plugin` (покрытие), `maven-shade-plugin` (uber-jar вне Spring Boot), `flatten-maven-plugin` (CI-дружественные версии `${revision}`), `versions-maven-plugin`.
- Maven Wrapper (`./mvnw`, `.mvn/wrapper/maven-wrapper.properties`) фиксирует версию Maven для проекта; коммить его, чтобы CI и каждый разработчик использовали один и тот же Maven.

### Gradle в одном абзаце
- Скрипты сборки на Kotlin или Groovy DSL (`build.gradle.kts`), задачи вместо фаз, с DAG и инкрементальным кэшируемым выполнением; демон держит JVM прогретой, поэтому повторные сборки намного быстрее, чем у Maven. Те же репозитории и координаты; конфигурации `implementation`, `api`, `runtimeOnly`, `testImplementation` примерно соответствуют областям видимости Maven. Цена: больше гибкости — больше способов написать нечитаемую сборку; жёсткость Maven — это преимущество для команд.

## Пример
```xml
<!-- фрагмент pom.xml: родитель Boot, зафиксированные BOM, модульные и интеграционные тесты, Java 21 -->
<project>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.3.4</version>
  </parent>
  <groupId>kg.beeline.esim</groupId>
  <artifactId>esim-api</artifactId>
  <version>1.4.2</version>

  <properties>
    <java.version>21</java.version>
    <testcontainers.version>1.20.2</testcontainers.version>
  </properties>

  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>org.testcontainers</groupId>
        <artifactId>testcontainers-bom</artifactId>
        <version>${testcontainers.version}</version>
        <type>pom</type>
        <scope>import</scope>
      </dependency>
    </dependencies>
  </dependencyManagement>

  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>org.postgresql</groupId>
      <artifactId>postgresql</artifactId>
      <scope>runtime</scope>
    </dependency>
    <dependency>
      <groupId>org.testcontainers</groupId>
      <artifactId>postgresql</artifactId>
      <scope>test</scope>
    </dependency>
  </dependencies>

  <build>
    <plugins>
      <plugin>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-maven-plugin</artifactId>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-failsafe-plugin</artifactId>   <!-- *IT.java запускаются на verify -->
        <executions>
          <execution><goals><goal>integration-test</goal><goal>verify</goal></goals></execution>
        </executions>
      </plugin>
    </plugins>
  </build>
</project>
```
```bash
./mvnw -B clean verify                      # CI: модульные тесты (surefire) + интеграционные тесты (failsafe)
./mvnw -B -DskipTests package               # только jar
./mvnw dependency:tree -Dincludes=com.fasterxml.jackson.core   # кто приносит какой Jackson
```

## Подводные камни
- `-Dmaven.test.skip=true` пропускает компиляцию тестов, поэтому сломанный тестовый класс замечают только позже; локально предпочитай `-DskipTests`, а в CI никогда не пропускай тесты.
- Правило nearest-wins незаметно понижает версию транзитивной библиотеки (классический `NoSuchMethodError` во время выполнения); зафиксируй её в `dependencyManagement` и используй правило `dependencyConvergence` плагина enforcer.
- SNAPSHOT-зависимости в релизной сборке делают её невоспроизводимой; release-плагин или правило enforcer должны их запрещать.
- `mvn install` одной и той же версии с разных машин даёт разные jar в `~/.m2`; используй `clean verify` в CI и публикуй только из пайплайна.
- Если забыть про wrapper и собирать тем Maven, что оказался в CI-образе, получишь сюрпризы с версиями плагинов.
- Связанные заметки: [[dockerfile-and-images]] (кэширование `~/.m2` при сборке), [[git-commands]], [[testing-types-and-dev-cycles]].
