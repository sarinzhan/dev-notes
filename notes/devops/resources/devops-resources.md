---
title: DevOps, OS and general learning resources
tags: [resources, linux, docker, roadmap]
learned: 2026-10-22
---
## TL;DR
Reading list for the DevOps side of the vault: Linux/OS, Docker, nginx, plus the general "Java senior roadmap" links and the to-learn backlog (ClickHouse). Knowledge is in the notes under [[linux-basics|linux]], [[docker-fundamentals|docker]], [[git-commands|git]], [[maven]] and [[logging-stacks|observability]]; this page only tracks where to read more.

## Key points
### Operating systems and Linux
- [How Linux works (YouTube playlist, RU)](https://www.youtube.com/playlist?list=PLHHm04DXWzeKZycf_ZuBgxWdVBnrjE_mj) — video series on Linux internals: kernel, processes, filesystems; pairs with [[linux-architecture-and-kernel]] and [[linux-processes]].
- Study plan carried over from the source notes (topics, in order): basics (FHS, core commands, users/permissions, package managers) -> architecture (kernel vs user space, syscalls, `strace`, init systems, shells, GNU tools) -> processes (`fork`/`execve`, signals, `ps`/`htop`/`nice`, systemd units) -> memory/filesystems/IO (virtual memory, paging, swap, ext4/XFS/btrfs, `/proc` and `/sys`, IO schedulers) -> networking (stack, ARP/IP/ICMP/TCP/UDP, iproute2, nftables, DNS, DHCP) -> kernel internals (modules, sysfs/procfs, scheduler, namespaces, cgroups) -> shell scripting (bash, awk/sed, cron, systemd timers). Each stage now has a note: [[linux-basics]], [[linux-architecture-and-kernel]], [[linux-processes]], [[linux-memory-filesystems-io]], [[linux-networking]], [[linux-commands]].

### Docker and containers
- [Docker documentation](https://docs.docker.com/) — reference for CLI, Dockerfile, Compose, Swarm, BuildKit; the source of truth when a flag behaves unexpectedly.
- *Docker Deep Dive* by Nigel Poulton — book covering engine internals, images, networking, volumes, Swarm and security; the best single read after the docs.
- Docker study plan from the source notes: why Docker exists and what it replaced; containers vs VMs; images and containers; CLI and Compose; networking and volumes; Dockerfile best practices; container and log management. Covered by [[docker-fundamentals]], [[dockerfile-and-images]], [[docker-containers-and-resources]], [[docker-volumes-and-registry]], [[docker-swarm]], [[docker-socket-dind-dood]].

### Web servers
- [The NGINX Handbook (freeCodeCamp)](https://www.freecodecamp.org/news/the-nginx-handbook/) — long-form tutorial: static serving, reverse proxy, load balancing, caching, TLS; the reverse proxy in front of [[docker-swarm]] services.

### Java career roadmaps
- [Roadmap: Java Senior Developer (LinkedIn)](https://www.linkedin.com/pulse/roadmap-java-senior-developer-dineshchand-g-r/) — skills checklist from core Java to architecture and DevOps.
- [Senior Java Developer 2024 (dev.to)](https://dev.to/antofik/senior-java-developer-2024-2cof) — another roadmap with tool and topic lists; useful to cross-check gaps in this vault.

### To learn (backlog)
- [ClickHouse introduction (Habr/OTUS, RU)](https://habr.com/ru/companies/otus/articles/773174/) — column-oriented OLAP database; relevant for analytics on incident and provisioning events next to Postgres ([[database-fundamentals]]).
- [Habr article 910194 (RU)](https://habr.com/ru/articles/910194/) — saved from the feed, unclassified; read and file under the right note.
- Spring Boot Admin — application monitoring UI for Boot actuators (noted in the Spring links; belongs with [[logging-stacks]] and the metrics note [[otel-metric-types]]).

## Pitfalls
- A link list is not knowledge: after reading a resource, fold what matters into the topic note and leave only the one-line pointer here.
- Video playlists in Russian are fine for understanding, but practise the English terms (namespaces, cgroups, page cache) because interviews and docs use them.
- Related: [[architecture-resources]], [[spring-resources]].
