---
title: Docker volumes, bind mounts and registries
tags: [docker, volumes, storage, registry, docker-hub]
learned: 2026-10-22
---
## TL;DR
A container's writable layer dies with the container and is slow for heavy writes, so persistent data lives in **volumes** (managed by Docker under `/var/lib/docker/volumes`) or **bind mounts** (any host path mapped into the container). A **registry** (Docker Hub, GitLab Container Registry, Harbor, ECR) is the repository of images: `docker push` uploads layers, `docker pull` downloads the missing ones, and tags are mutable names for immutable digests.

## Key points
### Three kinds of mounts
| | Volume | Bind mount | tmpfs |
|---|---|---|---|
| Syntax | `-v pgdata:/var/lib/postgresql/data` or `--mount type=volume,src=pgdata,dst=...` | `-v /srv/app/config:/app/config:ro` or `--mount type=bind,...` | `--tmpfs /tmp` or `--mount type=tmpfs,dst=/tmp,tmpfs-size=256m` |
| Where data lives | `/var/lib/docker/volumes/<name>/_data`, managed by Docker | Exactly the host path you give | RAM of the host |
| Created by | Docker (`docker volume create`, or on first use) | Must exist on the host (`-v` creates a directory if missing; `--mount` errors) | Docker |
| Portable | Yes: name only, driver can be NFS/cloud | No: depends on host layout | n/a |
| Pre-population | An empty new volume is seeded with the image's content at that path | Host content hides the image's content | Empty |
| Typical use | Databases, Kafka log dirs, uploaded files, Grafana state | Config files, source code in dev, `/var/run/docker.sock` ([[docker-socket-dind-dood]]) | Secrets, scratch space, read-only root FS |
- A **volume** is the recommended default for persistent data: Docker owns the lifecycle, you can back it up and migrate it, and volume drivers can place it on NFS, Ceph or cloud block storage ([[docker-swarm]]).
- A **bind mount** gives the container a view of a host directory: host and container see every change instantly. Handy in development (`-v $PWD:/src`), risky in production because the container can modify host files and the path must exist on every host.
- `VOLUME /data` in a Dockerfile creates an *anonymous* volume (`docker volume ls` shows a hash) each time a container starts without an explicit mount; they pile up and `docker rm -v` is needed to remove them.
- Mount options: `:ro` read-only, `:z`/`:Z` SELinux relabel, `consistency` (macOS). Named volumes keep uid/gid from the image; bind mounts keep host ownership, so the container user must be able to read them ([[linux-basics]]).

### Volume operations
- `docker volume create|ls|inspect|rm|prune`; `docker volume inspect pgdata -f '{{.Mountpoint}}'`.
- Backup: run a throwaway container that mounts the volume and a host directory, then `tar` (see Example). Restore the same way. Database volumes should be backed up with the database's own tool (`pg_dump`, `pg_basebackup`) for consistency, the tar approach is for cold backups.
- Compose: top-level `volumes:` declares named volumes; `docker compose down -v` deletes them (data loss), plain `down` keeps them.
- Space: `docker system df`, `docker volume prune` (dangling only), check `/var/lib/docker` when the disk is full ([[linux-memory-filesystems-io]]).

### Registries
- **Docker Hub** (`docker.io`) is the default public registry: official images (`postgres`, `eclipse-temurin`), verified publishers, your own public/private repos. Anonymous pull rate limits (100 per 6 h per IP, 200 authenticated) bite CI; mirror or authenticate.
- Self-hosted and vendor registries: GitLab Container Registry (`registry.gitlab.com/group/project/app:tag`), GitHub `ghcr.io`, Harbor (vulnerability scanning, replication, RBAC), AWS ECR, Google Artifact Registry, the plain `registry:2` image as a minimal private registry.
- Image reference anatomy: `registry.example.com:5000/team/app:1.4.2@sha256:abc...` = host[:port] / repository (namespace/name) : tag @ digest. Without a host it defaults to Docker Hub; without a tag to `latest`.
- `docker login registry.example.com` stores a token in `~/.docker/config.json` (or a credential helper); `docker tag local:dev registry.example.com/team/app:1.4.2`; `docker push`; `docker pull`. Only layers the registry does not already have are uploaded (content-addressed storage), so a code change pushes one small layer ([[dockerfile-and-images]]).
- Tags are mutable: `1.4.2` can be re-pushed to point at a different image. Deploy by digest or use immutable-tag policies in Harbor/ECR for production. `docker buildx imagetools inspect` shows the manifest list (multi-arch amd64/arm64 images under one tag).
- Registry API is HTTP: `GET /v2/_catalog`, `GET /v2/<repo>/tags/list`, manifests and blobs; a registry needs TLS or must be listed under `insecure-registries` in `daemon.json`.
- Image signing and scanning: Docker Scout / Trivy for CVEs, cosign (Sigstore) for signatures, retention policies to delete old tags.

## Example
```bash
# Named volume for Postgres, cold backup and restore, then push the app image
docker volume create pgdata
docker run -d --name pg --restart unless-stopped \
  -e POSTGRES_PASSWORD_FILE=/run/secrets/pg_pass \
  -v pgdata:/var/lib/postgresql/data \
  -v /etc/esim/pg_pass:/run/secrets/pg_pass:ro \
  -p 127.0.0.1:5432:5432 postgres:16

# cold backup of the volume into /backup on the host (stop the writer first for consistency)
docker stop pg
docker run --rm -v pgdata:/data:ro -v /backup:/backup alpine \
  tar -czf /backup/pgdata-$(date +%F).tgz -C /data .
docker start pg
# restore into a fresh volume
docker volume create pgdata_restored
docker run --rm -v pgdata_restored:/data -v /backup:/backup:ro alpine \
  tar -xzf /backup/pgdata-2026-10-22.tgz -C /data

# publish the application image to GitLab's registry
docker login registry.gitlab.com -u "$CI_REGISTRY_USER" -p "$CI_JOB_TOKEN"
docker tag esim-api:local registry.gitlab.com/beeline/esim/api:1.4.2
docker push registry.gitlab.com/beeline/esim/api:1.4.2
docker buildx imagetools inspect registry.gitlab.com/beeline/esim/api:1.4.2 | grep -E 'Digest|Platform'
```

## Pitfalls
- `docker compose down -v` in the wrong directory deletes the database volume; name volumes explicitly and back them up.
- A bind-mounted config directory owned by root with `USER app` in the image leads to `Permission denied`; fix ownership on the host or use `:ro` with world-readable files.
- Mounting a volume over a directory that the image populates hides the image's files once the volume is non-empty; init scripts that expect image content get confused.
- Pulling `latest` on each deploy gives different images on different nodes; pin tags, and pin digests for anything security-sensitive.
- Hub rate limits make CI fail randomly at 429; use a pull-through cache (`registry` with `proxy.remoteurl`) or authenticate.
- Related: [[docker-fundamentals]], [[docker-swarm]] (configs, secrets and shared storage across nodes), [[linux-basics]].
