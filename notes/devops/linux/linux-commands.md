---
title: Linux commands and shell scripting
tags: [linux, shell, bash, cli, scripting]
learned: 2026-10-22
---
## TL;DR
The commands a backend developer actually runs on a server: `find` and `grep` for locating things, `ss`/`netstat` and `lsof` for network and file handles, `ps`/`top`/`kill` for processes, `tail -f`/`journalctl` for logs, plus enough bash (variables, conditions, loops, functions, redirects, pipes), `awk`/`sed`, `cron` and systemd timers to automate the boring parts.

## Key points
### find — search files and directories
- Syntax: `find [path] [conditions] [actions]`. Conditions are ANDed; `-o` for OR; `-not`/`!` negates.
- Common conditions: `-name '*.log'` (`-iname` case-insensitive), `-type f|d|l`, `-size +100M`, `-mtime -1` (modified in the last day), `-mmin +30`, `-user esim`, `-perm -o+w`, `-newer file`, `-maxdepth 2`.
- Actions: `-print` (default), `-delete`, `-exec cmd {} \;` (one per file), `-exec cmd {} +` (batched), `-ls`.
- `find /var/log -name '*.gz' -mtime +14 -delete`; `find . -type f -name '*.java' -exec grep -l 'TODO' {} +`.

### grep — search text by pattern
- Syntax: `grep [options] pattern [file...]`. Reads stdin when no file is given, so it sits at the end of pipes.
- `-r`/`-R` recursive, `-n` line numbers, `-i` ignore case, `-l` only file names, `-c` count, `-v` invert, `-w` whole word, `-E` extended regex (`grep -E 'ERROR|WARN'`), `-F` fixed string (fast, no regex), `-A 3 -B 3 -C 3` context lines, `--include='*.yml'`.
- `grep -rn --include='*.java' 'KafkaTemplate' src/`; `journalctl -u app | grep -E 'ERROR|Exception' | tail -50`. `rg` (ripgrep) is the faster modern alternative.

### netstat / ss — network statistics
- `netstat` shows connections, listening ports, routing table, interface stats; it is deprecated (package `net-tools`) and replaced by `ss` and `ip` ([[linux-networking]]).
- `netstat -tulpn` = `ss -tulpn`: TCP/UDP listening sockets with PIDs. `ss -tan state established | wc -l` counts open connections; `ss -tanp '( dport = :5432 )'` filters by destination.
- `lsof -i :8080` shows which process holds a port; `lsof -p <pid>` lists all files/sockets of a process; `lsof +L1` finds deleted-but-open files ([[linux-memory-filesystems-io]]).

### Processes, logs, system
- `ps aux --sort=-%mem | head`, `pgrep -f app.jar`, `pkill -f`, `kill -15`, `nohup cmd &`, `jobs`, `fg`, `disown` ([[linux-processes]]).
- `tail -f -n 200 app.log`, `less +F`, `journalctl -u app -f --since today`, `dmesg -T | tail` (kernel messages, OOM kills).
- `df -h`, `du -sh *`, `free -h`, `uptime`, `vmstat 1`, `iostat -x 1`, `top -H -p <pid>` (threads of the JVM; convert the thread id to hex to match `jstack` output).
- `curl -sS -o /dev/null -w '%{http_code} %{time_total}\n' http://localhost:8080/actuator/health`, `jq` for JSON.
- `tar -czf backup.tgz dir/`, `tar -xzf`, `rsync -avz --delete src/ host:/dst/`, `scp`, `ssh -L 5432:db:5432 bastion` (port forward).
- `watch -n 2 'ss -s'`, `xargs -P 4`, `tee`, `sort | uniq -c | sort -rn` (the histogram idiom), `cut -d: -f1`, `tr`, `wc -l`.

### Bash scripting
- Header: `#!/usr/bin/env bash` then `set -euo pipefail` (exit on error, on unset variable, on failure inside a pipe).
- Variables: `name=value` (no spaces), `"$name"` always quoted, `${name:-default}`, `$(cmd)` for command substitution, `$?` last exit code, `$#`/`$@` arguments.
- Conditions: `if [[ -f "$f" && "$env" == "prod" ]]; then ...; elif ...; else ...; fi`; `[[ ]]` is safer than `[ ]`; numeric compare with `-eq -lt` or `(( a < b ))`.
- Loops: `for f in *.log; do ...; done`, `for i in {1..5}`, `while read -r line; do ...; done < file`, `until`.
- Functions: `deploy() { local env="$1"; ...; return 0; }`; `local` keeps variables scoped.
- Redirects and pipes: `>` overwrite, `>>` append, `2>&1` stderr into stdout, `&>` both, `< file`, `<<EOF` heredoc, `|` pipe, `|&` pipe both, `cmd1 && cmd2 || cmd3`.
- `trap 'cleanup' EXIT` for temp-file cleanup; `shellcheck script.sh` catches most mistakes.

### awk and sed — mini languages for text
- `awk '{print $1, $NF}'` prints the first and last field; `awk -F: '$3 >= 1000 {print $1}' /etc/passwd`; `awk '/ERROR/ {n++} END {print n}'`; sums: `awk '{s+=$5} END {print s/1024/1024 " MB"}'`.
- `sed 's/old/new/g' file` prints with substitution, `-i` edits in place, `-n '10,20p'` prints a range, `/pattern/d` deletes matching lines, `-E` extended regex. `sed -i 's/^server.port=.*/server.port=8081/' application.properties`.

### Scheduling
- `crontab -e`; format `min hour dom month dow command`; `0 3 * * * /opt/scripts/backup.sh >> /var/log/backup.log 2>&1`. Cron has a minimal `PATH` and no shell profile; use absolute paths. `/etc/cron.d/` for system jobs.
- systemd timers (`OnCalendar=Mon..Fri 03:00`, `Persistent=true` to run a missed job after boot) log to the journal and can depend on other units ([[linux-processes]]).

## Example
```bash
#!/usr/bin/env bash
# Rotate and ship app logs older than 7 days, then report top error classes of today.
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

## Pitfalls
- Unquoted variables split on spaces and glob; `rm -rf "$dir"/*` with an empty `dir` wipes `/`.
- `grep` on a huge binary-ish log prints "Binary file matches"; use `-a` or `grep --text`.
- `find -exec ... \;` forks once per file; prefer `+` or `xargs -0` with `-print0` for file names with spaces.
- `pipefail` is off by default, so `cmd | tee log` hides `cmd`'s failure.
- Cron jobs that work interactively fail in cron because `PATH` and env are different; source the env file explicitly.
- Related: [[linux-basics]], [[git-commands]], [[docker-containers-and-resources]].
