---
title: Dockerfile, multi-stage builds, layers and cache
tags: [docker, dockerfile, build-cache, multi-stage]
learned: 2026-10-22
---
## TL;DR
A Dockerfile is a script of instructions that `docker build` executes step by step, turning each one into an image layer. The build cache reuses a layer when the instruction and its inputs have not changed, so **instruction order decides build speed**: put rarely changing things (base image, dependencies) first and the jar last. **Multi-stage builds** compile in one stage and copy only the artifact into a small runtime image.

## Key points
### Instructions
| Instruction | What it does |
|---|---|
| `FROM image[:tag] [AS name]` | Base image; first instruction (after optional `ARG`). `FROM scratch` for an empty base |
| `RUN cmd` | Executes a command at build time and saves the result as a new layer (`apt-get install`, `mvn package`) |
| `COPY src dst` / `ADD` | Copy files from the **build context** (the directory passed to `docker build`, usually `.`) into the image. `ADD` also unpacks local tar archives and fetches URLs; prefer `COPY` unless you need that |
| `WORKDIR /app` | Sets the working directory for the following `RUN`, `CMD`, `ENTRYPOINT`, `COPY`; creates it if missing |
| `ENV KEY=value` | Environment variable at build time *and* in the running container |
| `ARG NAME=default` | Build-time variable only (`--build-arg`); not present at runtime |
| `EXPOSE 8080` | Documents that the container listens on a port. **Does not publish it**; `-p 8080:8080` or Compose/Swarm does. Other tools read it as metadata |
| `CMD ["java","-jar","app.jar"]` | Default command, fully replaceable by `docker run image <other cmd>` |
| `ENTRYPOINT ["java","-jar","app.jar"]` | The executable; `CMD` or run arguments are appended as its arguments. Use exec form (JSON array) so the process is PID 1 and receives signals ([[linux-processes]]) |
| `VOLUME /data` | Declares a mount point; an anonymous volume is created if nothing is mounted there ([[docker-volumes-and-registry]]) |
| `USER app` | Run following instructions and the container as this user, not root |
| `HEALTHCHECK CMD ...` | Command Docker runs periodically to report `healthy`/`unhealthy` ([[docker-containers-and-resources]]) |
| `LABEL`, `SHELL`, `STOPSIGNAL`, `ONBUILD` | Metadata, shell for `RUN`, signal for `docker stop`, triggers for child images |

### Layers and caching
- Every instruction produces a layer (`RUN`, `COPY`, `ADD` add filesystem content; `ENV`, `WORKDIR`, `CMD` add only metadata). Docker keeps layers in a local cache.
- On rebuild Docker walks the instructions in order and checks whether the instruction *and* its inputs changed: for `RUN` the command string; for `COPY`/`ADD` the checksum of the copied files. If unchanged, the cached layer is reused. **The first changed layer invalidates every layer after it**, because each layer is built on top of the previous one.
- Therefore order instructions from least to most volatile: base image, system packages, dependency manifest (`pom.xml`) and dependency download, then source code, then the build.
- `RUN apt-get update && apt-get install -y x && rm -rf /var/lib/apt/lists/*` must be one instruction: a separate `rm` in a later layer does not shrink the earlier layer, and a cached `apt-get update` alone serves stale package lists.
- `.dockerignore` keeps `target/`, `.git`, IDE files out of the context: smaller uploads and fewer spurious cache misses.
- BuildKit (default since Docker 23) adds `RUN --mount=type=cache,target=/root/.m2` for persistent dependency caches across builds without baking them into layers, `--mount=type=secret` for credentials, and parallel stage execution.

### Multi-stage builds
- Several `FROM` stages in one Dockerfile. Build in a stage with the full JDK and Maven (`mvn package`), then in a fresh runtime stage `COPY --from=build` only the artifacts. The final image contains no compiler, no sources, no `.m2`.
- Benefits: small image (JRE instead of JDK, 200 MB instead of 700 MB), smaller attack surface, reproducible builds that do not depend on the CI runner's toolchain. `--target build` builds only one stage (for running tests in CI).
- Spring Boot layered jars: `java -Djarmode=tools -jar app.jar extract --layers --destination extracted` (Boot 3.3+; `-Djarmode=layertools` before) splits the fat jar into `dependencies`, `spring-boot-loader`, `snapshot-dependencies`, `application`, so a code change rebuilds only the last small layer ([[spring-boot-starters-and-autoconfiguration]]).

### Good practice checklist
- Pin base image tags (`eclipse-temurin:21.0.4_7-jre-jammy`) or digests; `latest` is not reproducible.
- Run as a non-root `USER`; a read-only root filesystem (`--read-only`) with `tmpfs` for `/tmp` where possible.
- One process per container; logs to stdout/stderr ([[logging-stacks]]).
- Use exec-form `ENTRYPOINT` and let the JVM read container limits (`-XX:MaxRAMPercentage=75.0`, default on JDK 10+).
- Scan images (`docker scout cves`, Trivy) and rebuild regularly to pick up base image fixes.

## Example
```dockerfile
# syntax=docker/dockerfile:1.7
# ---- build stage: JDK + Maven, cached dependency download ----
FROM maven:3.9.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
RUN --mount=type=cache,target=/root/.m2 mvn -q -B dependency:go-offline     # cached until pom.xml changes
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -q -B -DskipTests package \
 && java -Djarmode=tools -jar target/*.jar extract --layers --destination /layers

# ---- runtime stage: JRE only, non-root, layered jar ----
FROM eclipse-temurin:21-jre-jammy
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build /layers/dependencies/ ./
COPY --from=build /layers/spring-boot-loader/ ./
COPY --from=build /layers/snapshot-dependencies/ ./
COPY --from=build /layers/application/ ./                 # only this layer changes per release
USER app
EXPOSE 8080
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75.0 -XX:+ExitOnOutOfMemoryError"
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://localhost:8080/actuator/health || exit 1
ENTRYPOINT ["java", "org.springframework.boot.loader.launch.JarLauncher"]
```
```bash
docker build -t registry.example.com/esim/api:1.4.2 .
docker build --target build -t esim-api-build .          # stop after the build stage (run tests here)
```

## Pitfalls
- `COPY . .` as the first copy step invalidates the dependency cache on every source change; copy `pom.xml` first.
- Shell-form `ENTRYPOINT java -jar app.jar` wraps the JVM in `/bin/sh -c`; `SIGTERM` hits the shell, not Java, and `docker stop` ends in `SIGKILL` after the timeout.
- Secrets passed via `ARG` or `ENV` end up in the image history (`docker history`); use BuildKit `--mount=type=secret` or runtime secrets ([[docker-swarm]]).
- `EXPOSE` without `-p` and people wondering why the port is unreachable.
- `curl` is not present in distroless or some slim images; the `HEALTHCHECK` silently fails. Use a tiny Java or shell probe, or install curl in the final stage.
- Related: [[docker-fundamentals]], [[maven]], [[docker-containers-and-resources]].
