---
title: Docker fundamentals, containers vs VMs
tags: [docker, containers, virtualization, images]
learned: 2026-10-22
---
## TL;DR
Docker packages an application with all its dependencies into a lightweight **container**: an isolated process on the host kernel, not an emulated machine. A **virtual machine** boots a full guest OS with its own kernel on a hypervisor; a container shares the host kernel and is isolated by namespaces and limited by cgroups. An **image** is the immutable, layered template; a **container** is a running instance of it with one thin writable layer on top.

## Key points
### Why Docker appeared
- Before: "works on my machine". Deployment meant installing the right JDK, libraries, config and system packages on every server by hand or with Ansible/Puppet; environments drifted. VMs solved isolation but cost gigabytes and minutes per instance.
- Docker (2013) made Linux containers (namespaces + cgroups, already in the kernel; LXC existed) usable: a `Dockerfile` to describe the environment, an image format to ship it, a registry to distribute it, one command to run it anywhere with the same result.
- What it solves: reproducible environments, dependency isolation, fast start (milliseconds), density (dozens of containers per host), immutable deploy artifacts, the base for orchestration ([[docker-swarm]], Kubernetes).
- Drawbacks: shared kernel (weaker isolation than a VM, Linux-only containers on Linux kernels; Docker Desktop runs a hidden Linux VM on macOS/Windows), image sprawl and disk usage, networking and storage add complexity, root daemon as attack surface ([[docker-socket-dind-dood]]), stateful workloads need care ([[docker-volumes-and-registry]]).

### Containers vs virtual machines
- Short version: a VM boots its own kernel on a hypervisor; a container is a group of host processes isolated by namespaces and limited by cgroups. Hypervisor types, how hardware virtualization works, every namespace, cgroup controllers, OverlayFS and the security trade-offs are in [[virtualization-and-containers]].
- `ps` on the host shows the container's processes as ordinary processes; `docker top <c>` maps them.

### Image vs container
- **Image**: a read-only template with the application and all its dependencies (base OS files, JDK, jar, config). Stored as an ordered set of layers; each layer is the filesystem diff produced by one Dockerfile instruction. Identified by a content digest (`sha256:...`) and tagged (`repo/app:1.4.2`). Immutable: a new instruction creates a new layer, previous layers stay untouched ([[dockerfile-and-images]]).
- **Container**: a running (or stopped) instance of an image: the image layers plus one thin **writable layer** plus runtime state (PID, network namespace, mounts, env). Many containers from one image share the read-only layers. Deleting the container deletes its writable layer; data that must survive goes to a volume.
- Images are built from a Dockerfile (`docker build`) or committed from a running container (`docker commit`; avoid, not reproducible). Pull from a registry with `docker pull`.

### Layered filesystem
- Each layer is a directory of files; the storage driver (**overlay2** on modern Linux) stacks them with a union mount: upper layers hide or add files from lower ones; a file deleted in an upper layer is masked with a *whiteout*, the bytes remain in the lower layer.
- Benefits: layers are shared between images and containers on disk and in the registry (pull only what you do not have), builds are cached per layer, a container start copies nothing.
- Writes inside a container go to its writable layer via copy-on-write: the first write to a file from a lower layer copies the whole file up. Heavy write workloads (databases) belong on volumes.
- `docker history <image>` lists layers and sizes; `docker image inspect` shows the `RootFS.Layers` digests; `docker system df -v` shows shared vs unique space.

### Docker architecture
- `docker` CLI -> REST API over `/var/run/docker.sock` -> `dockerd` -> `containerd` (image and container lifecycle) -> `runc` (OCI runtime that actually does the `clone()` into namespaces). Kubernetes talks to containerd directly; the images are the same OCI format.
- Docker Compose describes multi-container applications in `compose.yaml` for one host; Swarm and Kubernetes schedule across hosts.

## Example
```bash
# Same image, two containers; prove they share layers and the kernel
docker pull eclipse-temurin:21-jre-alpine
docker history eclipse-temurin:21-jre-alpine --format '{{.Size}}\t{{.CreatedBy}}' | head -5

docker run -d --name a eclipse-temurin:21-jre-alpine sleep 1d
docker run -d --name b eclipse-temurin:21-jre-alpine sleep 1d
docker system df -v | grep -A3 'Containers space'       # each container: ~0 B unique, image shared

uname -r                                                 # host kernel
docker exec a uname -r                                   # identical: there is no guest kernel
docker exec a cat /etc/os-release | head -2              # Alpine userland on top of it
ps -o pid,ppid,cmd -C sleep                              # the two sleeps are plain host processes
docker inspect -f '{{.GraphDriver.Data.UpperDir}}' a      # the writable layer on disk
```

## Pitfalls
- Treating a container as a VM: installing `sshd`, running several daemons, storing data in the writable layer. One process per container, data in volumes, logs to stdout.
- Image `latest` is just a tag that moves; pin versions or digests for reproducible deploys.
- Alpine uses musl, not glibc; some native libraries and DNS behaviours differ. `-jre-alpine` is small but test it; distroless or `-jre-jammy` are glibc alternatives.
- Writes to the container layer are lost on `docker rm` and slow under overlay2; see [[docker-volumes-and-registry]].
- Related: [[dockerfile-and-images]], [[docker-containers-and-resources]], [[linux-architecture-and-kernel]].
