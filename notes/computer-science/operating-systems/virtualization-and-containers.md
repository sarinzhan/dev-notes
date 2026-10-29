---
title: Virtualization and containers
tags: [operating-systems, virtualization, containers, namespaces, cgroups, docker]
learned: 2026-10-29
order: 10
---
## TL;DR
One machine with one OS is limiting: you cannot run Node 18 and Node 20, or JDK 11 and JDK 21, side by side without them getting in each other's way, and you cannot give each team its own machine. There are three answers. **Multi-boot**: several OSes on different partitions, one running at a time. **Virtual machines**: a **hypervisor** splits the hardware between several complete operating systems, each with its own kernel; isolation is strong, but every kernel and OS install is duplicated. **Containers**: ordinary processes on one **shared kernel**, which uses **namespaces** to control what each one sees (its own PIDs, mounts, network) and **cgroups** to limit what it uses (CPU, memory, pids). The root file system comes from an **image**, stacked from read-only layers with **OverlayFS** so that a hundred containers share one copy. Containers are cheap and start in milliseconds, but a kernel bug affects them all; in the cloud you usually run containers inside VMs to get both.

## Key points
### Why virtualize
- Installing one version of a runtime system-wide usually locks the machine to it, and testing a program against several versions, OSes or configurations would need several machines. Servers also tend to sit mostly idle, so packing several workloads onto one box saves money, as long as they cannot see or starve each other.
- The progression: **multi-boot** (1990s: choose Windows 95 or 98 at startup) → **virtual machines** (several OSes at once) → **containers** (share the kernel, isolate everything else).

```anim
virt-stacks
```

### Multi-boot
- Each OS lives in its own partition; the boot loader (GRUB, the Windows boot manager) asks which one to start. Only one runs; the others are files on disk.
- The strongest isolation there is, since the other systems are not running at all, but switching means a reboot and the hardware is used by one OS at a time.

### Virtual machines and hypervisors
- A **hypervisor** (virtual machine monitor) runs several **guest** operating systems on one machine. Each guest gets a slice of the CPUs and memory, plus virtual devices (disk, NIC, display), and boots a full OS with its own kernel as if it were on real hardware. VirtualBox lets you give a VM one core of four, and its kernel simply sees one core.

| Type | Runs on | Examples | Typical use |
|---|---|---|---|
| Type 1 (bare metal) | directly on the hardware, or as part of the host kernel | KVM (Linux), VMware ESXi, Microsoft Hyper-V, Xen; AWS Nitro is KVM-based | servers, every public cloud |
| Type 2 (hosted) | as an application on a normal OS | VirtualBox, VMware Workstation/Fusion, Parallels | developer laptops, testing |

- **How it really works.** The lecture describes the hypervisor as proxying the guests' system calls to the base kernel. Not quite: a process in a VM makes its system calls to its **own guest kernel**, and most guest code, kernel included, runs directly on the CPU at full speed. Hardware virtualization (Intel VT-x, AMD-V, ARM EL2) runs the guest in a special mode where privileged operations, such as touching real devices or page-table registers, **trap** to the hypervisor (a "VM exit"). The hypervisor emulates them and resumes the guest.
- **Memory**: guest "physical" memory is ordinary virtual memory of the host. The CPU translates twice: guest virtual → guest physical (the guest's page tables) → host physical (the hypervisor's **EPT**/NPT tables). A TLB miss can then cost up to 24 memory reads instead of 4, which is why huge pages matter even more in VMs ([[memory-management]]).
- **IO**: emulating a real NIC or disk controller is slow, so guests use **paravirtual** drivers (**virtio**) that know they talk to a hypervisor, or get a real device passed through (IOMMU, SR-IOV) for near-native speed.
- **CPU**: each vCPU is a thread on the host. When the host gives it to another VM, the guest sees **steal time** (`st` in `top`): a noisy neighbour on an oversubscribed host ([[cpu]]).
- The price is redundancy: every VM carries a whole kernel, OS tools, page cache and background daemons, takes gigabytes, and boots in seconds to minutes. **MicroVMs** (Firecracker, used by AWS Lambda and Fargate) strip the virtual hardware down to the minimum to boot in about 125 ms.

### Containers: one kernel, isolated processes
- The container idea: the kernel is the expensive, shared part, so share it, and isolate everything else. A container is a set of ordinary processes that the kernel shows a **private view** of the system. Originally called **jails** (FreeBSD, 2000).
- Two kernel features make it:
  - **Namespaces** limit what a process **sees**.
  - **cgroups** limit what it **uses**.
  - On top, a different root file system, fewer privileges (capabilities, seccomp) and a runtime (runc, called by containerd, called by Docker or Kubernetes) to set it all up.
- Because the guest shares the host kernel, the user-space tools can be any Linux distribution (an Ubuntu image on a Fedora host is fine: `apt-get` and `bash` are just files), but **not another kernel**. Linux containers need a Linux kernel: Docker Desktop on macOS and Windows runs a hidden Linux VM, and Windows containers need a Windows kernel (that is why Docker Desktop asks you to choose Linux or Windows containers).

### Namespaces: what a container sees
| Namespace | Isolates | Inside the container |
|---|---|---|
| `mnt` | the mount table | its own `/`, which is a directory on the host |
| `pid` | process IDs | its main process is PID 1; it sees only its own processes |
| `net` | interfaces, IPs, routing table, ports, firewall rules | its own `eth0` and `lo`; port 8080 does not clash with the host's |
| `uts` | hostname and domain name | `hostname` returns the container ID |
| `ipc` | System V IPC, POSIX message queues | its own shared memory segments |
| `user` | user and group IDs | root inside can be an unprivileged user outside (rootless containers) |
| `cgroup` | the view of the cgroup tree | its own cgroup looks like the root |
| `time` | boot and monotonic clocks | (rarely used; for checkpoint/restore) |

- **Mount namespace**: the runtime creates a directory on the host, fills it with the image's files, and in a new mount namespace makes it the root (`pivot_root`, the stronger cousin of `chroot`). The process cannot go above its `/`, and sees no other mounts unless given them (volumes are bind mounts of host directories into this namespace). From the host, every container's files are visible under the runtime's directory.
- **PID namespace**: the first process in a new PID namespace is PID 1 there; the host kernel also gives it a global PID, which is what it schedules. `ps` and `top` read `/proc`, and the container gets a `/proc` mounted for its own namespace, so it sees only its own processes. PID 1 has special duties, forwarding signals and reaping zombies, which is why `docker run --init` adds `tini` ([[signals-and-process-lifecycle]]).
- **Network namespace**: a new one has only a loopback. Docker's default **bridge** mode creates a **veth pair** (a virtual cable): one end is the container's `eth0`, the other plugs into the `docker0` bridge on the host, and NAT rules connect it to the outside (`-p 80:8080` adds a port forward). `--network host` skips the namespace and the container uses the host's interfaces directly; `--network none` leaves only loopback ([[linux-networking]]).

```anim
container-isolation
```

### cgroups: what a container uses
- **Control groups** put processes in groups and limit and account their resources. Google engineers started them (as "process containers") because they needed to pack many jobs onto shared machines; they were merged in Linux 2.6.24 (2008). cgroup v2, one unified hierarchy, is the default on current distributions.
- The main controllers:
  - **cpu**: `cpu.max` is a quota per period, so `100000 100000` is one CPU per 100 ms window. A container that uses its quota early in the window is **throttled** until the next one; `cpu.weight` is a relative share under contention.
  - **memory**: `memory.max` is a hard limit. Above it the kernel reclaims, then OOM-kills a process **inside that cgroup** (exit code 137).
  - **pids**: `pids.max` caps the number of processes and threads, which stops fork bombs.
  - **io**: limits on bandwidth and IOPS per device.
- Docker's `--cpus`, `--memory` and `--pids-limit`, and Kubernetes requests and limits, are written into these files. The JVM reads them to size its heap and its thread pools ([[docker-containers-and-resources]], [[jvm-memory-areas]]).
- Namespaces without cgroups would isolate but not protect: one container could still eat all the CPU or memory and starve the rest.

### Images and OverlayFS
- Copying a full Ubuntu file tree into every container's directory, as early container tools did, wastes disk and page cache. Instead an image is a stack of **read-only layers**, and **OverlayFS** merges them into one view:
  - `lowerdir`: the image layers, shared by every container started from the image;
  - `upperdir`: one thin writable layer per container;
  - `merged`: what the container sees as `/`.
- Reading a file finds the topmost layer that has it. The first write to a file from a lower layer **copies it up** into the upper layer (copy-on-write at file level, a costly first write for big files). Deleting a lower file writes a **whiteout** marker in the upper layer.
- One hundred containers from one image share the lower layers on disk and their pages in the page cache. Each Dockerfile instruction adds a layer, which is why layer order matters for caching ([[dockerfile-and-images]]).

### What containers cost, and what they do not isolate
- A container's processes are ordinary processes. Switching between two containers is a normal process context switch, with the usual TLB cost of a new address space; namespaces themselves add nothing to the switch (the lecture suggests switching containers flushes everything for security; it costs what any process switch costs) ([[process-management]]).
- System calls go straight to the host kernel with no translation, so CPU-bound code runs at native speed. The overhead is in the details: the bridge and NAT add a little network latency, OverlayFS makes the first write to a big file slow, and cgroup throttling can add latency spikes.
- **The shared kernel is the weak spot.** A kernel bug reachable from a container can escape to the host and to every other container. Defences, from cheap to strong:
  - drop Linux **capabilities** and run as non-root;
  - **seccomp** profiles that block risky system calls (Docker's default profile blocks dozens, including `io_uring`);
  - **user namespaces** (rootless containers, where root inside is nobody outside);
  - a read-only root file system;
  - **gVisor**, a kernel written in user space that intercepts the container's system calls;
  - **Kata Containers** or **Firecracker**: each container in its own lightweight VM.
- Which is why clouds isolate customers with VMs and let each customer isolate their own services with containers.

### VMs vs containers
| | Virtual machine | Container |
|---|---|---|
| Kernel | Its own guest kernel on a hypervisor | Shares the host kernel |
| Isolation | Hardware-enforced by the hypervisor | Namespaces (what it sees) + cgroups (what it uses) |
| Start time | Seconds to minutes (firmware, kernel, init); ~125 ms for a microVM | Milliseconds (`clone()` into namespaces + `exec`) |
| Size | Gigabytes (full OS) | Megabytes (app + libraries), shared layers |
| Overhead | Hardware virtualization, two-level page tables, duplicated kernels and caches | Near zero: native system calls |
| Security boundary | Strong | Weaker: a kernel exploit escapes to the host |
| Can run | Any OS for the CPU architecture | Only programs for the host kernel (Linux on Linux) |

## Example
A container by hand, without Docker, then the same thing seen in a real one:

```bash
# 1. New PID, mount, UTS and network namespaces; --mount-proc gives a /proc for the new PID namespace
sudo unshare --pid --fork --mount-proc --uts --net bash
ps aux            # only bash and ps: bash is PID 1
hostname box1     # changes the hostname only inside the UTS namespace
ip link           # only "lo": a fresh network namespace
exit

# 2. A cgroup v2 limit by hand: at most half a CPU and 256 MiB
sudo mkdir /sys/fs/cgroup/demo
echo "50000 100000" | sudo tee /sys/fs/cgroup/demo/cpu.max
echo 256M           | sudo tee /sys/fs/cgroup/demo/memory.max
echo $$             | sudo tee /sys/fs/cgroup/demo/cgroup.procs   # move this shell into the cgroup

# 3. The same pieces in a running Docker container
PID=$(docker inspect -f '{{.State.Pid}}' esim-api)   # the container's PID 1, as the host sees it
sudo ls -l /proc/$PID/ns/                 # links to its pid, net, mnt, ipc, uts, user, cgroup namespaces
sudo lsns -p $PID                         # the same as a table
sudo nsenter -t $PID -n ss -ltnp          # run ss inside its network namespace only
docker inspect -f '{{.GraphDriver.Data.MergedDir}}' esim-api   # its "/" on the host (OverlayFS merged dir)
cat /sys/fs/cgroup/system.slice/docker-$(docker inspect -f '{{.Id}}' esim-api).scope/memory.max
```

## Pitfalls
- Treating a container as a small VM: it has no kernel of its own. Kernel version, kernel parameters (`sysctl`), and kernel bugs are the host's.
- Building an image on an ARM Mac and running it on x86 servers: containers share the host CPU architecture too, so the binary fails with `exec format error` ([[anatomy-of-a-process]]).
- Running containers as root with all capabilities "because it is isolated": root inside plus a kernel bug is root on the host. Use a non-root user, drop capabilities, keep seccomp on.
- Setting a CPU limit and reading only average CPU: a JVM with many GC and request threads uses its quota early in each 100 ms window and is throttled for the rest. Check `nr_throttled` in `cpu.stat`.
- Writing heavy data into the container's writable layer: it is slow (copy-up, OverlayFS) and lost when the container is removed. Use volumes.
- Assuming VM performance equals bare metal for memory-heavy work: two-level page tables make TLB misses much more expensive; use huge pages and enough RAM.
- Related: [[docker-fundamentals]] for images, containers and the Docker architecture, [[docker-containers-and-resources]] for limits and lifecycle in practice, [[kubernetes-basics]] for orchestration, [[linux-architecture-and-kernel]] for the rest of the kernel.
