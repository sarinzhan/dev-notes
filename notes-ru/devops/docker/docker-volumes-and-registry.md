---
title: Volumes, bind mounts и registry в Docker
tags: [docker, volumes, storage, registry, docker-hub]
learned: 2026-10-22
---
## Кратко
Записываемый слой контейнера умирает вместе с контейнером и медленно работает при интенсивной записи, поэтому постоянные данные хранят в **volumes** (управляются Docker в `/var/lib/docker/volumes`) или **bind mounts** (любой путь хоста, проброшенный в контейнер). **Registry** (Docker Hub, GitLab Container Registry, Harbor, ECR) — это хранилище образов: `docker push` загружает слои, `docker pull` скачивает недостающие, а теги — это изменяемые имена для неизменяемых дайджестов.

## Основное
### Три вида монтирования
| | Volume | Bind mount | tmpfs |
|---|---|---|---|
| Синтаксис | `-v pgdata:/var/lib/postgresql/data` или `--mount type=volume,src=pgdata,dst=...` | `-v /srv/app/config:/app/config:ro` или `--mount type=bind,...` | `--tmpfs /tmp` или `--mount type=tmpfs,dst=/tmp,tmpfs-size=256m` |
| Где лежат данные | `/var/lib/docker/volumes/<name>/_data`, управляется Docker | Ровно по указанному пути хоста | В RAM хоста |
| Кто создаёт | Docker (`docker volume create` или при первом использовании) | Должен существовать на хосте (`-v` создаёт каталог, если его нет; `--mount` выдаёт ошибку) | Docker |
| Переносимость | Да: только имя, драйвер может быть NFS/облачным | Нет: зависит от структуры хоста | н/д |
| Начальное заполнение | Пустой новый volume заполняется содержимым образа по этому пути | Содержимое хоста скрывает содержимое образа | Пусто |
| Типичное применение | Базы данных, каталоги логов Kafka, загруженные файлы, состояние Grafana | Конфигурационные файлы, исходный код в разработке, `/var/run/docker.sock` ([[docker-socket-dind-dood]]) | Секреты, временное пространство, корневая ФС только для чтения |
- **Volume** — рекомендуемый вариант по умолчанию для постоянных данных: жизненным циклом владеет Docker, его можно бэкапить и переносить, а драйверы volumes могут размещать его на NFS, Ceph или облачном блочном хранилище ([[docker-swarm]]).
- **Bind mount** даёт контейнеру доступ к каталогу хоста: хост и контейнер мгновенно видят любое изменение. Удобно в разработке (`-v $PWD:/src`), рискованно в production, потому что контейнер может менять файлы хоста, а путь должен существовать на каждом хосте.
- `VOLUME /data` в Dockerfile создаёт *анонимный* volume (`docker volume ls` показывает хеш) при каждом запуске контейнера без явного монтирования; они накапливаются, и для их удаления нужен `docker rm -v`.
- Опции монтирования: `:ro` только чтение, `:z`/`:Z` перемаркировка SELinux, `consistency` (macOS). Именованные volumes сохраняют uid/gid из образа; bind mounts сохраняют владельца на хосте, поэтому пользователь контейнера должен иметь право их читать ([[linux-basics]]).

### Операции с volumes
- `docker volume create|ls|inspect|rm|prune`; `docker volume inspect pgdata -f '{{.Mountpoint}}'`.
- Бэкап: запустить одноразовый контейнер, который монтирует volume и каталог хоста, затем `tar` (см. Пример). Восстановление — так же. Volumes баз данных для согласованности лучше бэкапить собственным инструментом БД (`pg_dump`, `pg_basebackup`), подход с tar — для холодных бэкапов.
- Compose: `volumes:` верхнего уровня объявляет именованные volumes; `docker compose down -v` удаляет их (потеря данных), обычный `down` сохраняет.
- Место: `docker system df`, `docker volume prune` (только висячие), проверяй `/var/lib/docker`, когда диск заполнен ([[linux-memory-filesystems-io]]).

### Registry
- **Docker Hub** (`docker.io`) — публичный registry по умолчанию: официальные образы (`postgres`, `eclipse-temurin`), проверенные издатели, собственные публичные и приватные репозитории. Лимиты на анонимный pull (100 за 6 ч на IP, 200 с аутентификацией) бьют по CI; используй зеркало или аутентифицируйся.
- Self-hosted и вендорские registry: GitLab Container Registry (`registry.gitlab.com/group/project/app:tag`), GitHub `ghcr.io`, Harbor (сканирование уязвимостей, репликация, RBAC), AWS ECR, Google Artifact Registry, простой образ `registry:2` как минимальный приватный registry.
- Анатомия ссылки на образ: `registry.example.com:5000/team/app:1.4.2@sha256:abc...` = host[:port] / repository (namespace/name) : tag @ digest. Без хоста подразумевается Docker Hub; без тега — `latest`.
- `docker login registry.example.com` сохраняет токен в `~/.docker/config.json` (или в credential helper); `docker tag local:dev registry.example.com/team/app:1.4.2`; `docker push`; `docker pull`. Загружаются только слои, которых в registry ещё нет (хранилище с адресацией по содержимому), поэтому изменение кода пушит один маленький слой ([[dockerfile-and-images]]).
- Теги изменяемы: `1.4.2` можно перезапушить так, чтобы он указывал на другой образ. Для production деплой по дайджесту или используй политики неизменяемых тегов в Harbor/ECR. `docker buildx imagetools inspect` показывает manifest list (мультиархитектурные образы amd64/arm64 под одним тегом).
- API registry — HTTP: `GET /v2/_catalog`, `GET /v2/<repo>/tags/list`, манифесты и blobs; registry нужен TLS, иначе его надо внести в `insecure-registries` в `daemon.json`.
- Подпись и сканирование образов: Docker Scout / Trivy для CVE, cosign (Sigstore) для подписей, политики хранения для удаления старых тегов.

## Пример
```bash
# Именованный volume для Postgres, холодный бэкап и восстановление, затем push образа приложения
docker volume create pgdata
docker run -d --name pg --restart unless-stopped \
  -e POSTGRES_PASSWORD_FILE=/run/secrets/pg_pass \
  -v pgdata:/var/lib/postgresql/data \
  -v /etc/esim/pg_pass:/run/secrets/pg_pass:ro \
  -p 127.0.0.1:5432:5432 postgres:16

# холодный бэкап volume в /backup на хосте (для согласованности сначала останови пишущий процесс)
docker stop pg
docker run --rm -v pgdata:/data:ro -v /backup:/backup alpine \
  tar -czf /backup/pgdata-$(date +%F).tgz -C /data .
docker start pg
# восстановление в новый volume
docker volume create pgdata_restored
docker run --rm -v pgdata_restored:/data -v /backup:/backup:ro alpine \
  tar -xzf /backup/pgdata-2026-10-22.tgz -C /data

# публикация образа приложения в registry GitLab
docker login registry.gitlab.com -u "$CI_REGISTRY_USER" -p "$CI_JOB_TOKEN"
docker tag esim-api:local registry.gitlab.com/beeline/esim/api:1.4.2
docker push registry.gitlab.com/beeline/esim/api:1.4.2
docker buildx imagetools inspect registry.gitlab.com/beeline/esim/api:1.4.2 | grep -E 'Digest|Platform'
```

## Подводные камни
- `docker compose down -v` не в том каталоге удаляет volume базы данных; давай volumes явные имена и бэкапь их.
- Каталог конфигурации, смонтированный через bind mount и принадлежащий root, при `USER app` в образе приводит к `Permission denied`; исправь владельца на хосте или используй `:ro` с файлами, доступными на чтение всем.
- Монтирование volume поверх каталога, который заполняет образ, скрывает файлы образа, как только volume становится непустым; init-скрипты, ожидающие содержимое образа, сбиваются с толку.
- Pull `latest` при каждом деплое даёт разные образы на разных узлах; фиксируй теги, а для всего, что важно с точки зрения безопасности, — дайджесты.
- Лимиты Hub заставляют CI случайно падать с 429; используй pull-through кеш (`registry` с `proxy.remoteurl`) или аутентифицируйся.
- Связанные заметки: [[docker-fundamentals]], [[docker-swarm]] (configs, secrets и общее хранилище между узлами), [[linux-basics]].
