---
title: Контейнеры Docker, ресурсы, политики перезапуска и логи
tags: [docker, cgroups, healthcheck, restart-policy, logging]
learned: 2026-10-22
---
## Кратко
Жизненный цикл контейнера: created -> running -> (paused) -> exited -> removed, им управляют `docker run/stop/start/rm`. Лимиты ресурсов (`--memory`, `--cpus`, `--pids-limit`) — это настройки cgroup, которые соблюдает ядро; превышение памяти означает OOM killer и код выхода 137. `HEALTHCHECK` сообщает Docker, действительно ли процесс внутри работает, политики перезапуска (restart policies) решают, что происходит после его завершения, а логи — это всё, что процесс пишет в stdout/stderr и что перехватывает драйвер логирования.

## Основное
### Жизненный цикл и CLI
- `docker create` создаёт контейнер из образа (записываемый слой, конфигурация); `docker start` запускает его; `docker run` = create + start (+ `-d` в фоне (detach), `-it` интерактивный TTY, `--rm` удалить при завершении, `--name`, `-e KEY=v`, `--env-file`, `-p host:container`, `-v`, `--network`).
- `docker stop` отправляет `SIGTERM` (или `STOPSIGNAL`), ждёт `--stop-timeout`/`-t` (10 с), затем `SIGKILL`; `docker kill` сразу отправляет `SIGKILL`; `docker restart`; `docker pause`/`unpause` замораживают через freezer cgroup ([[linux-processes]]).
- `docker ps -a` (статус: `Up 2h (healthy)`, `Exited (137) 5m ago`), `docker inspect`, `docker exec -it c sh`, `docker top c`, `docker stats` (CPU/память/IO каждого контейнера в реальном времени), `docker cp`, `docker diff` (изменения в записываемом слое), `docker rm -f`.
- Коды выхода: 0 — нормальное завершение; 1 — ошибка приложения; 137 = 128 + 9 (`SIGKILL`: убит OOM killer или `docker kill`); 143 = 128 + 15 (`SIGTERM` обработан по умолчанию); 139 — segfault. `docker inspect -f '{{.State.OOMKilled}}' c` подтверждает OOM.
- Процесс в контейнере — это PID 1 в своём пространстве имён: у него нет обработчиков сигналов по умолчанию, и он обязан собирать зомби-процессы. `docker run --init` подставляет `tini` в роли PID 1.

### Лимиты ресурсов (cgroups)
| Флаг | Настройка cgroup | Поведение |
|---|---|---|
| `--memory 1g` (`-m`) | `memory.max` | Жёсткий лимит; превышение запускает OOM killer внутри cgroup (выход 137) |
| `--memory-reservation 512m` | `memory.low`/`high` | Мягкий лимит, давление на освобождение памяти только при конкуренции |
| `--memory-swap` | `memory.swap.max` | Сумма памяти и swap; если равно `--memory`, swap для контейнера отключён |
| `--cpus 1.5` | `cpu.max` (quota/period) | Троттлинг на уровне 1.5 ядра времени за период 100 мс; проявляется как CPU throttling, а не как ошибка |
| `--cpu-shares 512` | `cpu.weight` | Относительный вес, действует только при конкуренции за CPU |
| `--cpuset-cpus 0-3` | `cpuset.cpus` | Привязка к конкретным ядрам |
| `--pids-limit 512` | `pids.max` | Защищает от fork-бомб; JVM с большим числом потоков нужен запас |
| `--ulimit nofile=65536:65536` | rlimit | Открытые файловые дескрипторы |
| `--device-read-bps`, `--blkio-weight` | `io.max`, `io.weight` | Лимиты пропускной способности диска |
- JVM (JDK 10+, cgroup v2 с 11.0.16/15) читает эти лимиты для `availableProcessors()` и максимальной кучи по умолчанию (25% лимита; задай `-XX:MaxRAMPercentage=75`). Куча — это не весь объём: metaspace, стеки потоков, direct-буферы, code cache и JIT требуют сверху ещё 20–40%, поэтому `--memory` нужно выбирать с учётом этого ([[jvm-memory-areas]], [[garbage-collection]]).
- Квота CPU влияет на число потоков GC, параллелизм `ForkJoinPool.commonPool()` и пулы потоков по умолчанию ([[fork-join-framework]], [[executors-and-thread-pools]]). `--cpus 0.5` даёт `availableProcessors() == 1`.

### Проверки здоровья (health checks)
- Без `HEALTHCHECK` Docker считает контейнер готовым сразу после запуска и помечает его неисправным, только когда процесс завершается с ненулевым кодом. Зависшая JVM выглядит нормально.
- С `HEALTHCHECK` в колонке статуса видно `(starting)`, `(healthy)` или `(unhealthy)`. Dockerfile: `HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 CMD curl -f http://localhost:8080/actuator/health || exit 1`.
- Docker смотрит только на код выхода команды: 0 = успех -> `healthy`; ненулевой -> увеличивается `FailingStreak`. Как только `FailingStreak` превышает `--retries`, статус становится `unhealthy`. Один успешный результат сбрасывает серию.
- Проверки идут с момента старта контейнера с заданным интервалом, но неудачи в течение `--start-period` не учитываются; пока серия не превысит лимит, статус остаётся `starting`. `docker inspect -f '{{json .State.Health}}' c` показывает серию и последние выводы.
- Сам Docker не перезапускает unhealthy-контейнеры; Swarm заменяет unhealthy-задачи ([[docker-swarm]]), Compose с `depends_on: condition: service_healthy` ждёт их, а sidecar-контейнеры в духе `autoheal` их перезапускают.

### Политики перезапуска
- `--restart no` (по умолчанию), `on-failure[:max-retries]` (только при ненулевом коде выхода), `always` (в том числе после перезапуска демона, если не остановлен вручную), `unless-stopped` (как `always`, но остаётся выключенным, если ты остановил его до перезапуска демона).
- Перезапуски идут с экспоненциальной задержкой (100 мс с удвоением, максимум 1 мин); `docker inspect -f '{{.RestartCount}}'`. Цикл падений выглядит как `Restarting (1) 3 seconds ago`.
- Для сервисов используй `unless-stopped`; для разовых задач — `no` или `on-failure:3`.

### Логи
- stdout/stderr контейнера перехватывает **драйвер логирования (logging driver)**: `json-file` (по умолчанию; работает `docker logs`), `local` (сжатие и ротация по умолчанию, предпочтителен), `journald`, `syslog`, `fluentd`, `gelf`, `awslogs`. `docker logs -f --tail 200 --since 10m c`.
- С `json-file` настрой ротацию, иначе диск заполнится: `--log-opt max-size=50m --log-opt max-file=5` или для всего демона в `/etc/docker/daemon.json`.
- Файлы, записанные внутри контейнера (`/app/logs/app.log`), не видны `docker logs` и умирают вместе с контейнером; логируй в stdout в формате JSON и отправляй через Promtail/Fluent Bit ([[logging-stacks]]).
- `docker events` транслирует события жизненного цикла (die, oom, health_status) для алертинга.

## Пример
```bash
# Запуск Spring Boot сервиса на одном хосте в стиле production
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

# Диагностика цикла перезапусков
docker ps -a --filter name=esim-api --format '{{.Status}}'          # Restarting (137) 2 seconds ago
docker inspect -f 'oom={{.State.OOMKilled}} exit={{.State.ExitCode}} restarts={{.RestartCount}}' esim-api
docker logs --tail 100 esim-api 2>&1 | grep -iE 'OutOfMemory|Killed|Exception'
docker stats --no-stream esim-api                                   # MEM USAGE / LIMIT
```

## Подводные камни
- Выход 137 с `OOMKilled=true` и без `OutOfMemoryError` в логах: память закончилась у *контейнера*, а не у кучи. Уменьши `MaxRAMPercentage` или увеличь `--memory`; `-XX:+ExitOnOutOfMemoryError` обрабатывает только OOM кучи.
- CPU throttling при `--cpus` проявляется как всплески задержки при низкой средней загрузке CPU; смотри `nr_throttled` в `/sys/fs/cgroup/.../cpu.stat` и логи GC.
- `docker stop` ждёт все 10 с, а потом убивает: JVM не является PID 1 (shell-форма entrypoint) или graceful shutdown дольше таймаута; согласуй `-t` с `spring.lifecycle.timeout-per-shutdown-phase`.
- Health check, обращающийся к endpoint, зависящему от базы данных, переводит сервис в unhealthy при каждом сбое БД; для перезапусков используй проверки в стиле liveness (`/actuator/health/liveness`), а для маршрутизации — readiness.
- `docker logs` разрастается до десятков ГБ с `json-file` без ротации ([[linux-memory-filesystems-io]]).
- Связанные заметки: [[docker-fundamentals]], [[dockerfile-and-images]], [[linux-architecture-and-kernel]], [[hikaricp-pool-sizing]] (размер пула и квота CPU).
