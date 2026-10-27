---
title: Основы Kubernetes для Spring Boot сервиса
tags: [kubernetes, devops, containers, spring-boot, jvm]
learned: 2026-10-27
---
## Кратко
Kubernetes запускает контейнеры как **pods**, поддерживает нужное их количество через **Deployment** (которому принадлежит **ReplicaSet**), даёт им стабильный адрес через **Service** и открывает HTTP наружу через **Ingress**. Желаемое состояние описывается в YAML; контроллеры приводят реальность к нему (reconcile). Для Spring Boot приложения важнее всего три **пробы (probes)**, подключённые к Actuator, **requests и limits** по CPU и памяти, которые должна учитывать JVM, и rolling update, переключающий трафик только на pods, сообщившие о готовности.

## Основное

### Объекты
- **Pod**: один или несколько контейнеров с общим сетевым пространством имён и volumes; эфемерен, получает новый IP при каждом перезапуске. Напрямую pods почти никогда не создают.
- **ReplicaSet**: поддерживает N одинаковых pods. **Deployment** управляет ReplicaSet и выполняет rolling updates и откаты (`kubectl rollout undo`). StatefulSet — для того, у чего есть идентичность и хранилище (Postgres, Kafka), DaemonSet — по одному pod на узел (OTel Collector, агент логов), Job/CronJob — для пакетных задач.
- **Service**: стабильный виртуальный IP + DNS-имя (`esim.prod.svc.cluster.local`), балансирующий нагрузку между pods, выбранными по меткам. Типы: **ClusterIP** (только внутри кластера, по умолчанию), **NodePort** (открывает порт на каждом узле, 30000–32767), **LoadBalancer** (запрашивает у облака внешний LB), **ExternalName** (DNS-псевдоним). Обнаружение сервисов (service discovery) — это просто DNS ([[api-gateway-and-service-discovery]]).
- **Ingress**: маршрутизация HTTP на уровне L7 (host/path → Service), которую выполняет ingress-контроллер (NGINX, Traefik); терминирование TLS с сертификатом от cert-manager. Более новый **Gateway API** (GA с 1.26+ для основных ресурсов) заменяет Ingress для продвинутой маршрутизации.
- **ConfigMap** / **Secret**: конфигурация в виде переменных окружения или смонтированных файлов; Secrets — это всего лишь base64, поэтому шифруй их при хранении или используй внешнее хранилище секретов. Spring Boot читает их через env/`spring.config.import=optional:configtree:/etc/config/` ([[spring-boot-properties-and-profiles]]).
- **Namespace**: логический раздел (`dev`, `prod`, по командам) для имён, RBAC, ResourceQuota и NetworkPolicy.

### Пробы со Spring Boot Actuator
- **Startup**-проба: блокирует остальные пробы, пока приложение не стартовало; используй её для медленного старта JVM вместо огромного `initialDelaySeconds`.
- **Liveness** (`/actuator/health/liveness`): «жив ли процесс»; неудача → перезапуск контейнера. *Не* должна включать проверки БД, иначе сбой БД будет в цикле перезапускать все pods.
- **Readiness** (`/actuator/health/readiness`): «может ли принимать трафик»; неудача → pod убирается из endpoints Service, без перезапуска. Включай зависимости, без которых работать невозможно.
- Включается через `management.endpoint.health.probes.enabled=true` (автоматически, когда Boot обнаруживает Kubernetes) и `management.endpoint.health.group.readiness.include=readinessState,db`. Graceful shutdown: `server.shutdown=graceful` + sleep в `preStop`, чтобы текущие запросы завершились, пока endpoint удаляется.

### Ресурсы и JVM
- `requests` = то, что резервирует планировщик (и класс QoS pod); `limits` = жёсткий потолок. Память сверх лимита → OOMKilled (выход 137); CPU сверх лимита → троттлинг.
- JVM 11+ (и 8u191+) учитывает контейнер: она читает лимит cgroup, поэтому `-XX:MaxRAMPercentage=75` задаёт размер кучи от лимита контейнера, а не от RAM узла. Оставь 25–35 % на metaspace, потоки, нативную память, code cache. Java 21 корректно работает с cgroup v2; задай `-XX:+UseContainerSupport` (включено по умолчанию).
- Лимит CPU влияет на `availableProcessors()` → потоки GC, common pool, потоки Tomcat. Лимит `500m` даёт один процессор и медленный старт; многие команды задают requests по CPU, но не задают limit по CPU.
- `kubectl top pod`, `jcmd <pid> VM.native_memory` и метрики Micrometer `jvm_memory_*` показывают, куда уходит память ([[jvm-memory-areas]], [[garbage-collection]], [[docker-containers-and-resources]]).

### Rolling update и масштабирование
- `strategy: RollingUpdate`, `maxSurge: 1`, `maxUnavailable: 0`: новый pod поднимается, проходит readiness, старый pod завершается. Сломанный образ никогда не получает трафик, а `kubectl rollout undo` возвращает назад.
- **HPA** (HorizontalPodAutoscaler) масштабирует число реплик по CPU/памяти (metrics-server) или по пользовательским метрикам (Prometheus adapter: лаг Kafka consumer, запросы в секунду). Требует заданных `requests`. KEDA масштабирует по источникам событий (лаг Kafka вплоть до нуля). VPA подстраивает requests; PodDisruptionBudget сохраняет минимум во время drain узлов.

### Основы kubectl
```bash
kubectl get pods -n prod -o wide
kubectl describe pod esim-7d9f-abc -n prod         # события: OOMKilled, ImagePullBackOff, неудачи проб
kubectl logs -f deploy/esim -n prod --since=10m
kubectl exec -it deploy/esim -n prod -- sh
kubectl port-forward svc/esim 8080:80 -n prod
kubectl apply -f deployment.yaml && kubectl rollout status deploy/esim -n prod
kubectl rollout undo deploy/esim -n prod
kubectl scale deploy/esim --replicas=4 -n prod
kubectl get events -n prod --sort-by=.lastTimestamp
```

### Helm
Helm упаковывает набор шаблонизированных манифестов в **chart** с `values.yaml`; `helm install esim ./chart -f values-prod.yaml` рендерит и применяет их, `helm upgrade` / `helm rollback` управляют релизами. Это фактический стандарт установки сторонних стеков (Kafka, Grafana, Tempo, Loki) и способ держать один chart на сервис со значениями для каждого окружения; Kustomize — альтернатива на основе overlays, встроенная в `kubectl -k`.

### Сравнение с Docker Swarm
| | Swarm ([[docker-swarm]], [[docker-swarm-stack-deploy]]) | Kubernetes |
|---|---|---|
| Единица | Service с репликами, tasks | Pod, Deployment, ReplicaSet |
| Сеть | Overlay + routing mesh, DNS на сервис | CNI-плагин, Service, Ingress, NetworkPolicy |
| Конфигурация | `docker config`/`secret` | ConfigMap/Secret, плюс операторы, CRD |
| Здоровье | Только HEALTHCHECK контейнера | Три типа проб, у каждой свои действия |
| Автомасштабирование | Встроенного нет | HPA/VPA/KEDA |
| Экосистема | Маленькая, простая, совместима с compose-файлами | Огромная, Helm, операторы, управляемые сервисы |
Swarm подходит для нескольких узлов, которые обслуживает небольшая команда; Kubernetes окупается при большом числе сервисов, автомасштабировании и стандартных инструментах.

## Пример
```yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: esim-provisioning, namespace: prod, labels: { app: esim-provisioning } }
spec:
  replicas: 3
  selector: { matchLabels: { app: esim-provisioning } }
  strategy: { type: RollingUpdate, rollingUpdate: { maxSurge: 1, maxUnavailable: 0 } }
  template:
    metadata: { labels: { app: esim-provisioning } }
    spec:
      terminationGracePeriodSeconds: 40
      containers:
        - name: app
          image: registry.example.kg/esim-provisioning:1.42.0
          ports: [{ containerPort: 8080, name: http }]
          env:
            - { name: SPRING_PROFILES_ACTIVE, value: prod }
            - { name: JAVA_TOOL_OPTIONS, value: "-XX:MaxRAMPercentage=75 -XX:+UseG1GC -Djava.security.egd=file:/dev/./urandom" }
            - name: DB_PASSWORD
              valueFrom: { secretKeyRef: { name: esim-db, key: password } }
          envFrom:
            - configMapRef: { name: esim-config }
          resources:
            requests: { cpu: "500m", memory: "768Mi" }
            limits: { memory: "1Gi" }
          startupProbe:
            httpGet: { path: /actuator/health/liveness, port: http }
            periodSeconds: 5
            failureThreshold: 24          # до 2 минут на старт
          livenessProbe:
            httpGet: { path: /actuator/health/liveness, port: http }
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet: { path: /actuator/health/readiness, port: http }
            periodSeconds: 5
            failureThreshold: 3
          lifecycle:
            preStop: { exec: { command: ["sh", "-c", "sleep 5"] } }
---
apiVersion: v1
kind: Service
metadata: { name: esim-provisioning, namespace: prod }
spec:
  selector: { app: esim-provisioning }
  ports: [{ port: 80, targetPort: http }]
```

## Подводные камни
- Liveness-проба, проверяющая базу данных: один сбой БД перезапускает весь парк, который затем при старте заваливает БД запросами. Держи liveness тривиальной.
- Лимит памяти без `MaxRAMPercentage` и с `-Xmx`, скопированным с VM: одна только куча превышает лимит → OOMKilled без Java `OutOfMemoryError` в логах.
- Тег образа `latest` + `imagePullPolicy: IfNotPresent`: на узлах работают разные версии, а откаты невозможны. Фиксируй теги.
- Нет `requests` → HPA не может вычислить загрузку, а планировщик плохо раскладывает pods; нет PodDisruptionBudget → обновление узла выключает все реплики разом.
- Secrets в Git в виде обычного YAML: используй sealed-secrets, SOPS или external secrets operator.
- Связанные заметки: [[docker-fundamentals]], [[dockerfile-and-images]], [[docker-swarm]], [[docker-containers-and-resources]], [[distributed-tracing]] (collector как DaemonSet), [[api-gateway-and-service-discovery]], [[devops-resources]].
