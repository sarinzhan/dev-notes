---
title: docker.sock, Docker-in-Docker и Docker-out-of-Docker
tags: [docker, docker-sock, dind, dood, gitlab-ci]
learned: 2026-10-22
---
## Кратко
`/var/run/docker.sock` — это Unix-сокет, через который CLI `docker` отправляет вызовы REST API демону `dockerd`; тот, кто может в него писать, управляет хостом. В CI Docker внутри задачи (job) получают либо **монтированием этого сокета** в контейнер задачи (Docker-out-of-Docker, DooD: сборки идут на демоне хоста), либо запуском отдельного привилегированного демона в сервисном контейнере (Docker-in-Docker, DinD: изолированный демон, больше изоляции, больше затрат).

## Основное
### Что такое docker.sock
- **Демон (`dockerd`)**: главный фоновый процесс, который управляет всеми объектами Docker: контейнерами, образами, volumes, сетями. Он ждёт команд.
- **Клиент (`docker`)**: CLI, в котором ты набираешь `docker run`, `docker ps`.
- Когда ты запускаешь `docker run nginx`, клиент не выполняет работу сам. Он (1) открывает соединение с демоном через файл сокета `/var/run/docker.sock`, (2) отправляет команду как HTTP REST-запрос по этому соединению (`POST /containers/create`, `POST /containers/{id}/start`), (3) демон обрабатывает запрос и выполняет действие (скачивает образ, запускает контейнер).
- **Протокол**: Unix domain socket (UDS) даёт быстрое локальное межпроцессное взаимодействие на одном хосте; по умолчанию `DOCKER_HOST=unix:///var/run/docker.sock`. Тот же API можно отдавать по TCP (`tcp://host:2376` с TLS, `2375` без шифрования) для удалённого доступа ([[linux-networking]]).
- **Безопасность**: доступ к сокету **равносилен root на хосте**. Через него кто угодно может запустить привилегированный контейнер, смонтировать `/` хоста, прочитать любой секрет, а демон работает от root. По умолчанию пользоваться им могут только root и члены группы `docker`, поэтому добавление пользователя в `docker` равносильно выдаче root ([[linux-basics]]). Rootless Docker и Podman снижают этот риск.
- `curl --unix-socket /var/run/docker.sock http://localhost/v1.45/containers/json` показывает API напрямую; тот же сокет можно пробросить в контейнер через `-v /var/run/docker.sock:/var/run/docker.sock` ([[docker-volumes-and-registry]]).

### Зачем CI нужен Docker внутри задачи
- GitLab Runner с Docker executor запускает каждую задачу внутри контейнера. Сборка образа, push в registry, запуск Testcontainers или `docker compose up` для интеграционных тестов требуют демона Docker изнутри этого контейнера задачи. Есть два способа его предоставить: DooD и DinD.

### DooD (Docker-out-of-Docker)
- Сокет хоста монтируется в контейнер задачи. Бинарник `docker` в задаче общается с демоном **хоста**; любой контейнер или образ, созданный pipeline, на самом деле живёт на хосте runner.
- Плюсы: быстро, общий кеш слоёв между задачами (кеш хоста), не нужен привилегированный режим, нет вложенных драйверов хранилища, Testcontainers работает из коробки.
- Минусы: задачи делят один демон: нет изоляции между pipeline, задача может увидеть или удалить контейнеры другой задачи, пути volumes — это пути хоста (`-v $PWD:/src` в задаче ссылается на файловую систему хоста, а не контейнера задачи), брошенные контейнеры и образы копятся на хосте, а у задачи есть root на runner. Подходит для доверенных внутренних runner.
- Конфигурация runner: `volumes = ["/var/run/docker.sock:/var/run/docker.sock", "/cache"]` в `config.toml`; в задаче `image: docker:27-cli`, `services: []`, при желании `DOCKER_HOST: unix:///var/run/docker.sock` для явности.

### DinD (Docker-in-Docker)
- Вместо монтирования сокета задача объявляет `services: [docker:27-dind]`: GitLab запускает sidecar-контейнер со **своим, чистым демоном**. Клиент `docker` в задаче общается с ним по TCP (`DOCKER_HOST: tcp://docker:2376` с TLS-сертификатами, которые передаются через volume, или `2375` с `DOCKER_TLS_CERTDIR: ""` без шифрования).
- Плюсы: полная изоляция: у pipeline свой демон, он не может трогать контейнеры хоста, и всё исчезает вместе с задачей. Чистые, воспроизводимые сборки.
- Минусы: сервису dind нужен `privileged = true` на runner (а это само по себе возможность, равносильная root на хосте, поэтому аргумент об изоляции слабее, чем кажется); вложенное хранилище overlay2 медленнее; кеш слоёв пуст в каждой задаче, если не добавить кеширование через registry (`--cache-from`, registry cache в BuildKit) или постоянный volume; Testcontainers нужны `DOCKER_HOST` и `TESTCONTAINERS_HOST_OVERRIDE=docker` плюс настройки `/certs`.
- Варианты: Kaniko и BuildKit rootless (`moby/buildkit:rootless`) собирают образы без демона и привилегированного режима: предпочтительный ответ, когда нужен только `docker build`.

### Выбор
| Потребность | Выбор |
|---|---|
| Только собрать и запушить образы | Kaniko / BuildKit rootless или DinD |
| Testcontainers или интеграционные тесты на Compose на внутреннем runner | DooD (проще и быстрее всего) |
| Недоверенные или многоарендные (multi-tenant) pipeline | DinD (или runner, изолированный в VM) |
| Деплой в Swarm из CI | CLI `docker` с `DOCKER_HOST=ssh://deploy@manager` или runner на manager с DooD ([[docker-swarm]]) |

## Пример
```yaml
# .gitlab-ci.yml: две задачи, по одной на каждый подход
stages: [test, build]

integration-tests:            # DooD: Testcontainers на демоне runner
  stage: test
  image: maven:3.9.9-eclipse-temurin-21
  variables:
    DOCKER_HOST: unix:///var/run/docker.sock     # сокет монтируется через config.toml runner
    TESTCONTAINERS_RYUK_DISABLED: "false"
  script:
    - mvn -B verify

build-image:                  # DinD: изолированный демон, результат пушится в registry
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
# /etc/gitlab-runner/config.toml (фрагмент)
[[runners]]
  executor = "docker"
  [runners.docker]
    privileged = true                                   # обязательно для сервиса dind
    volumes = ["/var/run/docker.sock:/var/run/docker.sock", "/certs/client", "/cache"]
```

## Подводные камни
- Монтирование `docker.sock` в любой прикладной контейнер (Portainer, Traefik, sidecar для «мониторинга») даёт этому контейнеру root на хосте; используй прокси для сокета (`tecnativa/docker-socket-proxy`), который разрешает только нужные endpoint для чтения.
- В DooD пути bind-монтирования в `docker run -v` внутри задачи разрешаются на **хосте**; вместо этого используй volumes или `docker cp`.
- DinD без кеша в registry или volume пересобирает все слои в каждой задаче; время CI взрывается.
- Смешивание обоих подходов: `services: [docker:dind]` плюс смонтированный сокет; клиент общается с тем, на который указывает `DOCKER_HOST`, а второй зря тратит ресурсы.
- Осиротевшие контейнеры от задач DooD (Testcontainers без Ryuk, отменённые pipeline) заполняют диск runner; запланируй `docker system prune -af --filter until=24h` ([[linux-memory-filesystems-io]]).
- Связанные заметки: [[docker-fundamentals]], [[docker-containers-and-resources]], [[docker-swarm]].
