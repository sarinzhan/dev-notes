---
title: docker.sock, Docker-in-Docker and Docker-out-of-Docker
tags: [docker, docker-sock, dind, dood, gitlab-ci]
learned: 2026-10-22
---
## TL;DR
`/var/run/docker.sock` is the Unix socket through which the `docker` CLI sends REST API calls to the daemon `dockerd`; whoever can write to it controls the host. In CI you need Docker inside a job either by **mounting that socket** into the job container (Docker-out-of-Docker, DooD: builds run on the host daemon) or by starting a separate privileged daemon as a service container (Docker-in-Docker, DinD: an isolated daemon, more isolation, more cost).

## Key points
### What docker.sock is
- **Daemon (`dockerd`)**: the main background process that manages all Docker objects: containers, images, volumes, networks. It waits for commands.
- **Client (`docker`)**: the CLI you type `docker run`, `docker ps` into.
- When you run `docker run nginx` the client does not do the work itself. It (1) opens a connection to the daemon through the socket file `/var/run/docker.sock`, (2) sends the command as an HTTP REST request over that connection (`POST /containers/create`, `POST /containers/{id}/start`), (3) the daemon processes the request and performs the action (pulls the image, starts the container).
- **Protocol**: a Unix domain socket (UDS) gives fast local inter-process communication on one host; `DOCKER_HOST=unix:///var/run/docker.sock` is the default. The same API can be served over TCP (`tcp://host:2376` with TLS, `2375` plain) for remote access ([[linux-networking]]).
- **Security**: access to the socket is **equivalent to root on the host**. Through it anyone can start a privileged container, mount `/` of the host, read every secret, and the daemon runs as root. By default only root and members of the `docker` group may use it, which is why adding a user to `docker` is a root grant ([[linux-basics]]). Rootless Docker and Podman reduce this.
- `curl --unix-socket /var/run/docker.sock http://localhost/v1.45/containers/json` shows the API directly; the same socket can be exposed in a container via `-v /var/run/docker.sock:/var/run/docker.sock` ([[docker-volumes-and-registry]]).

### Why CI needs Docker inside a job
- A GitLab Runner with the Docker executor runs every job inside a container. Building an image, pushing to a registry, running Testcontainers or `docker compose up` for integration tests all need a Docker daemon from inside that job container. The two ways to provide one are DooD and DinD.

### DooD (Docker-out-of-Docker)
- Mount the host socket into the job container. The `docker` binary in the job talks to the **host's** daemon; any container or image created by the pipeline actually lives on the runner host.
- Pros: fast, shared layer cache across jobs (the host cache), no privileged mode, no nested storage drivers, Testcontainers works out of the box.
- Cons: jobs share one daemon: no isolation between pipelines, a job can see or delete another job's containers, volume paths are host paths (a `-v $PWD:/src` in the job refers to the host filesystem, not the job container), leaked containers and images accumulate on the host, and a job has root on the runner. Suitable for trusted internal runners.
- Runner config: `volumes = ["/var/run/docker.sock:/var/run/docker.sock", "/cache"]` in `config.toml`; in the job `image: docker:27-cli`, `services: []`, optionally `DOCKER_HOST: unix:///var/run/docker.sock` to be explicit.

### DinD (Docker-in-Docker)
- Instead of mounting the socket, the job declares `services: [docker:27-dind]`: GitLab starts a sidecar container running its **own, fresh daemon**. The job's `docker` client talks to it over TCP (`DOCKER_HOST: tcp://docker:2376` with TLS certificates shared via a volume, or `2375` with `DOCKER_TLS_CERTDIR: ""` for plain).
- Pros: full isolation: the pipeline has its own daemon, cannot touch the host's containers, and everything disappears with the job. Clean, reproducible builds.
- Cons: the dind service needs `privileged = true` on the runner (which itself is a host-root-equivalent capability, so the isolation argument is weaker than it looks); nested overlay2 storage is slower; the layer cache is empty on every job unless you add registry caching (`--cache-from`, BuildKit registry cache) or a persistent volume; Testcontainers needs `DOCKER_HOST` and `TESTCONTAINERS_HOST_OVERRIDE=docker` plus `/certs` tweaks.
- Variants: Kaniko and BuildKit rootless (`moby/buildkit:rootless`) build images without a daemon or privileged mode: the preferred answer when the only need is `docker build`.

### Choosing
| Need | Pick |
|---|---|
| Only build and push images | Kaniko / BuildKit rootless, or DinD |
| Testcontainers or Compose-based integration tests on an internal runner | DooD (simplest, fastest) |
| Untrusted or multi-tenant pipelines | DinD (or a VM-isolated runner) |
| Deploy to a Swarm from CI | `docker` CLI with `DOCKER_HOST=ssh://deploy@manager` or a runner on the manager with DooD ([[docker-swarm]]) |

## Example
```yaml
# .gitlab-ci.yml: two jobs, one per approach
stages: [test, build]

integration-tests:            # DooD: Testcontainers against the runner's daemon
  stage: test
  image: maven:3.9.9-eclipse-temurin-21
  variables:
    DOCKER_HOST: unix:///var/run/docker.sock     # socket mounted by the runner's config.toml
    TESTCONTAINERS_RYUK_DISABLED: "false"
  script:
    - mvn -B verify

build-image:                  # DinD: isolated daemon, result pushed to the registry
  stage: build
  image: docker:27-cli
  services:
    - name: docker:27-dind
      alias: docker
  variables:
    DOCKER_HOST: tcp://docker:2376
    DOCKER_TLS_CERTDIR: /certs
    DOCKER_TLS_VERIFY: "1"
    DOCKER_CERT_PATH: /certs/client
  before_script:
    - docker login -u "$CI_REGISTRY_USER" -p "$CI_JOB_TOKEN" "$CI_REGISTRY"
  script:
    - docker build --pull
        --cache-from "$CI_REGISTRY_IMAGE:cache"
        --build-arg BUILDKIT_INLINE_CACHE=1
        -t "$CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA"
        -t "$CI_REGISTRY_IMAGE:cache" .
    - docker push "$CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA"
    - docker push "$CI_REGISTRY_IMAGE:cache"
```
```toml
# /etc/gitlab-runner/config.toml (excerpt)
[[runners]]
  executor = "docker"
  [runners.docker]
    privileged = true                                   # required for the dind service
    volumes = ["/var/run/docker.sock:/var/run/docker.sock", "/certs/client", "/cache"]
```

## Pitfalls
- Mounting `docker.sock` into any application container (Portainer, Traefik, a "monitoring" sidecar) gives that container root on the host; use a socket proxy (`tecnativa/docker-socket-proxy`) that allows only the needed read endpoints.
- With DooD, bind-mount paths in `docker run -v` inside the job are resolved on the **host**; use volumes or `docker cp` instead.
- DinD without a registry or volume cache rebuilds every layer each job; CI time explodes.
- Mixing both: `services: [docker:dind]` plus a mounted socket; the client talks to whichever `DOCKER_HOST` points at, and the other one wastes resources.
- Orphaned containers from DooD jobs (Testcontainers without Ryuk, cancelled pipelines) fill the runner's disk; schedule `docker system prune -af --filter until=24h` ([[linux-memory-filesystems-io]]).
- Related: [[docker-fundamentals]], [[docker-containers-and-resources]], [[docker-swarm]].
