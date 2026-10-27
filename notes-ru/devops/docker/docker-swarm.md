---
title: Docker Swarm
tags: [docker, swarm, orchestration, deploy, overlay-network]
learned: 2026-10-22
---
## Кратко
Docker Swarm — встроенный оркестратор Docker: он объединяет несколько Docker-хостов в один кластер, запускает приложения как **сервисы (services)**, состоящие из реплицированных **задач (tasks)** — контейнеров, балансирует трафик между ними, перезапускает упавшие, постепенно выкатывает обновления и хранит секреты. Желаемое состояние описывается в stack-файле формата Compose ([[docker-swarm-stack-deploy]]), а `docker stack deploy` приводит кластер к нему. Проще Kubernetes, достаточно для нескольких узлов.

## Основное
### Возможности
- Кластеризация (несколько хостов, один логический кластер), оркестрация сервисов через декларативные описания, масштабирование числом реплик, встроенная балансировка нагрузки, высокая доступность с автоматическим переносом задач с мёртвого узла, декларативное управление через stack-файлы (формат Compose) или CLI.

### Основные понятия
- **Узел (node)**: один Docker-хост в кластере. **Managers** хранят состояние кластера, планируют задачи и обслуживают API; **workers** только выполняют задачи. Managers тоже выполняют задачи, если их не перевели в drain.
- **Консенсус Raft**: managers реплицируют состояние через Raft; для изменений должно работать большинство (кворум). Запускай 3 или 5 managers (переживают 1 или 2 отказа); чётное число ничего не добавляет. Потеря кворума замораживает планирование; работающие задачи продолжают работать. Для аварийного восстановления делай бэкап `/var/lib/docker/swarm`.
- **Сервис (service)**: абстракция более высокого уровня, определяющая, как контейнеры разворачиваются, управляются и масштабируются в swarm: образ, число реплик, сети, порты, политика обновления и перезапуска, размещение, ресурсы.
- **Задача (task)**: один экземпляр контейнера, созданный и управляемый сервисом, единица планирования; она размещается на одном узле и является реально работающим контейнером, который выполняет спецификацию сервиса. Мёртвая задача не перезапускается на месте: планировщик создаёт новую, а старая остаётся в истории `docker service ps`.
- **Overlay-сеть**: виртуальная сеть VXLAN, охватывающая хосты, так что контейнеры на разных узлах общаются по имени сервиса, как будто они в одном bridge.
- Сервисы и контейнеры: `docker run` создаёт контейнер, привязанный к одному хосту; `docker service create` создаёт желаемое состояние, которое swarm поддерживает где угодно.

### Жизненный цикл кластера (CLI)
- `docker swarm init --advertise-addr 10.0.0.1` на первом manager; команда печатает `docker swarm join --token <worker-token> 10.0.0.1:2377`. `docker swarm join-token manager` — токен для manager.
- `docker node ls`, `docker node update --label-add region=eu node02`, `docker node promote|demote`, `docker node rm`.
- `docker service create|ls|ps|logs|inspect|scale|update|rollback|rm` управляют сервисами (см. Пример).
- `docker stack deploy -c stack.yml esim`, `docker stack services esim`, `docker stack ps esim`, `docker stack rm esim`.
- Порты узлов: 2377/tcp (управление), 7946/tcp+udp (gossip), 4789/udp (VXLAN) ([[linux-networking]]).

### Сети и балансировка нагрузки
- **Overlay и bridge**: bridge-сети существуют в пределах хоста; overlay-сети охватывают кластер (опционально с шифрованием через `--opt encrypted`). Сервис может быть подключён к нескольким overlay, например `frontend` и `backend`.
- **Ingress-сеть**: специальная overlay-сеть плюс **routing mesh**: опубликованный порт (`-p 8080:8080`) открыт на *каждом* узле; запрос, пришедший на любой узел, перенаправляется (IPVS) на здоровую задачу где угодно. `mode: host` публикует порт только на узле, где работает задача (нужно в некоторых случаях с UDP или при сохранении IP клиента).
- **VIP и DNS round robin**: по умолчанию каждый сервис получает виртуальный IP; встроенный DNS разрешает имя сервиса в VIP, а IPVS балансирует соединения между задачами. `endpoint_mode: dnsrr` вместо этого возвращает IP всех задач — для клиентов, которые балансируют сами, или для stateful-наборов. При `dnsrr` JVM не должна кешировать DNS навсегда.
- Обычно перед mesh стоит reverse proxy (Traefik, nginx) для TLS и маршрутизации по хосту; Traefik читает метки сервисов.

### Хранилище
- Записываемый слой контейнера и локальные volumes остаются на узле, где работала задача; после переноса на другой узел данных там нет ([[docker-volumes-and-registry]]).
- Варианты: закрепить stateful-сервисы за одним узлом ограничением размещения и локальным volume; общее хранилище (NFS, GlusterFS, Ceph), смонтированное на каждом узле или через volume-плагин; или держать настоящие базы данных вне swarm. Бэкап — через tar внутри контейнера плюс логические дампы.

### Безопасность, secrets и configs
- Трафик между узлами идёт через взаимный TLS (mutual TLS); корневой CA создаётся при `swarm init`, сертификаты ротируются автоматически, join-токены можно ротировать (`docker swarm join-token --rotate worker`).
- **Secrets** (`docker secret create db_pass -`): хранятся зашифрованными в журнале Raft, доставляются только задачам, которые их используют, монтируются как файлы в памяти в `/run/secrets/<name>`; никогда не видны в `docker inspect` или переменных окружения. Spring Boot читает их через `spring.config.import=configtree:/run/secrets/` ([[spring-boot-properties-and-profiles]]).
- **Configs** (`docker config create`): тот же механизм для нечувствительных файлов (`application.yml`, конфиг nginx). И те и другие неизменяемы; ротация — через новое имя.
- В самом Swarm нет RBAC: доступ к Docker API manager — это полный контроль ([[docker-socket-dind-dood]]); защищай сокет, закрывай API через TLS, если он открыт наружу, ограничивай порты swarm файрволом до подсети узлов.

### CI/CD и эксплуатация
- Pipeline: собрать и запушить образ ([[dockerfile-and-images]]), затем на manager выполнить `docker stack deploy -c stack.yml --with-registry-auth esim` с новым тегом; Swarm делает rolling update. GitLab CI запускает это по SSH или с runner на manager.
- Canary: второй сервис с 1 репликой за весом в Traefik или `update_config` с `parallelism: 1` и окном `monitor` ([[docker-swarm-stack-deploy]]).
- Мониторинг: `docker service ps --no-trunc`, `docker events`, cAdvisor/node-exporter и отправщик логов как global-сервисы ([[logging-stacks]]).
- Swarm и Kubernetes: Swarm встроен, синтаксис Compose, настраивается за минуты, хорош для нескольких узлов. У Kubernetes гораздо более богатая экосистема (операторы, автомасштабирование, CRD, ingress, сетевые политики, StatefulSets), крутая кривая обучения, и это отраслевой стандарт для крупных платформ. Выбирай Swarm для небольших команд и простоты on-prem; переходи, когда понадобится то, чего в нём нет.

## Пример
```bash
# Поднять кластер из 3 узлов и запустить сервис с rolling update
docker swarm init --advertise-addr 10.0.0.1                # на node01 (manager)
docker swarm join --token SWMTKN-1-... 10.0.0.1:2377        # на node02, node03 (workers)
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

docker service ps api                                      # на каком узле работает каждая задача
docker service scale api=6
docker service update --image registry.gitlab.com/beeline/esim/api:1.4.3 api   # rolling update
docker service rollback api                                # если новая версия ведёт себя плохо
docker node update --availability drain node03             # задачи уезжают; обслуживание
```

## Подводные камни
- Два manager хуже одного: потеря любого из них означает потерю кворума. Используй 1, 3 или 5.
- `docker stack deploy` игнорирует `build:`, порядок `depends_on` и `container_name`; образы должны лежать в registry, доступном каждому узлу, отсюда `--with-registry-auth`.
- Перенесённая stateful-задача попадает на узел без своего volume и стартует пустой; ограничь её размещение или используй общее хранилище.
- Routing mesh скрывает IP клиента от приложения; используй `mode: host` или доверяй `X-Forwarded-For` от прокси.
- Health checks, обращающиеся к базе данных, при сбое БД перезапускают все реплики разом; слишком короткий `start_period` вызывает циклы перезапуска медленно стартующих JVM ([[docker-swarm-stack-deploy]]).
- Связанные заметки: [[docker-swarm-stack-deploy]] (справочник по stack-файлу), [[docker-containers-and-resources]], [[docker-volumes-and-registry]], [[docker-socket-dind-dood]], [[linux-networking]].
