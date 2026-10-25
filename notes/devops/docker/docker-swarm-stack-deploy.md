---
title: Swarm stack file: deploy and healthcheck reference
tags: [docker, swarm, stack-file, rolling-update, healthcheck]
learned: 2026-10-22
---
## TL;DR
A stack file is a Compose file plus the `deploy` block that only Swarm reads: mode and replicas, rolling-update and rollback policy, restart policy, placement constraints and preferences, resource limits and reservations. Together with `healthcheck` it defines how a service is rolled out, where it runs, how it is replaced when it breaks and how much of a node it may take. `docker stack deploy -c stack.yml <name>` applies it; the cluster basics are in [[docker-swarm]].

## Key points
### `healthcheck` and `deploy` keys
- **healthcheck**: without it Docker assumes a container is ready as soon as it started and only notices failure when the process exits non-zero. With it the status shows `starting`, `healthy` or `unhealthy`; `FailingStreak` in `docker inspect` increments on each non-zero exit code of the command and resets on success; failures during `start_period` do not count; after `retries` consecutive failures the task is `unhealthy` and Swarm **replaces** it. Details in [[docker-containers-and-resources]]; the mechanics are identical, the difference is that Swarm acts on the result.
- **deploy.mode**: `replicated` (set `replicas: N`) or `global` (exactly one task per node: log shippers, node exporters, see [[logging-stacks]]).
- **deploy.replicas**: number of copies; replicated mode only.
- **deploy.labels**: service metadata for routing, monitoring (`traefik.enable=true`).
- **deploy.update_config** (rolling update without downtime): `parallelism` how many tasks update at once; `delay` pause between batches; `failure_action` `continue | pause | rollback`; `monitor` how long to watch a new task after start, if it survives that window the update of that task counts as successful; `max_failure_ratio` 0–1, the tolerated share of failed task updates, `0` triggers `failure_action` on the first failure; `order` `start-first` (new task up before the old stops, zero downtime, needs spare resources) or `stop-first`.
- **deploy.rollback_config**: same fields, applied when rolling back (automatically on `failure_action: rollback` or manually via `docker service rollback`).
- **deploy.restart_policy**: `condition` `none | on-failure | any`; `delay` before restart; `max_attempts` how many times; `window` the period in which failures are counted.
- **deploy.placement**: `constraints` are hard rules (`node.role == manager`, `node.labels.disk == ssd`, `node.hostname != node01`, `node.labels.region == eu`); `preferences` are soft (`spread: node.labels.zone` distributes tasks evenly across zones). Typical uses: keep the database on a manager with its volume, split backend and frontend across nodes.
- **deploy.resources**: `limits` (max CPU and memory the task may use, enforced by cgroups) and `reservations` (minimum guaranteed amount the scheduler must find on a node before placing the task). Reservations let Swarm spread load sensibly; limits protect neighbours.

### Reading the result
- `docker stack ps esim --no-trunc` shows every task with its node, desired and current state and the error of a failed one; `docker service inspect --pretty esim_api` prints the effective update, rollback and placement config.
- `docker service update --rollback` or `docker service rollback` applies `rollback_config`; `docker service update --force` restarts all tasks with the same spec (useful after a config or secret rotation).
- Secrets and configs referenced by the stack must exist (`external: true`) or be created from files in the stack; both are immutable, rotate by name (`db_pass_v2`) and update the reference ([[docker-volumes-and-registry]]).

## Example
```yaml
# stack.yml  (docker stack deploy -c stack.yml --with-registry-auth esim)
version: "3.9"
services:
  api:
    image: registry.gitlab.com/beeline/esim/api:1.4.2
    networks: [backend, proxy]
    secrets: [db_pass]
    configs:
      - source: api_config
        target: /app/config/application.yml
    environment:
      SPRING_CONFIG_IMPORT: "configtree:/run/secrets/"
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:8080/actuator/health || exit 1"]
      interval: 15s
      timeout: 5s
      start_period: 40s
      retries: 3
    deploy:
      mode: replicated
      replicas: 3
      labels:
        traefik.enable: "true"
        traefik.http.routers.api.rule: "Host(`api.esim.local`)"
        traefik.http.services.api.loadbalancer.server.port: "8080"
      update_config:
        parallelism: 1
        delay: 10s
        order: start-first
        failure_action: rollback
        monitor: 30s
        max_failure_ratio: 0
      rollback_config:
        parallelism: 1
        order: stop-first
      restart_policy:
        condition: on-failure
        delay: 5s
        max_attempts: 3
        window: 60s
      placement:
        constraints: ["node.role == worker", "node.labels.region == eu"]
        preferences: [{ spread: node.labels.zone }]
      resources:
        limits: { cpus: "1.0", memory: 1G }
        reservations: { cpus: "0.25", memory: 512M }

  postgres:
    image: postgres:16
    networks: [backend]
    secrets: [db_pass]
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/db_pass
    volumes: [pgdata:/var/lib/postgresql/data]
    deploy:
      replicas: 1
      placement:
        constraints: ["node.labels.db == true"]   # the node that owns the volume

networks:
  backend: { driver: overlay, attachable: true }
  proxy:   { external: true }
volumes:
  pgdata: {}
secrets:
  db_pass: { external: true }
configs:
  api_config: { file: ./application.yml }
```

## Pitfalls
- `order: start-first` with `replicas: 1` and no spare memory on the node fails the update, because the new task must fit next to the old one; reserve headroom or use `stop-first`.
- `max_failure_ratio: 0` plus `monitor: 30s` and a JVM that takes 45 s to become healthy means every update "fails" and rolls back; align `monitor` with `start_period`.
- `restart_policy.condition: any` with `max_attempts` unset restarts a crash-looping task forever, hiding the bug behind "it is Up again"; set `max_attempts` and alert on `docker events`.
- Constraints that no node satisfies leave tasks in `pending` with "no suitable node"; check `docker service ps` and node labels.
- `resources.limits` are enforced by cgroups like `docker run --memory` ([[docker-containers-and-resources]]), so a Spring Boot app still needs `-XX:MaxRAMPercentage` tuned to the limit.
- Related: [[docker-swarm]], [[dockerfile-and-images]] (`HEALTHCHECK` baked into the image), [[logging-stacks]] (global services for shippers).
