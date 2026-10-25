---
title: Docker Swarm
tags: [docker, swarm, orchestration, deploy, overlay-network]
learned: 2026-10-22
---
## TL;DR
Docker Swarm is Docker's built-in orchestrator: it joins several Docker hosts into one cluster, runs applications as **services** made of replicated **tasks** (containers), balances traffic between them, restarts failed ones, rolls out updates gradually and keeps secrets. You describe the desired state in a Compose-format stack file ([[docker-swarm-stack-deploy]]) and `docker stack deploy` makes the cluster converge to it. Simpler than Kubernetes, enough for a few nodes.

## Key points
### Capabilities
- Clustering (several hosts, one logical cluster), service orchestration through declarative descriptions, scaling by replica count, built-in load balancing, high availability with automatic failover of tasks from a dead node, declarative management via stack files (Compose format) or the CLI.

### Core concepts
- **Node**: one Docker host in the cluster. **Managers** hold cluster state, schedule tasks and serve the API; **workers** only run tasks. Managers run tasks too unless drained.
- **Raft consensus**: managers replicate state through Raft; a majority (quorum) must be up to make changes. Run 3 or 5 managers (tolerates 1 or 2 failures); an even number adds nothing. Losing quorum freezes scheduling; running tasks keep running. Back up `/var/lib/docker/swarm` for disaster recovery.
- **Service**: the higher-level abstraction that defines how containers are deployed, managed and scaled across the swarm: image, replica count, networks, ports, update and restart policy, placement, resources.
- **Task**: one instance of a container created and managed by a service, the unit of scheduling; it is placed on one node and is the actually running container that fulfils the service spec. A dead task is not restarted in place: the scheduler creates a new one, the old stays in `docker service ps` history.
- **Overlay network**: a VXLAN virtual network spanning hosts, so containers on different nodes talk by service name as if on one bridge.
- Services vs containers: `docker run` creates a container tied to one host; `docker service create` creates desired state that the swarm maintains anywhere.

### Cluster lifecycle (CLI)
- `docker swarm init --advertise-addr 10.0.0.1` on the first manager; it prints `docker swarm join --token <worker-token> 10.0.0.1:2377`. `docker swarm join-token manager` for a manager token.
- `docker node ls`, `docker node update --label-add region=eu node02`, `docker node promote|demote`, `docker node rm`.
- `docker service create|ls|ps|logs|inspect|scale|update|rollback|rm` manage services (see Example).
- `docker stack deploy -c stack.yml esim`, `docker stack services esim`, `docker stack ps esim`, `docker stack rm esim`.
- Node ports: 2377/tcp (management), 7946/tcp+udp (gossip), 4789/udp (VXLAN) ([[linux-networking]]).

### Networks and load balancing
- **Overlay vs bridge**: bridge networks are per host; overlay networks span the cluster (optionally encrypted with `--opt encrypted`). A service can attach to several overlays, for example `frontend` and `backend`.
- **Ingress network**: a special overlay plus the **routing mesh**: a published port (`-p 8080:8080`) is open on *every* node; a request hitting any node is forwarded (IPVS) to a healthy task anywhere. `mode: host` publishes only on the node running the task (needed for some UDP or client-IP-preserving cases).
- **VIP vs DNS round robin**: by default each service gets a virtual IP; the embedded DNS resolves the service name to the VIP and IPVS balances connections across tasks. `endpoint_mode: dnsrr` returns all task IPs instead, for clients that do their own balancing or for stateful sets. The JVM must not cache DNS forever with `dnsrr`.
- A reverse proxy (Traefik, nginx) usually fronts the mesh for TLS and host-based routing; Traefik reads service labels.

### Storage
- The container writable layer and local volumes stay on the node that ran the task; after a reschedule to another node the data is not there ([[docker-volumes-and-registry]]).
- Options: pin stateful services to one node with a placement constraint and a local volume; shared storage (NFS, GlusterFS, Ceph) mounted on every node or through a volume plugin; or keep real databases outside the swarm. Back up with the tar-through-a-container approach plus logical dumps.

### Security, secrets and configs
- Node traffic is mutual TLS; the root CA is created at `swarm init`, certificates rotate automatically, join tokens can be rotated (`docker swarm join-token --rotate worker`).
- **Secrets** (`docker secret create db_pass -`): encrypted at rest in the Raft log, delivered only to tasks that use them, mounted as in-memory files at `/run/secrets/<name>`; never visible in `docker inspect` or env. Spring Boot reads them via `spring.config.import=configtree:/run/secrets/` ([[spring-boot-properties-and-profiles]]).
- **Configs** (`docker config create`): the same mechanism for non-sensitive files (`application.yml`, nginx config). Both are immutable; rotate by name.
- No RBAC in Swarm itself: access to a manager's Docker API is full control ([[docker-socket-dind-dood]]); protect the socket, TLS the API if exposed, firewall swarm ports to the node subnet.

### CI/CD and operations
- Pipeline: build and push the image ([[dockerfile-and-images]]), then on a manager `docker stack deploy -c stack.yml --with-registry-auth esim` with the new tag; Swarm does the rolling update. GitLab CI runs it over SSH or from a runner on a manager.
- Canary: a second service with 1 replica behind a Traefik weight, or `update_config` with `parallelism: 1` and a `monitor` window ([[docker-swarm-stack-deploy]]).
- Monitoring: `docker service ps --no-trunc`, `docker events`, cAdvisor/node-exporter and a log shipper as global services ([[logging-stacks]]).
- Swarm vs Kubernetes: Swarm is built in, Compose syntax, minutes to set up, fine for a handful of nodes. Kubernetes has a far richer ecosystem (operators, autoscaling, CRDs, ingress, network policies, StatefulSets), a steep learning curve and is the industry default for larger platforms. Pick Swarm for small teams and on-prem simplicity; move when you need what it lacks.

## Example
```bash
# Bring up a 3-node cluster and run a service with a rolling update
docker swarm init --advertise-addr 10.0.0.1                # on node01 (manager)
docker swarm join --token SWMTKN-1-... 10.0.0.1:2377        # on node02, node03 (workers)
docker node ls
docker node update --label-add region=eu --label-add zone=a node02
docker node update --label-add region=eu --label-add zone=b node03

docker network create --driver overlay --attachable backend
printf 'S3cr3t' | docker secret create db_pass -

docker service create --name api --replicas 3 \
  --network backend -p 8080:8080 \
  --secret db_pass \
  --constraint 'node.role == worker' --placement-pref 'spread=node.labels.zone' \
  --limit-memory 1G --reserve-memory 512M \
  --update-parallelism 1 --update-delay 10s --update-order start-first --update-failure-action rollback \
  registry.gitlab.com/beeline/esim/api:1.4.2

docker service ps api                                      # which node runs each task
docker service scale api=6
docker service update --image registry.gitlab.com/beeline/esim/api:1.4.3 api   # rolling update
docker service rollback api                                # if the new version misbehaves
docker node update --availability drain node03             # tasks move away; maintenance
```

## Pitfalls
- Two managers are worse than one: losing either loses quorum. Use 1, 3 or 5.
- `docker stack deploy` ignores `build:`, `depends_on` ordering and `container_name`; images must be in a registry reachable by every node, hence `--with-registry-auth`.
- A rescheduled stateful task lands on a node without its volume and starts empty; constrain it or use shared storage.
- The routing mesh hides the client IP from the application; use `mode: host` or trust `X-Forwarded-For` from the proxy.
- Health checks that call the database make a DB hiccup restart every replica at once; `start_period` too short causes restart loops of slow-starting JVMs ([[docker-swarm-stack-deploy]]).
- Related: [[docker-swarm-stack-deploy]] (stack file reference), [[docker-containers-and-resources]], [[docker-volumes-and-registry]], [[docker-socket-dind-dood]], [[linux-networking]].
