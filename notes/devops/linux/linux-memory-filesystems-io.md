---
title: Linux memory, filesystems and IO
tags: [linux, virtual-memory, filesystems, page-cache, disk]
learned: 2026-10-22
---
## TL;DR
Every process sees its own **virtual address space** mapped to physical RAM in 4 KiB pages; the kernel uses the rest of RAM as **page cache** for files, which is why `free` shows almost nothing free on a healthy server (the mechanism is in [[memory-management]]). Filesystems (ext4, XFS, btrfs) store data on block devices, pseudo-filesystems (`/proc`, `/sys`) expose kernel state, and the IO scheduler orders disk requests. The classic "disk is full but files are gone" puzzle is answered by knowing what `ls`, `du` and `df` actually measure.

## Key points
### Memory
- Virtual memory, page tables, page faults, the page cache, swap, the OOM killer and copy-on-write are explained in [[memory-management]]. This note keeps the Linux commands: `free -h` (look at `available`), `/proc/<pid>/status` and `/proc/<pid>/maps` for one process, `vm.swappiness` and `vm.overcommit_memory` in `/etc/sysctl.d/`.

### Filesystems
- File systems (ext4, XFS, btrfs, overlay2, tmpfs), inodes, journaling, partitions, `mkfs`, `mount` and `/etc/fstab` are covered in [[storage-management]]. Quick commands: `lsblk -f`, `findmnt`, `blkid`, `df -i` for inodes.

### Pseudo-filesystems
- `/proc`: process and kernel info generated on read: `/proc/<pid>/{status,maps,fd,limits}`, `/proc/meminfo`, `/proc/cpuinfo`, `/proc/sys/` tunables.
- `/sys`: devices, drivers, buses, cgroups (`/sys/fs/cgroup`). Writing to files changes kernel state ([[linux-architecture-and-kernel]]).

### IO scheduling
- IO schedulers (mq-deadline, BFQ, kyber, none) are in [[storage-management]]; `iostat -x 1`, `iotop` and `vmstat 1` (`wa`) show whether the disk is the bottleneck.

### ls vs du vs df
| Tool | Measures | Where it can mislead |
|---|---|---|
| `ls -l` | **Apparent size** stored in the inode (the file length) | Sparse files look huge; a file deleted by name disappears from `ls` even if still open |
| `du` | **Allocated blocks** walked through directory entries (`du -sh *`) | Does not see deleted-but-open files; counts hard links once per invocation; crossing mounts unless `-x` |
| `df` | **Filesystem-level** used/free as reported by the superblock | Includes space held by open deleted files and reserved root blocks (5% on ext4) |
- "`df` says 95%, `du` says 40%": a process still holds a deleted file open (rotated log, deleted Docker layer). Find it with `lsof +L1` or `find /proc/*/fd -ls | grep deleted`, then restart or truncate via `/proc/<pid>/fd/<n>`.
- `du` smaller than `ls` sum: sparse files or compression (btrfs). `du` larger than `ls`: small files each take a full block.

## Example
```bash
# Disk "full" but du cannot find the usage
df -h /var                              # /dev/sda2  98G  93G  0  100% /var
sudo du -xsh /var/* | sort -h | tail -5 # biggest is /var/lib/docker at 31G: does not add up
sudo lsof +L1 | head                    # files with link count 0 = deleted but open
# java  4123 esim 7w REG 8,2 48G 0 /var/log/esim/app.log (deleted)
sudo truncate -s 0 /proc/4123/fd/7      # free the space without restarting the JVM

# Memory: what is really free
free -h
#               total   used   free  shared  buff/cache  available
# Mem:           15Gi  6.1Gi  412Mi  210Mi       9.2Gi       8.9Gi   <- 'available' is the number that matters
cat /proc/$(pgrep -f app.jar)/status | grep -E 'VmRSS|VmSwap|Threads'
```

## Pitfalls
- Deleting a log file that a running process writes to frees nothing; truncate it or use `copytruncate` in logrotate.
- Docker volumes and image layers live under `/var/lib/docker`; `docker system prune` is often the real fix for a full disk ([[docker-volumes-and-registry]]).
- Memory pitfalls (`free` vs `available`, swap on JVM hosts, `vm.max_map_count`, THP) are in [[memory-management]].
- A JVM heap larger than the cgroup memory limit is killed, not swapped, inside a container; set `-XX:MaxRAMPercentage` instead of `-Xmx` ([[garbage-collection]]).
- Related: [[linux-architecture-and-kernel]], [[linux-commands]], [[java-nio]].
