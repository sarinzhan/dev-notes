---
title: Docker containers, resources, restart policies and logs
tags: [docker, cgroups, healthcheck, restart-policy, logging]
learned: 2026-10-22
---
## TL;DR
A container's lifecycle is created -> running -> (paused) -> exited -> removed, driven by `docker run/stop/start/rm`. Resource limits (`--memory`, `--cpus`, `--pids-limit`) are cgroup settings the kernel enforces; exceeding memory means the OOM killer and exit code 137. A `HEALTHCHECK` tells Docker whether the process inside is actually working, restart policies decide what happens when it exits, and logs are whatever the process writes to stdout/stderr, captured by a logging driver.

## Key points
### Lifecycle and CLI
- `docker create` builds the container from an image (writable layer, config); `docker start` runs it; `docker run` = create + start (+ `-d` detach, `-it` interactive TTY, `--rm` delete on exit, `--name`, `-e KEY=v`, `--env-file`, `-p host:container`, `-v`, `--network`).
- `docker stop` sends `SIGTERM` (or `STOPSIGNAL`), waits `--stop-timeout`/`-t` (10 s), then `SIGKILL`; `docker kill` sends `SIGKILL` immediately; `docker restart`; `docker pause`/`unpause` freeze via the freezer cgroup ([[linux-processes]]).
- `docker ps -a` (status: `Up 2h (healthy)`, `Exited (137) 5m ago`), `docker inspect`, `docker exec -it c sh`, `docker top c`, `docker stats` (live CPU/mem/IO per container), `docker cp`, `docker diff` (changes in the writable layer), `docker rm -f`.
- Exit codes: 0 normal; 1 application error; 137 = 128 + 9 (`SIGKILL`: OOM-killed or `docker kill`); 143 = 128 + 15 (`SIGTERM` handled by default); 139 segfault. `docker inspect -f '{{.State.OOMKilled}}' c` confirms OOM.
- The process in the container is PID 1 in its namespace: it does not get default signal handlers and must reap zombies. `docker run --init` injects `tini` as PID 1.

### Resource limits (cgroups)
| Flag | cgroup setting | Behaviour |
|---|---|---|
| `--memory 1g` (`-m`) | `memory.max` | Hard limit; exceeding it triggers the OOM killer inside the cgroup (exit 137) |
| `--memory-reservation 512m` | `memory.low`/`high` | Soft limit, reclaim pressure under contention only |
| `--memory-swap` | `memory.swap.max` | Total memory+swap; set equal to `--memory` to disable swap for the container |
| `--cpus 1.5` | `cpu.max` (quota/period) | Throttled at 1.5 cores worth of time per 100 ms period; shows as CPU throttling, not an error |
| `--cpu-shares 512` | `cpu.weight` | Relative weight only when CPUs are contended |
| `--cpuset-cpus 0-3` | `cpuset.cpus` | Pin to specific cores |
| `--pids-limit 512` | `pids.max` | Prevents fork bombs; thread-heavy JVMs need headroom |
| `--ulimit nofile=65536:65536` | rlimit | Open file descriptors |
| `--device-read-bps`, `--blkio-weight` | `io.max`, `io.weight` | Disk throughput limits |
- The JVM (JDK 10+, cgroup v2 since 11.0.16/15) reads these limits for `availableProcessors()` and the default max heap (25% of the limit; set `-XX:MaxRAMPercentage=75`). The heap is not the whole footprint: metaspace, thread stacks, direct buffers, code cache and the JIT need 20–40% on top, so size `--memory` accordingly ([[jvm-memory-areas]], [[garbage-collection]]).
- CPU quota affects GC thread counts, `ForkJoinPool.commonPool()` parallelism and default thread pools ([[fork-join-framework]], [[executors-and-thread-pools]]). `--cpus 0.5` gives `availableProcessors() == 1`.

### Health checks
- Without `HEALTHCHECK` Docker assumes the container is ready as soon as it started, and only marks it as failed when the process exits with a non-zero code. A hung JVM looks fine.
- With `HEALTHCHECK` the status column shows `(starting)`, `(healthy)` or `(unhealthy)`. Dockerfile: `HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 CMD curl -f http://localhost:8080/actuator/health || exit 1`.
- Docker only looks at the exit code of the command: 0 = success -> `healthy`; non-zero -> `FailingStreak` increments. Once `FailingStreak` exceeds `--retries`, status becomes `unhealthy`. A single success resets the streak.
- Checks run from container start at the given interval, but failures during `--start-period` do not count; until the streak passes the limit the status stays `starting`. `docker inspect -f '{{json .State.Health}}' c` shows the streak and the last outputs.
- Docker itself does not restart unhealthy containers; Swarm replaces unhealthy tasks ([[docker-swarm]]), Compose `depends_on: condition: service_healthy` waits for them, and `autoheal`-style sidecars restart them.

### Restart policies
- `--restart no` (default), `on-failure[:max-retries]` (non-zero exit only), `always` (also after daemon restart, unless manually stopped), `unless-stopped` (like `always` but stays down if you stopped it before the daemon restarted).
- Restarts use exponential backoff (100 ms doubling, capped at 1 min); `docker inspect -f '{{.RestartCount}}'`. A crash loop shows as `Restarting (1) 3 seconds ago`.
- For services use `unless-stopped`; for one-off jobs `no` or `on-failure:3`.

### Logs
- The container's stdout/stderr are captured by a **logging driver**: `json-file` (default; `docker logs` works), `local` (compressed, rotated by default, preferred), `journald`, `syslog`, `fluentd`, `gelf`, `awslogs`. `docker logs -f --tail 200 --since 10m c`.
- With `json-file` set rotation or the disk fills: `--log-opt max-size=50m --log-opt max-file=5` or daemon-wide in `/etc/docker/daemon.json`.
- Files written inside the container (`/app/logs/app.log`) are invisible to `docker logs` and die with the container; log to stdout in JSON and ship with Promtail/Fluent Bit ([[logging-stacks]]).
- `docker events` streams lifecycle events (die, oom, health_status) for alerting.

## Example
```bash
# Production-style run of a Spring Boot service on a single host
docker run -d --name esim-api \
  --restart unless-stopped \
  --init \
  --memory 1536m --memory-swap 1536m --cpus 2 --pids-limit 1024 \
  --ulimit nofile=65536:65536 \
  --read-only --tmpfs /tmp:rw,size=256m \
  --env-file /etc/esim/env \
  -e JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=70 -XX:+ExitOnOutOfMemoryError" \
  -p 127.0.0.1:8080:8080 \
  --health-cmd 'curl -fsS http://localhost:8080/actuator/health || exit 1' \
  --health-interval 15s --health-timeout 5s --health-start-period 40s --health-retries 3 \
  --log-driver local --log-opt max-size=50m --log-opt max-file=5 \
  registry.example.com/esim/api:1.4.2

# Diagnose a restart loop
docker ps -a --filter name=esim-api --format '{{.Status}}'          # Restarting (137) 2 seconds ago
docker inspect -f 'oom={{.State.OOMKilled}} exit={{.State.ExitCode}} restarts={{.RestartCount}}' esim-api
docker logs --tail 100 esim-api 2>&1 | grep -iE 'OutOfMemory|Killed|Exception'
docker stats --no-stream esim-api                                   # MEM USAGE / LIMIT
```

## Pitfalls
- Exit 137 with `OOMKilled=true` and no `OutOfMemoryError` in the logs: the *container* ran out of memory, not the heap. Lower `MaxRAMPercentage` or raise `--memory`; `-XX:+ExitOnOutOfMemoryError` handles only heap OOM.
- CPU throttling under `--cpus` shows as latency spikes with low average CPU; check `nr_throttled` in `/sys/fs/cgroup/.../cpu.stat` and GC logs.
- `docker stop` taking the full 10 s and then killing: the JVM is not PID 1 (shell-form entrypoint) or graceful shutdown exceeds the timeout; align `-t` with `spring.lifecycle.timeout-per-shutdown-phase`.
- A health check hitting an endpoint that depends on the database makes every DB blip flip the service to unhealthy; use liveness-style checks (`/actuator/health/liveness`) for restarts and readiness for routing.
- `docker logs` growing to tens of GB under `json-file` without rotation ([[linux-memory-filesystems-io]]).
- Related: [[docker-fundamentals]], [[dockerfile-and-images]], [[linux-architecture-and-kernel]], [[hikaricp-pool-sizing]] (pool size vs CPU quota).
