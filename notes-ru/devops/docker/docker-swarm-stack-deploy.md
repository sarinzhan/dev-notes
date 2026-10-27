---
title: Stack-файл Swarm: справочник по deploy и healthcheck
tags: [docker, swarm, stack-file, rolling-update, healthcheck]
learned: 2026-10-22
---
## Кратко
Stack-файл — это Compose-файл плюс блок `deploy`, который читает только Swarm: режим и число реплик, политика rolling update и отката (rollback), политика перезапуска, ограничения и предпочтения размещения, лимиты и резервирование ресурсов. Вместе с `healthcheck` он определяет, как сервис выкатывается, где работает, как заменяется при поломке и какую часть узла может занять. `docker stack deploy -c stack.yml <name>` применяет его; основы кластера — в [[docker-swarm]].

## Основное
### Ключи `healthcheck` и `deploy`
- **healthcheck**: без него Docker считает контейнер готовым сразу после запуска и замечает сбой, только когда процесс завершается с ненулевым кодом. С ним статус показывает `starting`, `healthy` или `unhealthy`; `FailingStreak` в `docker inspect` увеличивается при каждом ненулевом коде выхода команды и сбрасывается при успехе; неудачи в течение `start_period` не учитываются; после `retries` неудач подряд задача становится `unhealthy`, и Swarm её **заменяет**. Подробности в [[docker-containers-and-resources]]; механика та же, разница в том, что Swarm действует на основе результата.
- **deploy.mode**: `replicated` (задаётся `replicas: N`) или `global` (ровно одна задача на узел: отправщики логов, node exporters, см. [[logging-stacks]]).
- **deploy.replicas**: число копий; только в режиме replicated.
- **deploy.labels**: метаданные сервиса для маршрутизации, мониторинга (`traefik.enable=true`).
- **deploy.update_config** (rolling update без простоя): `parallelism` — сколько задач обновляется одновременно; `delay` — пауза между пачками; `failure_action` `continue | pause | rollback`; `monitor` — сколько наблюдать за новой задачей после старта, если она переживает это окно, обновление задачи считается успешным; `max_failure_ratio` 0–1 — допустимая доля неудачных обновлений задач, `0` запускает `failure_action` при первой же неудаче; `order` `start-first` (новая задача поднимается до остановки старой, без простоя, нужны свободные ресурсы) или `stop-first`.
- **deploy.rollback_config**: те же поля, применяются при откате (автоматически при `failure_action: rollback` или вручную через `docker service rollback`).
- **deploy.restart_policy**: `condition` `none | on-failure | any`; `delay` перед перезапуском; `max_attempts` — сколько раз; `window` — период, в течение которого считаются сбои.
- **deploy.placement**: `constraints` — жёсткие правила (`node.role == manager`, `node.labels.disk == ssd`, `node.hostname != node01`, `node.labels.region == eu`); `preferences` — мягкие (`spread: node.labels.zone` равномерно распределяет задачи по зонам). Типичные применения: держать базу данных на manager вместе с её volume, разносить backend и frontend по разным узлам.
- **deploy.resources**: `limits` (максимум CPU и памяти, который может использовать задача, обеспечивается cgroups) и `reservations` (минимальный гарантированный объём, который планировщик должен найти на узле перед размещением задачи). Резервирование позволяет Swarm разумно распределять нагрузку; лимиты защищают соседей.

### Как читать результат
- `docker stack ps esim --no-trunc` показывает каждую задачу с её узлом, желаемым и текущим состоянием и ошибкой упавшей задачи; `docker service inspect --pretty esim_api` выводит действующую конфигурацию обновления, отката и размещения.
- `docker service update --rollback` или `docker service rollback` применяет `rollback_config`; `docker service update --force` перезапускает все задачи с той же спецификацией (полезно после ротации config или secret).
- Secrets и configs, на которые ссылается стек, должны существовать (`external: true`) или создаваться из файлов в стеке; и те и другие неизменяемы, ротация — через новое имя (`db_pass_v2`) и обновление ссылки ([[docker-volumes-and-registry]]).

## Пример
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
        constraints: ["node.labels.db == true"]   # узел, которому принадлежит volume

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

## Подводные камни
- `order: start-first` при `replicas: 1` и отсутствии свободной памяти на узле проваливает обновление, потому что новая задача должна поместиться рядом со старой; оставь запас или используй `stop-first`.
- `max_failure_ratio: 0` плюс `monitor: 30s` и JVM, которой нужно 45 с, чтобы стать healthy, означают, что каждое обновление «проваливается» и откатывается; согласуй `monitor` со `start_period`.
- `restart_policy.condition: any` без `max_attempts` бесконечно перезапускает падающую в цикле задачу и прячет баг за «он снова Up»; задай `max_attempts` и настрой алерты по `docker events`.
- Ограничения, которым не удовлетворяет ни один узел, оставляют задачи в `pending` с «no suitable node»; проверь `docker service ps` и метки узлов.
- `resources.limits` обеспечиваются cgroups так же, как `docker run --memory` ([[docker-containers-and-resources]]), поэтому Spring Boot приложению всё равно нужно настроить `-XX:MaxRAMPercentage` под лимит.
- Связанные заметки: [[docker-swarm]], [[dockerfile-and-images]] (`HEALTHCHECK`, встроенный в образ), [[logging-stacks]] (global-сервисы для отправщиков логов).
