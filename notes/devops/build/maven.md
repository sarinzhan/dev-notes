---
title: Maven build lifecycle and dependency management
tags: [maven, build, dependencies, java]
learned: 2026-10-22
---
## TL;DR
Apache Maven manages a Java project, its dependencies and its build. It gives automated builds, declarative dependency management with transitive resolution, a standard project layout and reproducible builds driven by `pom.xml`. A build is a **lifecycle** of ordered **phases** (`validate` -> `compile` -> `test` -> `package` -> `verify` -> `install` -> `deploy`); running a phase runs all phases before it, and plugins bind **goals** to phases. Gradle is the main alternative: Groovy/Kotlin build scripts, incremental builds and a daemon, the default for Android and many newer projects.

## Key points
### What Maven is for
- Build automation: one command compiles, tests, packages and publishes.
- Dependency management: declare `groupId:artifactId:version`, Maven downloads it and its transitive dependencies from repositories into the local cache `~/.m2/repository`.
- Standardised code structure: `src/main/java`, `src/main/resources`, `src/test/java`, output in `target/`. Any Maven project looks the same.
- Repeatable, reproducible builds: the same pom and the same versions give the same artifact on any machine or CI runner ([[docker-socket-dind-dood]], [[dockerfile-and-images]]).

### Lifecycle, phases, goals
| Phase (default lifecycle) | What happens |
|---|---|
| `validate` | Check the project is correct and all information is available |
| `compile` | Compile `src/main/java` into `target/classes` (`maven-compiler-plugin:compile`) |
| `test-compile` | Compile tests |
| `test` | Run unit tests with Surefire (`*Test`, `Test*`, `*Tests`); failures stop the build |
| `package` | Build and package the project into a JAR, WAR or another artifact format in `target/` (`maven-jar-plugin`; Spring Boot's `repackage` goal makes the fat executable jar) |
| `verify` | Run integration tests (Failsafe, `*IT`) and quality checks |
| `install` | Install the built artifact into the local Maven repository (`~/.m2`) so other projects on this machine can depend on it |
| `deploy` | Upload the artifact to a remote repository (Nexus, Artifactory, GitLab Package Registry) |
- `clean` is a separate lifecycle that deletes `target/`. `mvn clean install` = clean the project, compile, test, package and install into the local repository, the usual "full build" command. `mvn clean verify` is the better CI command: it does everything except polluting `~/.m2`.
- Goals can be invoked directly: `mvn dependency:tree`, `mvn spring-boot:run`, `mvn versions:display-dependency-updates`, `mvn help:effective-pom`.
- Useful flags: `-DskipTests` (compile tests, skip running), `-Dmaven.test.skip=true` (skip compiling too), `-pl module -am` (one module and what it needs), `-T 4` (parallel modules), `-o` offline, `-U` force snapshot update, `-B` batch mode for CI, `-q` quiet, `-X` debug, `-P prod` activate a profile.

### Dependencies
- Coordinates `groupId:artifactId:version[:packaging[:classifier]]`. **Scopes**: `compile` (default, everywhere, transitive), `provided` (compile only; the container supplies it, e.g. Lombok, servlet API), `runtime` (not needed to compile: JDBC drivers, Logback), `test` (JUnit, Testcontainers), `import` (BOM in `dependencyManagement`), `system` (avoid).
- **Transitive resolution** with **nearest-wins** mediation: when two paths bring different versions of the same artifact, the one closest to the root of the tree wins; with equal depth, the first declared. `mvn dependency:tree -Dverbose` shows what was omitted and why; `<exclusions>` removes an unwanted transitive dependency.
- `<dependencyManagement>` pins versions without adding dependencies; child modules and BOM imports (`spring-boot-dependencies`, `testcontainers-bom`) inherit them. Spring Boot's parent pom is one big `dependencyManagement` plus plugin config ([[spring-boot-starters-and-autoconfiguration]]).
- Versions: `1.4.2` release (immutable in repositories), `1.5.0-SNAPSHOT` changing development build re-downloaded daily. `${project.version}` for sibling modules, `<properties>` for shared versions.
- Repositories: Maven Central by default; `<repositories>` or a `settings.xml` `<mirror>` pointing at a corporate Nexus/Artifactory proxy; credentials in `~/.m2/settings.xml` `<servers>`, never in the pom.

### Multi-module projects
- A parent pom with `<packaging>pom</packaging>` and `<modules>`; children declare `<parent>`. Reactor builds modules in dependency order. Shared plugin and dependency versions live in the parent.

### Common plugins
- `maven-compiler-plugin` (`<maven.compiler.release>21</maven.compiler.release>`), `maven-surefire-plugin` (unit tests), `maven-failsafe-plugin` (integration tests at `integration-test`/`verify`), `spring-boot-maven-plugin` (`repackage`, `run`, `build-image` via Buildpacks), `maven-enforcer-plugin` (ban duplicate or conflicting versions, require a Java version), `jacoco-maven-plugin` (coverage), `maven-shade-plugin` (uber-jar outside Spring Boot), `flatten-maven-plugin` (CI-friendly `${revision}` versions), `versions-maven-plugin`.
- Maven Wrapper (`./mvnw`, `.mvn/wrapper/maven-wrapper.properties`) pins the Maven version per project; commit it so CI and every developer use the same Maven.

### Gradle in one paragraph
- Build scripts in Kotlin or Groovy DSL (`build.gradle.kts`), tasks instead of phases with a DAG and incremental, cached execution; a daemon keeps the JVM warm, so repeated builds are much faster than Maven's. Same repositories and coordinates; `implementation` vs `api` vs `runtimeOnly` vs `testImplementation` configurations map roughly to Maven scopes. Costs: more flexibility means more ways to write an unreadable build; Maven's rigidity is a feature for teams.

## Example
```xml
<!-- pom.xml excerpt: Boot parent, pinned BOMs, unit vs integration tests, Java 21 -->
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
        <artifactId>maven-failsafe-plugin</artifactId>   <!-- *IT.java run at verify -->
        <executions>
          <execution><goals><goal>integration-test</goal><goal>verify</goal></goals></execution>
        </executions>
      </plugin>
    </plugins>
  </build>
</project>
```
```bash
./mvnw -B clean verify                      # CI: unit tests (surefire) + integration tests (failsafe)
./mvnw -B -DskipTests package               # just the jar
./mvnw dependency:tree -Dincludes=com.fasterxml.jackson.core   # who brings which Jackson
```

## Pitfalls
- `-Dmaven.test.skip=true` skips compiling tests, so a broken test class is only noticed later; prefer `-DskipTests` locally and never skip in CI.
- Nearest-wins silently downgrades a transitive library (classic `NoSuchMethodError` at runtime); pin it in `dependencyManagement` and use the enforcer plugin's `dependencyConvergence` rule.
- SNAPSHOT dependencies in a release build make it unreproducible; the release plugin or an enforcer rule should forbid them.
- `mvn install` of the same version from different machines gives different jars in `~/.m2`; use `clean verify` in CI and publish only from the pipeline.
- Forgetting the wrapper and building with whatever Maven the CI image has leads to plugin-version surprises.
- Related: [[dockerfile-and-images]] (caching `~/.m2` in builds), [[git-commands]], [[testing-types-and-dev-cycles]].
