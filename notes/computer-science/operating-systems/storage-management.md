---
title: Storage management
tags: [operating-systems, storage, ssd, hdd, file-systems, page-cache, linux]
learned: 2026-10-28
order: 7
---
## TL;DR
RAM is volatile, so anything that must survive a restart goes to **persistent storage**: tape, HDD, SSD, flash. Every drive, whatever is inside, shows the OS the same interface: an array of **logical blocks (LBAs)**. The drive's controller translates each LBA to a **physical** location: a cylinder, head and sector on an HDD, or a NAND page through the **flash translation layer** on an SSD, where writes always go to a fresh page and the drive must garbage-collect and level wear. On top, the OS cuts the disk into **partitions**, formats each with a **file system** (FAT32, ext4, XFS…) that turns files and directories into blocks, and **mounts** it onto a directory. Every read and write passes through the **page cache** in RAM: reads hit memory when they can; writes land in memory and reach the disk later, or when you call `fsync`. Between `read()` and the NAND cell there are many layers (POSIX read → file-system block → page → LBA → PBA), and most storage performance and durability surprises come from not knowing which one you are in.

## Key points
### Persistence: why and on what
- RAM loses everything when the power goes ([[memory-management]]). Data that must survive (database files, logs, Kafka segments, uploaded files) has to be written to a persistent medium.

| Medium | How it stores bits | Random read | Sequential | Typical use |
|---|---|---|---|---|
| Magnetic tape | magnetised tape on a reel, read in order | seconds to minutes (rewind) | 300–400 MB/s | archives, backups (LTO) |
| HDD | magnetised spinning platters | ~5–10 ms | 150–250 MB/s | cheap bulk storage, backups, cold data |
| SATA SSD | NAND flash behind a SATA controller | ~50–100 µs | ~550 MB/s (SATA limit) | older servers, laptops |
| NVMe SSD | NAND flash on PCIe, many parallel queues | ~10–100 µs | 3–14 GB/s | databases, Kafka, anything latency-bound |
| Flash (USB, SD) | NAND with a simple controller | ms | tens to hundreds of MB/s | moving files, cameras |
- Compare with RAM at ~100 ns ([[latency-numbers]]): even the fastest SSD is ~100× slower than memory, an HDD ~100,000×. That gap is why the page cache exists.

### HDD: platters, heads, tracks, sectors
- A hard disk has several **platters** spinning together (5,400–15,000 rpm), one **head** per surface, all heads on one arm moving together.
- Each surface is divided into concentric **tracks**; the same track on every platter is a **cylinder**. Radial lines cut a platter into **geometrical sectors** (slices); a **disk sector** is the piece of one track inside one slice, the smallest unit the drive reads or writes: 512 bytes traditionally, 4 KiB on "Advanced Format" drives. A **cluster** is a group of sectors a file system treats as one unit.
- Cost of a random access = **seek** (move the arm, ~4–9 ms) + **rotational latency** (wait for the sector, ~4 ms at 7,200 rpm). Sequential access avoids both, which is why HDDs are fine for streaming logs and terrible for random database reads.
- **CHS** (cylinder / head / sector): traditionally the OS addressed sectors by geometry, so it had to know the physical layout of the drive.
- **LBA** (logical block addressing): later drives expose the whole disk as an array of blocks 0…N, and the **disk controller translates** LBA → CHS. It costs the drive a little, but the hardware can improve freely: more sectors on outer tracks, bad sectors remapped to spares, all invisible to the OS.

```anim
storage-hdd-lba
```

### SSD: NAND pages, blocks and the FTL
- An SSD stores bits in **NAND flash** cells, grouped into **pages** (4 KiB, 16 KiB…) and **blocks** (64–512 pages). The minimum read/write unit is a page; a page can only be programmed when empty, and erasing works only on a **whole block**.
- The OS still sends LBAs. The **flash translation layer** (FTL), a mapping table in the SSD controller, maps logical blocks to pages ("LBA 1 → page A, 7 → G").
- **Out-of-place writes**: rewriting an LBA writes the new data to a free page, remaps the LBA and marks the old page stale. **Garbage collection** later copies the live pages out of a block and erases it. Those copies are **write amplification**: the drive writes more than the OS asked for.
- **TRIM** (`fstrim`, or the `discard` mount option) tells the SSD which LBAs were deleted, so GC stops copying dead data. Most distributions run `fstrim.timer` weekly.
- **Wear leveling**: NAND cells survive a limited number of program/erase cycles (roughly 100,000 for SLC, 3,000 for TLC, 1,000 for QLC). If some pages are written once and never touched (**cold**) while others change all the time (**hot**), the hot blocks die first and part of the SSD becomes unusable. The controller moves data around so all blocks wear evenly.
- **Over-provisioning** (OP): the drive hides spare capacity (often 7–28%) for GC and wear leveling: cold pages are moved into it, the cold block is erased, and hot pages are moved there to spread the wear. A drive kept nearly full has less free space to do this, so it slows down and wears faster.
- **LBA size ≠ page size**: with 4 KiB LBAs and 8 KiB pages, LBA 1 is page A at offset 0 and LBA 2 is page A at offset 4,096. Rewriting one LBA means a read-modify-write of the whole page, which adds write amplification and causes issues.
- Endurance is rated as **TBW** (terabytes written) or **DWPD** (drive writes per day). `smartctl -a /dev/nvme0` shows the percentage used and total data written.

```anim
storage-ssd-ftl
```

### Blocks everywhere: PBA, LBA, file-system block, page
| Term | Who sees it | Typical size |
|---|---|---|
| **PBA** (physical block address), physical sector | internal to the drive | 512 B or 4 KiB (HDD), NAND page (SSD) |
| **LBA** (logical block address), logical sector | exposed to the OS | 512 B or 4 KiB |
| **File-system block** (cluster) | the minimum the file system reads or writes | 1, 2 or 4 KiB, chosen when formatting |
| **Page** (virtual memory) | the page cache and the MMU | 4 KiB |
- 1 PBA = 1 or more LBAs; 1 FS block = 1 or more LBAs. Examples from the course: 4096 → 512 → 512 (one FS block = 8 LBAs = 8 PBAs), 4096 → 1024 → 2048, 4096 → 1024 → 4096.
- Common drives today are **512n** (512/512), **512e** (512-byte logical on 4 KiB physical, for compatibility) and **4Kn** (4096/4096). Check with `lsblk -o NAME,LOG-SEC,PHY-SEC` or `fdisk -l` ("Sector size (logical/physical)").
- On Linux the FS block size is chosen at `mkfs` time and must not exceed the memory page size (1024, 2048 or 4096 bytes on x86), so that one block fits in one page of the page cache.

### File systems
- A **file system** is an abstraction over raw storage: users want files and directories, not block numbers. It translates reading and writing a file into blocks, keeps metadata (names, sizes, owners, timestamps, which blocks belong to which file), allocates free blocks (at least one FS block per file, which is one or more LBAs), and enables caching.

| FS | Traits | Typical use |
|---|---|---|
| **FAT16/FAT32** | Simple linked-list table, no journal, no permissions, 4 GiB file limit | USB sticks, SD cards, the EFI boot partition |
| **exFAT** | FAT without the size limits | large USB drives, cameras |
| **NTFS** | Windows default: journaling, ACLs, compression | Windows disks |
| **APFS** | Apple default: copy-on-write, snapshots, clones, encryption, optimised for SSD | macOS, iOS |
| **ext4** | Journaling, mature, default on Debian/Ubuntu, fast fsck, 16 TiB files | general-purpose root and data on Linux |
| **XFS** | Journaling, excellent with large files and parallel IO, cannot shrink, default on RHEL | databases, Kafka log dirs, large volumes |
| **btrfs** | Copy-on-write, snapshots, checksums, built-in RAID, compression; higher overhead | snapshot-heavy setups, Fedora default, a Docker storage driver option |
| **ZFS** | Copy-on-write, checksums, snapshots, pools of disks, its own cache (ARC) | NAS and storage servers, backups |
| **overlay2** | Union FS stacking read-only layers plus one writable layer | Docker image layers ([[docker-fundamentals]]) |
| **tmpfs** | In RAM, lost on reboot | `/run`, `/dev/shm`, `/tmp` on some distros |
- **Inodes** hold metadata (owner, mode, timestamps, block pointers or extents); file names are directory entries pointing at inodes. A hard link is a second name for the same inode; a symlink is a file containing a path. Running out of inodes (`df -i`) looks like a full disk with free space.
- **Journaling** (ext4, XFS, NTFS): metadata changes are first written to a journal, then applied. After a crash the journal is replayed instead of scanning the whole disk, so the file system stays consistent. ext4's default `data=ordered` journals metadata only, after writing the data blocks.

### FAT32: the simplest real file system
- The **file allocation table** is an array of 32-bit integers. The index is the LBA (or cluster); the content is the **next** LBA of the same file, until an end-of-chain marker. The directory entry stores the name and the start LBA.
- Reading `test.txt` that starts at LBA 6: FAT[6] = 2, FAT[2] = 3, FAT[3] = end, so the file is LBAs 6, 2 and 3. Reaching block *n* of a file means walking *n* links, and on an HDD a fragmented chain means a seek per jump.
- **32 bits is really 28**: 4 bits are reserved (end of chain, bad, free, other uses). With one 512-byte LBA per entry, 2^28 × 512 B = 128 GiB is all you could address.
- The trick is **clusters**: an entry points to a group of LBAs. With 8 LBAs per cluster (4 KiB), cluster *c* covers LBAs *c* × 8 … *c* × 8 + 7 (cluster 6 = LBAs 48–55, cluster 3 = LBAs 24–31), and 2^28 × 4 KiB = 1 TiB is addressable.
- The larger the cluster, the more disk can be addressed, but every file takes whole clusters: **internal fragmentation**, wasted space nobody else can use ([[memory-management]] has the same trade-off for pages).

```anim
storage-fat-clusters
```

### Partitions and alignment
- The disk is a big array of LBAs (logical sectors). A **partition** starts at one LBA and ends at another (for example partition 1 = LBA 1–4), giving logical segmentation. Each partition can have its own file system with its own block (cluster) size.
- The **partition table** sits at the start of the disk: **MBR** (old: 4 primary partitions, 2 TiB limit, which is why `fdisk` offered partition numbers 1–4 in the demo) or **GPT** (128 partitions, practically unlimited size, a backup copy at the end of the disk).
- **Alignment**: suppose 8 LBAs map to 1 PBA and the FS block is 8 LBAs. If the partition starts at LBA 2, FS block 0 is LBAs 2–9, which is part of PBA 1 and part of PBA 2: they overlap. Every block write becomes a read-modify-write of two physical sectors, causing performance issues (and extra SSD wear). Modern tools start the first partition at sector 2048 (1 MiB), which is aligned for every sector size; old tools that started at sector 63 produced misaligned partitions.

### Formatting and mounting (from the demo)
- The steps are separate and explicit: create partitions (`fdisk`, `parted`), **format** each one (`mkfs.ext4`, `mkfs.vfat`, `mkfs.ntfs`…; this writes the superblock, inode tables, journal), then **mount** it onto an existing directory. Until it is mounted, a partition's files are unreachable.
- A **mount point** is an ordinary directory that, once mounted, becomes the entry to another file system. `/` itself is just the partition mounted at the root; nothing stops you from mounting an ext4 partition at `/ext4root` and a btrfs one at `/btrfsroot` and running a Postgres instance on each to compare them.
- Device names are not stable: unplugging and re-plugging a USB drive in the demo turned `sdb` into `sdc` and then `sdd`, and the old mount gave an input/output error. That is why `/etc/fstab` mounts by `UUID=` (see `blkid`), and why desktops use an automounter.
- Mounts are per **mount namespace**: a container sees only the mounts it was given, which is how Docker hides the host's partitions and shows its own layered root ([[docker-volumes-and-registry]]).
- Copying a file to a partition with a different file system is a full read and rewrite into new data structures, and can fail on things the target does not support: FAT32 rejects files over 4 GiB and characters like `:` or `?` in names.

```anim
storage-partitions
```

### The page cache
- File-system blocks read from disk are cached in RAM: each block number maps to a virtual-memory page (an FS block is usually ≤ one 4 KiB page), and each block maps to LBAs.
- **Cached read**: the user wants block 3, the OS checks the page cache, block 3 is there, its content is copied to the user buffer. No disk access.
- **Missed read**: block 7 is not cached, so the OS translates it to LBAs (with 1 block = 8 LBAs: 7 × 8 … 7 × 8 + 7 = LBAs 56–63), reads them from disk, and updates the cache. The kernel also reads ahead: sequential reads fetch the next blocks before they are asked for.
- **Write**: the OS writes into the page cache (buffered), creating or updating an entry, and marks it **dirty**. **Later** the kernel's flusher threads write dirty pages to disk (after `vm.dirty_expire_centisecs`, 30 s by default, or when dirty memory exceeds `vm.dirty_background_ratio`), or immediately when `fsync()` is called.
- Upsides: much faster reads, and the cache is shared, so two apps reading the same file share one copy. `free -h` shows it as `buff/cache`, and it counts as *available* memory because clean pages can be dropped at any time.
- Downside: it can cause data loss and corruption. A write goes to the cache, then the OS crashes or the power fails, and the data the app believed it wrote is gone. Durability needs `fsync` (or `fdatasync`), and even then the drive's own write cache must honour the flush.

### File modes: flags to open()
- A file must be opened before it can be used: `open()` returns a file descriptor ([[syscalls-and-file-descriptors]]), and the flags choose the mode.

| Flag | Effect | Who uses it |
|---|---|---|
| `O_RDONLY` / `O_WRONLY` / `O_RDWR` | access mode | everyone |
| `O_CREAT`, `O_EXCL`, `O_TRUNC` | create, fail if it exists, empty on open | lock files (`O_CREAT | O_EXCL`), overwriting outputs |
| `O_APPEND` | every write goes to the current end of file, atomically with respect to other appenders | log files, write-ahead logs |
| `O_DIRECT` | skips the page cache: DMA straight into the user buffer | databases with their own cache |
| `O_SYNC` / `O_DSYNC` | every `write()` waits until the data is on disk (slow) | small critical writes; databases' WAL on some setups |
- **`O_DIRECT` in detail**: a normal `read()` goes disk → DMA → **page cache** → copy → your buffer; with `O_DIRECT` the DMA lands in your buffer. Databases with their own buffer pool use it to avoid **double buffering**: MySQL InnoDB with `innodb_flush_method=O_DIRECT`, Oracle, ScyllaDB. Postgres relies on the page cache instead, which is why its `shared_buffers` is usually only about 25% of RAM.
- `O_DIRECT` requirements: the buffer address, the file offset and the length must all be aligned to the logical block size (512 B or 4 KiB), or the call fails with `EINVAL`. You also lose the kernel's read-ahead and write-behind, so it only pays off when the application caches and schedules IO better than the kernel ([[io-models-blocking-nonblocking-epoll]]).
- `O_DIRECT` does not mean durable: data may still sit in the device's write cache. Use `fsync`/`fdatasync` or `O_DSYNC` for durability.
- In Java: `FileChannel.force(true)` is `fsync` (`force(false)` ≈ `fdatasync`); `StandardOpenOption.APPEND`, `SYNC`, `DSYNC` map to the flags above; `ExtendedOpenOption.DIRECT` gives `O_DIRECT` ([[java-nio]]).

### A read, layer by layer
The course example: read the entire file `test.dat`, 5,000 bytes, with logical sector, physical sector, page and file-system block all 4,096 bytes, so 1 block = 1 LBA. The file needs two blocks: one full block and one with the remaining 904 bytes.
1. The app issues `read()`. The OS reads the file-system metadata, finds the start block and follows it until the end of the file: blocks 6 and 3 (it still needs the LBAs).
2. It queries the page cache: block 3 is cached (page z), block 6 is not.
3. It sends the disk controller a read command: LBA 6 (block 6 = LBA 6), length 1. The controller converts LBA → PBA and returns the data, by DMA, into a new page j; an interrupt signals completion.
4. The OS takes the content from j and z. z holds 4,096 bytes, but the user needs only the last 904 of the file, so it copies the needed bytes into the user buffer, returns success and unblocks the thread.

So many layers: a POSIX `read()` becomes file-system blocks, then pages, then LBAs, then PBAs, and writes are delayed by the page cache.

```anim
storage-read-path
```

### IO scheduling
- Block-layer schedulers reorder and merge requests: **mq-deadline** (bounded latency, a good default for SSD/NVMe), **BFQ** (fair per-process bandwidth, desktops, slow disks), **kyber** (low latency for fast devices), **none** (NVMe does its own queueing). CFQ was the old single-queue scheduler, removed in kernel 5.0.
- `cat /sys/block/nvme0n1/queue/scheduler` shows the active one in brackets.
- `iostat -x 1` for utilisation and await time, `iotop` for per-process IO, `vmstat 1` for `wa` (IO wait).

### Choosing a file system: Kafka on ext4 vs XFS
- Changing the file system under the same workload can change its latency a lot. Allegro moved their Kafka brokers from ext4 to XFS: on the course's charts, p999 produce latency on ext4 had spikes up to ~3 s, on XFS under ~550 ms.
- The usual explanation is the cost of **journal commits** and **metadata updates**: Kafka appends to many segment files constantly, and on ext4 the journal commit can stall writers. XFS handles parallel appends and metadata better. Kafka's own documentation discusses both file systems; XFS is the common recommendation for its log directories ([[kafka-architecture]]).

## Example
```bash
# What disks, partitions and file systems are there?
lsblk -o NAME,SIZE,TYPE,FSTYPE,LOG-SEC,PHY-SEC,MOUNTPOINT
#   sda      931G disk               512  4096          <- 512e drive
#   ├─sda1     1G part vfat          512  4096 /boot/efi
#   └─sda2   930G part ext4          512  4096 /
#   sdb      231G disk               512   512          <- USB stick
#   └─sdb1   231G part vfat          512   512

# Repartition the stick (destroys its data): n = new, d = delete, p = print, w = write
sudo fdisk /dev/sdb          # first sector 2048 (aligned), last sector +5G
sudo mkfs.ext4 /dev/sdb1     # superblock, inode tables, journal
sudo mkdir /mnt/usb && sudo mount /dev/sdb1 /mnt/usb
findmnt /mnt/usb
sudo blkid /dev/sdb1         # UUID=... -> use it in /etc/fstab, not /dev/sdb1
#   UUID=3f1c...  /data  ext4  defaults,noatime  0  2

# Page cache and dirty data
free -h                                  # buff/cache, available
grep -E 'Dirty|Writeback' /proc/meminfo  # data written but not yet on disk
sync                                     # flush everything now

# Is the disk the bottleneck?
iostat -x 1                  # r/s w/s r_await w_await %util per device
sudo smartctl -a /dev/nvme0  # SSD wear: "Percentage Used", "Data Units Written"
```
`%util` near 100 with growing `w_await` on a database volume means the disk is saturated; a large `Dirty` value right before a crash is data the applications thought they had written.

## Pitfalls
- Treating `write()` returning as "saved". Without `fsync` (or `O_SYNC`/`O_DSYNC`) the data is in RAM; a power cut loses it. Databases fsync their WAL on commit; your own file-based state needs the same ([[transactions-acid-and-isolation]]).
- `fsync` on the file but not on the directory after creating or renaming it: the new name may not survive a crash. The safe pattern for replacing a file is write temp → `fsync(temp)` → `rename` → `fsync(dir)`.
- Running databases or Kafka on network or burstable cloud disks without checking IOPS and latency limits: the page cache hides it until the working set stops fitting in RAM.
- Mounting by `/dev/sdX` in `/etc/fstab`: names change between boots and replugs; use `UUID=` or `LABEL=`.
- Filling an SSD to 100%: garbage collection has no free blocks, write amplification and latency rise; keep 10–20% free or rely on the drive's over-provisioning, and keep TRIM enabled.
- Formatting a USB stick for a server-side job with FAT32 and then failing on a 5 GB file or a file name containing `:`.
- Misaligned partitions on a VM image or an old disk: every write touches two physical sectors. Check that partition start sectors are divisible by 8 (or by 2048).
- Benchmarking a disk with a file smaller than RAM: you measure the page cache. Use `fio` with `--direct=1` or a file several times larger than memory.
- Deleting a log file a process still writes to frees nothing until the process closes it (`lsof +L1`); see [[linux-memory-filesystems-io]] for `ls` vs `du` vs `df`.
- Related: [[memory-management]] for the page cache's memory side, DMA and `mmap`, [[process-management]] for blocked threads waiting on IO, [[io-models-blocking-nonblocking-epoll]] for async file IO (`io_uring`), [[file-and-blob-storage]] and [[storage-choices]] for storage at the system-design level.
