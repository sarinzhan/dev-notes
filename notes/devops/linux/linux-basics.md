---
title: Linux basics for a backend developer
tags: [linux, filesystem, permissions, shell]
learned: 2026-10-22
---
## TL;DR
To operate a Linux box you need four things: know the filesystem hierarchy (where binaries, configs, logs and runtime state live), the core commands for files and processes, how users, groups and permission bits work, and how packages are installed. Everything else in the [[linux-architecture-and-kernel|architecture]] and [[linux-processes|process]] notes builds on this.

## Key points
### Filesystem hierarchy (FHS)
| Path | What lives there |
|---|---|
| `/bin`, `/sbin` | Essential user and system binaries (`ls`, `mount`); on modern distros symlinks to `/usr/bin`, `/usr/sbin` |
| `/usr` | Installed software: `/usr/bin`, `/usr/lib`, `/usr/local` for things installed by hand |
| `/etc` | System-wide configuration: `/etc/passwd`, `/etc/nginx/`, `/etc/systemd/` |
| `/var` | Variable data: logs (`/var/log`), spools, caches, Docker data (`/var/lib/docker`), Postgres data (`/var/lib/postgresql`) |
| `/proc` | Pseudo-filesystem exposing kernel and process state (`/proc/<pid>/`, `/proc/meminfo`) |
| `/sys` | Pseudo-filesystem for devices, drivers and kernel parameters (sysfs) |
| `/dev` | Device files (`/dev/sda`, `/dev/null`, `/dev/shm`) |
| `/home`, `/root` | User home directories; root's home |
| `/tmp`, `/run` | Temporary files; runtime state (pid files, sockets such as `/run/docker.sock`) |
| `/opt` | Self-contained third-party packages |

### Core commands
- Files: `ls -la`, `cd`, `cp -r`, `mv`, `rm -rf`, `cat`, `less`, `head`/`tail -f`, `find`, `grep -rn` ([[linux-commands]]).
- Ownership and mode: `chmod`, `chown user:group`, `umask`.
- Processes: `ps aux`, `top`/`htop`, `kill -15 <pid>`, `systemctl status|start|stop|enable <unit>` ([[linux-processes]]).
- Shell plumbing: `|`, `>`, `>>`, `2>&1`, `$(...)`, `&&`, `xargs`.

### Users, groups, permissions
- Every file has an owner, a group and nine permission bits: `rwx` for owner, group, others. `r=4 w=2 x=1`, so `chmod 640` = owner read/write, group read, others nothing.
- For a directory `x` means "can enter / traverse", `r` means "can list", `w` means "can create or delete entries".
- Special bits: `setuid` (run as file owner, e.g. `passwd`), `setgid` on a directory (new files inherit the group), sticky bit on `/tmp` (only the owner may delete).
- `sudo` runs a command as root; membership in the `docker` group is equivalent to root ([[docker-socket-dind-dood]]).
- `/etc/passwd` lists users, `/etc/shadow` holds password hashes, `/etc/group` lists groups; `id`, `whoami`, `groups`.

### Package managers
- Debian/Ubuntu: `apt update && apt install -y <pkg>`; `.deb` packages, `dpkg -l`.
- Fedora/RHEL: `dnf install <pkg>`; `.rpm` packages.
- Arch: `pacman -S <pkg>`.
- Alpine (common Docker base): `apk add --no-cache <pkg>` ([[dockerfile-and-images]]).

### Text editors
- `nano`: modal-free, hints at the bottom (`Ctrl+O` write, `Ctrl+X` exit). Fine for one-line config fixes.
- `vim`: modal. `i` insert, `Esc` back to normal, `:wq` write and quit, `:q!` discard, `/pattern` search, `dd` delete line, `u` undo. Worth learning because it is on every server and inside most containers (`vi` at least).

## Example
```bash
# Deploy user that can run the service but not read secrets of other users
sudo useradd --system --home /opt/esim --shell /usr/sbin/nologin esim
sudo mkdir -p /opt/esim /var/log/esim
sudo chown -R esim:esim /opt/esim /var/log/esim
sudo chmod 750 /opt/esim              # group may enter, others may not
sudo chmod 640 /opt/esim/application.yml   # config readable only by owner and group

# Check what we did
ls -la /opt/esim
# -rw-r----- 1 esim esim  812 Oct 22 10:14 application.yml
sudo -u esim java -jar /opt/esim/app.jar   # run as the service user
```

## Pitfalls
- `rm -rf` with an unset variable (`rm -rf $DIR/`) deletes `/`; use `set -u` in scripts and quote variables.
- `chmod 777` to "make it work" is never the fix; find the owning user/group and set 750/640.
- Files deleted while a process holds them open still consume disk space until the process closes them; see [[linux-memory-filesystems-io]] for `ls` vs `du` vs `df`.
- `apt install` inside a Dockerfile without `rm -rf /var/lib/apt/lists/*` bloats the image layer ([[dockerfile-and-images]]).
- Related: [[linux-commands]], [[linux-architecture-and-kernel]].
