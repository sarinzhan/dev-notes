---
title: Команды Linux и скрипты на shell
tags: [linux, shell, bash, cli, scripting]
learned: 2026-10-22
---
## Кратко
Команды, которые backend-разработчик на самом деле запускает на сервере: `find` и `grep` для поиска, `ss`/`netstat` и `lsof` для сети и файловых дескрипторов, `ps`/`top`/`kill` для процессов, `tail -f`/`journalctl` для логов, плюс достаточно bash (переменные, условия, циклы, функции, перенаправления, конвейеры), `awk`/`sed`, `cron` и таймеры systemd, чтобы автоматизировать скучную часть.

## Основное
### find — поиск файлов и каталогов
- Синтаксис: `find [path] [conditions] [actions]`. Условия объединяются через И; `-o` для ИЛИ; `-not`/`!` отрицает.
- Частые условия: `-name '*.log'` (`-iname` без учёта регистра), `-type f|d|l`, `-size +100M`, `-mtime -1` (изменён за последний день), `-mmin +30`, `-user esim`, `-perm -o+w`, `-newer file`, `-maxdepth 2`.
- Действия: `-print` (по умолчанию), `-delete`, `-exec cmd {} \;` (по одному запуску на файл), `-exec cmd {} +` (пачками), `-ls`.
- `find /var/log -name '*.gz' -mtime +14 -delete`; `find . -type f -name '*.java' -exec grep -l 'TODO' {} +`.

### grep — поиск текста по шаблону
- Синтаксис: `grep [options] pattern [file...]`. Если файл не указан, читает stdin, поэтому стоит в конце конвейеров.
- `-r`/`-R` рекурсивно, `-n` номера строк, `-i` без учёта регистра, `-l` только имена файлов, `-c` количество, `-v` инвертировать, `-w` целое слово, `-E` расширенные регулярные выражения (`grep -E 'ERROR|WARN'`), `-F` фиксированная строка (быстро, без regex), `-A 3 -B 3 -C 3` строки контекста, `--include='*.yml'`.
- `grep -rn --include='*.java' 'KafkaTemplate' src/`; `journalctl -u app | grep -E 'ERROR|Exception' | tail -50`. `rg` (ripgrep) — более быстрая современная альтернатива.

### netstat / ss — сетевая статистика
- `netstat` показывает соединения, слушающие порты, таблицу маршрутизации, статистику интерфейсов; он устарел (пакет `net-tools`) и заменён на `ss` и `ip` ([[linux-networking]]).
- `netstat -tulpn` = `ss -tulpn`: слушающие TCP/UDP-сокеты с PID. `ss -tan state established | wc -l` считает открытые соединения; `ss -tanp '( dport = :5432 )'` фильтрует по порту назначения.
- `lsof -i :8080` показывает, какой процесс держит порт; `lsof -p <pid>` перечисляет все файлы/сокеты процесса; `lsof +L1` находит удалённые, но ещё открытые файлы ([[linux-memory-filesystems-io]]).

### Процессы, логи, система
- `ps aux --sort=-%mem | head`, `pgrep -f app.jar`, `pkill -f`, `kill -15`, `nohup cmd &`, `jobs`, `fg`, `disown` ([[linux-processes]]).
- `tail -f -n 200 app.log`, `less +F`, `journalctl -u app -f --since today`, `dmesg -T | tail` (сообщения ядра, убийства OOM killer).
- `df -h`, `du -sh *`, `free -h`, `uptime`, `vmstat 1`, `iostat -x 1`, `top -H -p <pid>` (потоки JVM; переведи id потока в hex, чтобы сопоставить с выводом `jstack`).
- `curl -sS -o /dev/null -w '%{http_code} %{time_total}\n' http://localhost:8080/actuator/health`, `jq` для JSON.
- `tar -czf backup.tgz dir/`, `tar -xzf`, `rsync -avz --delete src/ host:/dst/`, `scp`, `ssh -L 5432:db:5432 bastion` (проброс порта).
- `watch -n 2 'ss -s'`, `xargs -P 4`, `tee`, `sort | uniq -c | sort -rn` (идиома гистограммы), `cut -d: -f1`, `tr`, `wc -l`.

### Скрипты на bash
- Заголовок: `#!/usr/bin/env bash`, затем `set -euo pipefail` (выход при ошибке, при незаданной переменной, при сбое внутри конвейера).
- Переменные: `name=value` (без пробелов), `"$name"` всегда в кавычках, `${name:-default}`, `$(cmd)` для подстановки команды, `$?` последний код выхода, `$#`/`$@` аргументы.
- Условия: `if [[ -f "$f" && "$env" == "prod" ]]; then ...; elif ...; else ...; fi`; `[[ ]]` безопаснее, чем `[ ]`; числовое сравнение через `-eq -lt` или `(( a < b ))`.
- Циклы: `for f in *.log; do ...; done`, `for i in {1..5}`, `while read -r line; do ...; done < file`, `until`.
- Функции: `deploy() { local env="$1"; ...; return 0; }`; `local` ограничивает область видимости переменных.
- Перенаправления и конвейеры: `>` перезаписать, `>>` дописать, `2>&1` stderr в stdout, `&>` оба, `< file`, `<<EOF` heredoc, `|` конвейер, `|&` конвейер для обоих потоков, `cmd1 && cmd2 || cmd3`.
- `trap 'cleanup' EXIT` для очистки временных файлов; `shellcheck script.sh` ловит большинство ошибок.

### awk и sed — мини-языки для текста
- `awk '{print $1, $NF}'` печатает первое и последнее поле; `awk -F: '$3 >= 1000 {print $1}' /etc/passwd`; `awk '/ERROR/ {n++} END {print n}'`; суммы: `awk '{s+=$5} END {print s/1024/1024 " MB"}'`.
- `sed 's/old/new/g' file` печатает с заменой, `-i` редактирует на месте, `-n '10,20p'` печатает диапазон, `/pattern/d` удаляет совпадающие строки, `-E` расширенные регулярные выражения. `sed -i 's/^server.port=.*/server.port=8081/' application.properties`.

### Планирование
- `crontab -e`; формат `min hour dom month dow command`; `0 3 * * * /opt/scripts/backup.sh >> /var/log/backup.log 2>&1`. У cron минимальный `PATH` и нет профиля оболочки; используй абсолютные пути. `/etc/cron.d/` — для системных задач.
- Таймеры systemd (`OnCalendar=Mon..Fri 03:00`, `Persistent=true`, чтобы пропущенная задача запустилась после загрузки) пишут в journal и могут зависеть от других юнитов ([[linux-processes]]).

## Пример
```bash
#!/usr/bin/env bash
# Ротировать и отправить логи приложения старше 7 дней, затем вывести топ классов ошибок за сегодня.
set -euo pipefail

LOG_DIR="${1:-/var/log/esim}"
ARCHIVE="/backup/logs/$(hostname)"
mkdir -p "$ARCHIVE"

find "$LOG_DIR" -type f -name 'app-*.log' -mtime +7 -print0 \
  | xargs -0 -r -I{} sh -c 'gzip -c "$1" > "$2/$(basename "$1").gz" && rm -f "$1"' _ {} "$ARCHIVE"

today="$LOG_DIR/app.log"
if [[ -f "$today" ]]; then
  echo "Top exceptions in $today:"
  grep -oE '[A-Za-z.]+(Exception|Error)' "$today" | sort | uniq -c | sort -rn | head -5
fi

errors=$(grep -c ' ERROR ' "$today" || true)
(( errors > 100 )) && echo "WARN: $errors errors today" >&2
exit 0
```

## Подводные камни
- Переменные без кавычек разбиваются по пробелам и раскрываются как glob; `rm -rf "$dir"/*` с пустым `dir` стирает `/`.
- `grep` по огромному логу с бинарными данными печатает «Binary file matches»; используй `-a` или `grep --text`.
- `find -exec ... \;` запускает процесс на каждый файл; предпочитай `+` или `xargs -0` с `-print0` для имён файлов с пробелами.
- `pipefail` по умолчанию выключен, поэтому `cmd | tee log` скрывает сбой `cmd`.
- Задачи cron, которые работают интерактивно, падают в cron, потому что `PATH` и окружение другие; явно подключай файл с переменными окружения.
- Связанные заметки: [[linux-basics]], [[git-commands]], [[docker-containers-and-resources]].
