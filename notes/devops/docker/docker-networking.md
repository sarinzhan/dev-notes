---
title: Docker networking
tags: [docker, networking, bridge, iptables, compose]
learned: 2026-10-29
---
## TL;DR
Each container gets its own network namespace, and a network driver decides how that namespace is wired. The default is a Linux bridge: a veth pair connects the container's `eth0` to a bridge on the host, outbound traffic is masqueraded, and `-p 8080:80` is an iptables DNAT rule. Use user-defined networks (Compose creates one per project): they add name resolution through the embedded DNS at `127.0.0.11` and isolate unrelated stacks. `host` shares the host's stack, `none` gives only loopback, `overlay` spans Swarm nodes, `macvlan` puts the container directly on the LAN.

## Key points
### Drivers
| Driver | What the container gets | Use for |
|---|---|---|
| `bridge` (default network `bridge`, interface `docker0`, `172.17.0.0/16`) | private IP, NAT out, reach by IP only; names only via the legacy `--link` | throwaway `docker run` |
| user-defined `bridge` (`docker network create`, every Compose project) | own bridge `br-<id>` and subnet, embedded DNS by container/service name, isolation from other networks, attach/detach while running | almost everything on one host |
| `host` | the host's interfaces and ports; `-p` is ignored | packet capture, very high packet rates, tools that must see the host network |
| `none` | only `lo` | batch jobs that must not talk to anything |
| `overlay` | VXLAN network across hosts | Swarm services ([[docker-swarm]]) |
| `macvlan` / `ipvlan` | own MAC (or shared MAC) and an IP on the physical LAN | legacy apps that expect to be a host on the LAN |

### How the bridge works
- `docker0` (or `br-<id>`) is a virtual L2 switch on the host and the default gateway (`172.17.0.1`) of the containers attached to it ([[host-to-host-communication]], [[arp]]).
- Each container gets a **veth pair**: one end is `eth0` inside its namespace, the other (`vethXXXX`) is plugged into the bridge. Namespaces themselves are covered in [[virtualization-and-containers]].
- Container-to-container on the same bridge is plain L2 switching, no NAT. Between different user-defined networks traffic is dropped by Docker's isolation rules.
- Outbound: the host routes the packet ([[network-routing]]) and a `MASQUERADE` rule in `POSTROUTING` rewrites the source `172.17.0.x` to the host's address; conntrack maps replies back ([[ip-addressing-and-nat]]).

### Publishing ports
- `-p 8080:80` adds a DNAT rule in the `nat` table's `DOCKER` chain: packets to any host address on port 8080 get destination `172.17.0.2:80`, plus an `ACCEPT` in the `filter` table's `FORWARD` path. The reply is rewritten back by conntrack (the reverse of that DNAT), so the client sees `host:8080` as the source.
- DNAT happens in `PREROUTING`, so the packet takes the `FORWARD` path and never reaches the `INPUT` chain where `ufw`/`firewalld` rules live: they do not protect published ports. Filter in the `DOCKER-USER` chain or bind to a specific address.
- `-p 127.0.0.1:8080:80` publishes only on the host's loopback: reachable from the host and an SSH tunnel, not from the network. Default for databases and admin UIs on a shared server ([[network-access-to-databases]]).
- `EXPOSE` in a Dockerfile is documentation; it publishes nothing ([[dockerfile-and-images]]).
- The process inside must listen on `0.0.0.0`, not `127.0.0.1`, or the DNAT target has nobody to answer (worked example in [[ip-addressing-and-nat]]).

### Embedded DNS
- On user-defined networks every container's `/etc/resolv.conf` points at `127.0.0.11`; dockerd answers names of containers, Compose services and `--network-alias` aliases, and forwards everything else to the host's resolvers ([[dns-resolution]]).
- A scaled Compose service (`--scale api=3`) returns several A records; clients that cache the first IP (JVM DNS cache, HikariCP holding connections) do not spread load.
- The default `bridge` network has no embedded DNS; containers there get a copy of the host's resolv.conf.

### Compose networks and multiple networks
- Compose creates `<project>_default` and attaches every service; the service name is the hostname. Declare extra networks to split tiers: nginx on `frontend` and `backend`, the app on `backend`, Postgres on `backend` only.
- A container on two networks has two interfaces (`eth0`, `eth1`) and two IPs; `docker network connect backend nginx` adds one at runtime.
- `internal: true` creates a network without a gateway: no outbound traffic and no published ports for containers attached only to it. Good for databases that must never call the internet.
- From a container to a service on the host: `host.docker.internal`, built in on Docker Desktop; on Linux add `extra_hosts: ["host.docker.internal:host-gateway"]` (Docker 20.10+).

```anim
docker-bridge-publish
```

## Example
A Compose stack with two networks (only nginx touches both), a loopback-only DB port, and commands to see the plumbing.
```yaml
# compose.yaml
name: esim                              # networks become esim_frontend, esim_backend
services:
  nginx:
    image: nginx:1.27
    ports: ["443:443"]                  # public entry
    networks: [frontend, backend]
  esim-api:
    image: registry.local/esim-api:1.8.0
    environment:
      SPRING_DATASOURCE_URL: jdbc:postgresql://postgres:5432/esim   # service name via 127.0.0.11
    extra_hosts: ["host.docker.internal:host-gateway"]
    networks: [backend]
  postgres:
    image: postgres:16
    ports: ["127.0.0.1:5432:5432"]      # psql from the host only
    networks: [backend]

networks:
  frontend: {}
  backend:
    ipam:
      config: [{ subnet: 10.30.0.0/24 }] # avoid clashing with the VPN's 172.x
```
```bash
docker compose up -d
docker network inspect esim_backend -f '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}'
ip -br link | grep veth                                 # host ends of the veth pairs
bridge link                                             # which veth is on which br-*
sudo iptables -t nat -S DOCKER                          # DNAT: :443 -> nginx, 127.0.0.1:5432 -> postgres
docker compose exec esim-api getent hosts postgres      # 10.30.0.3  postgres
```

## Pitfalls
- `ports: ["5432:5432"]` on a server with `ufw` "blocking" 5432: the port is open to the world anyway.
- Relying on the default `bridge` network for name resolution: it has none; create a network.
- Docker's default pools (`172.17.0.0/16` and up) colliding with corporate VPN or office subnets; set `default-address-pools` in `daemon.json`.
- `--network host` on Docker Desktop (macOS/Windows) attaches to the hidden Linux VM's network, not your laptop's, unless the opt-in host networking setting (4.34+) is enabled.
- Hard-coding container IPs; they change on recreate. Use service names.
- Related: [[docker-fundamentals]], [[docker-swarm]] for overlay and the routing mesh, [[tcpdump]] for capturing on `docker0` or inside a container, [[linux-networking]] for iptables/nftables.
