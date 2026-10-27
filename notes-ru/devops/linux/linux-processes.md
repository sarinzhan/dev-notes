---
title: Процессы, сигналы и systemd в Linux
tags: [linux, processes, signals, systemd, scheduling]
learned: 2026-10-22
---
## Кратко
Процесс — это запущенная программа со своим адресным пространством, файловыми дескрипторами и PID; потоки — это планируемые единицы, разделяющие память процесса. Новые процессы появляются через `fork()` (клонирование родителя) с последующим `execve()` (замена образа новой программой). Ядро общается с процессами через **сигналы** (`SIGTERM` = пожалуйста, остановись, `SIGKILL` = умри немедленно), а `systemd` — менеджер сервисов, который запускает их, следит за ними и планирует их запуск.

## Основное
### Процессы и потоки
- Процесс: PID, PID родителя, uid/gid, виртуальное адресное пространство, открытые файловые дескрипторы, текущий каталог (cwd), окружение, обработчики сигналов, cgroup и пространства имён ([[linux-architecture-and-kernel]]).
- Поток: в Linux поток — это *задача (task)*, созданная через `clone()`, которая разделяет память, файловые дескрипторы и обработчики сигналов с соседними задачами; ядро планирует задачи, а не процессы. `ps -eLf` или `top -H` показывают их. Платформенные потоки JVM отображаются на эти задачи 1:1 ([[threads-basics]]); виртуальные потоки — нет ([[virtual-threads]]).
- У каждого процесса есть родитель; PID 1 (`systemd` или твой entrypoint внутри контейнера) — предок всех. `pstree -p` показывает дерево.

### fork() и execve()
- `fork()` дублирует вызывающий процесс: тот же код, копия памяти по принципу copy-on-write, те же открытые дескрипторы. Возвращает управление дважды: PID потомка в родителе, 0 в потомке.
- `execve(path, argv, envp)` заменяет текущий образ процесса новой программой, сохраняя PID и (по умолчанию) открытые дескрипторы.
- Оболочка запускает `java -jar app.jar` через `fork()` + `execve("/usr/bin/java")` в потомке + `waitpid()` в родителе. `ProcessBuilder` в Java делает то же самое (через `posix_spawn` или `vfork`).
- Код завершения: процесс заканчивается с кодом 0–255; родитель забирает его через `wait()`. Пока код не забран, потомок — **зомби** (`Z` в `ps`): мёртв, но всё ещё в таблице процессов. Если родитель умирает, сирота переподчиняется PID 1, который обязан его собрать.

### Сигналы
| Сигнал | Номер | Значение | Действие по умолчанию |
|---|---|---|---|
| `SIGHUP` | 1 | Терминал закрыт; по соглашению для демонов — «перечитать конфиг» (`nginx -s reload`) | завершить |
| `SIGINT` | 2 | Прерывание с клавиатуры, `Ctrl+C` | завершить |
| `SIGTERM` | 15 | Вежливый запрос на завершение; по умолчанию для `kill`, `docker stop`, `systemctl stop` | завершить |
| `SIGKILL` | 9 | Немедленное убийство ядром; нельзя перехватить, заблокировать или проигнорировать | завершить |
| `SIGSTOP` / `SIGCONT` | 19 / 18 | Пауза / продолжение; `SIGSTOP` тоже нельзя перехватить | остановить / продолжить |
| `SIGSEGV` | 11 | Недопустимое обращение к памяти | core dump |
| `SIGQUIT` | 3 | `Ctrl+\`; JVM вместо завершения печатает дамп потоков | core dump |
- `kill -15 <pid>` (или просто `kill`) просит; `kill -9` заставляет. Между ними дай приложению время сбросить буферы, закрыть соединения и завершить текущие запросы.
- JVM обрабатывает `SIGTERM`, запуская shutdown hooks; graceful shutdown в Spring Boot (`server.shutdown=graceful`, `spring.lifecycle.timeout-per-shutdown-phase=30s`) перестаёт принимать запросы и ждёт завершения активных ([[spring-boot-properties-and-profiles]]).
- Docker отправляет `SIGTERM`, ждёт `--stop-timeout` (по умолчанию 10 с), затем `SIGKILL`. Если entrypoint — shell-скрипт, который не делает `exec` java-процесса, сигнал так и не доходит до JVM ([[docker-containers-and-resources]]).

### Наблюдение и приоритеты
- `ps aux` (все процессы, стиль BSD), `ps -ef`, `ps -o pid,ppid,stat,%cpu,%mem,cmd -p <pid>`.
- `top` / `htop`: CPU и память в реальном времени; `htop` добавляет вид дерева (`F5`), вид по потокам (`H`), убийство по `F9`.
- Состояния процессов в `ps`: `R` выполняется, `S` спит (ждёт события), `D` непрерываемый сон (обычно дисковый IO; нельзя убить, пока IO не вернётся), `Z` зомби, `T` остановлен.
- Средняя нагрузка (load average, `uptime`) считает готовые к выполнению задачи *и* задачи в состоянии `D`; нагрузка выше числа ядер при низкой загрузке CPU означает ожидание IO, а не нехватку CPU.
- `nice -n 10 cmd` запускает с пониженным приоритетом (диапазон от -20 — наивысший до 19 — наинизший); `renice -n 5 -p <pid>` меняет приоритет запущенного процесса. Планировщик (CFS, EEVDF с ядра 6.6) взвешивает время CPU по значению nice. `ionice` делает то же для дискового IO.

### Юниты systemd
- **Service**-юнит (`/etc/systemd/system/esim.service`) описывает, как запускать демона: `ExecStart`, `User`, `Restart=on-failure`, `Environment`, лимиты ресурсов (`MemoryMax`, `CPUQuota` — снова cgroups).
- **Timer**-юнит заменяет записи cron: `OnCalendar=*-*-* 03:00:00` запускает соответствующий `.service`; `systemctl list-timers` показывает расписание, логи идут в journal.
- `systemctl daemon-reload` после редактирования юнита; `systemctl enable --now esim`; `journalctl -u esim -f` для логов; `systemctl status esim` показывает дерево cgroup и последние строки лога.
- Также есть targets (`multi-user.target`), sockets (активация по сокету), mounts.

## Пример
```ini
# /etc/systemd/system/esim-api.service
[Unit]
Description=eSIM provisioning API
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
User=esim
WorkingDirectory=/opt/esim
EnvironmentFile=/etc/esim/env
ExecStart=/usr/bin/java -XX:MaxRAMPercentage=75 -jar /opt/esim/app.jar
ExecReload=/bin/kill -HUP $MAINPID
KillSignal=SIGTERM
TimeoutStopSec=40            # должно быть больше spring.lifecycle.timeout-per-shutdown-phase
Restart=on-failure
RestartSec=5
MemoryMax=2G
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now esim-api
journalctl -u esim-api -f --since "10 min ago"
```

## Подводные камни
- `kill -9` для JVM пропускает shutdown hooks: несброшенные логи, недописанные файлы, задержка rebalance в Kafka consumer group до истечения session timeout ([[kafka-consumer]]).
- Процесс в состоянии `D` нельзя убить; исправлять нужно зависший IO (NFS, умирающий диск), а не делать ещё один `kill -9`.
- Зомби в контейнере накапливаются, когда entrypoint не является настоящим init; используй `--init` или `tini`.
- `Restart=always` для сервиса, падающего за 100 мс, заставляет systemd сдаться после `StartLimitBurst`; проверь `systemctl reset-failed`.
- Связанные заметки: [[linux-architecture-and-kernel]], [[linux-commands]], [[docker-containers-and-resources]].
