---
title: Kubernetes basics for a Spring Boot service
tags: [kubernetes, devops, containers, spring-boot, jvm]
learned: 2026-10-27
---
## TL;DR
Kubernetes runs containers as **pods**, keeps the desired number alive through a **Deployment** (which owns a **ReplicaSet**), gives them a stable address through a **Service**, and exposes HTTP to the outside through an **Ingress**. You declare the desired state in YAML; controllers reconcile reality toward it. For a Spring Boot app the parts that matter most are the three **probes** wired to Actuator, CPU/memory **requests and limits** that the JVM must respect, and a rolling update that only shifts traffic to pods that report ready.

## Key points

### Objects
- **Pod**: one or more containers sharing network namespace and volumes; ephemeral, gets a new IP on every restart. You almost never create pods directly.
- **ReplicaSet**: keeps N identical pods running. **Deployment** manages ReplicaSets and performs rolling updates/rollbacks (`kubectl rollout undo`). StatefulSet for things with identity and storage (Postgres, Kafka), DaemonSet for one pod per node (OTel Collector, log agent), Job/CronJob for batch.
- **Service**: stable virtual IP + DNS name (`esim.prod.svc.cluster.local`) that load-balances across pods selected by labels. Types: **ClusterIP** (internal only, default), **NodePort** (opens a port on every node, 30000–32767), **LoadBalancer** (asks the cloud for an external LB), **ExternalName** (DNS alias). Service discovery is just DNS ([[api-gateway-and-service-discovery]]).
- **Ingress**: L7 HTTP routing (host/path → Service) handled by an ingress controller (NGINX, Traefik); TLS termination with a cert from cert-manager. The newer **Gateway API** (GA since 1.26+ for core resources) is replacing Ingress for advanced routing.
- **ConfigMap** / **Secret**: configuration as env vars or mounted files; Secrets are only base64, so encrypt at rest or use an external secret store. Spring Boot reads them via env/`spring.config.import=optional:configtree:/etc/config/` ([[spring-boot-properties-and-profiles]]).
- **Namespace**: logical partition (`dev`, `prod`, per team) for names, RBAC, ResourceQuota and NetworkPolicy.

### Probes with Spring Boot Actuator
- **Startup** probe: gates the other probes until the app has started; use it for slow JVM starts instead of a huge `initialDelaySeconds`.
- **Liveness** (`/actuator/health/liveness`): "is the process alive"; failure → container restart. Should *not* include DB checks, or a DB outage restarts every pod in a loop.
- **Readiness** (`/actuator/health/readiness`): "can it take traffic"; failure → pod removed from Service endpoints, no restart. Include dependencies you cannot work without.
- Enable with `management.endpoint.health.probes.enabled=true` (automatic when Boot detects Kubernetes) and `management.endpoint.health.group.readiness.include=readinessState,db`. Graceful shutdown: `server.shutdown=graceful` + `preStop` sleep so in-flight requests finish while the endpoint is removed.

### Resources and the JVM
- `requests` = what the scheduler reserves (and the pod's QoS class); `limits` = hard cap. Memory over limit → OOMKilled (exit 137); CPU over limit → throttled.
- JVM 11+ (and 8u191+) is container-aware: it reads the cgroup limit, so `-XX:MaxRAMPercentage=75` sizes the heap from the container limit instead of the node's RAM. Leave 25–35 % for metaspace, threads, native, code cache. Java 21 handles cgroup v2 correctly; set `-XX:+UseContainerSupport` (default on).
- CPU limit affects `availableProcessors()` → GC threads, common pool, Tomcat threads. A limit of `500m` gives one processor and a slow start; many teams set CPU requests but no CPU limit.
- `kubectl top pod`, `jcmd <pid> VM.native_memory` and the `jvm_memory_*` Micrometer metrics tell you where memory goes ([[jvm-memory-areas]], [[garbage-collection]], [[docker-containers-and-resources]]).

### Rolling update and scaling
- `strategy: RollingUpdate`, `maxSurge: 1`, `maxUnavailable: 0`: new pod comes up, passes readiness, old pod is terminated. A broken image never gets traffic, and `kubectl rollout undo` goes back.
- **HPA** (HorizontalPodAutoscaler) scales replicas on CPU/memory (metrics-server) or custom metrics (Prometheus adapter: Kafka consumer lag, requests per second). Needs `requests` set. KEDA scales on event sources (Kafka lag to zero). VPA adjusts requests; PodDisruptionBudget keeps a minimum during node drains.

### kubectl essentials
```bash
kubectl get pods -n prod -o wide
kubectl describe pod esim-7d9f-abc -n prod         # events: OOMKilled, ImagePullBackOff, probe failures
kubectl logs -f deploy/esim -n prod --since=10m
kubectl exec -it deploy/esim -n prod -- sh
kubectl port-forward svc/esim 8080:80 -n prod
kubectl apply -f deployment.yaml && kubectl rollout status deploy/esim -n prod
kubectl rollout undo deploy/esim -n prod
kubectl scale deploy/esim --replicas=4 -n prod
kubectl get events -n prod --sort-by=.lastTimestamp
```

### Helm
Helm packages a set of templated manifests as a **chart** with `values.yaml`; `helm install esim ./chart -f values-prod.yaml` renders and applies them, `helm upgrade` / `helm rollback` manage releases. It is the de-facto way to install third-party stacks (Kafka, Grafana, Tempo, Loki) and to keep one chart per service with per-environment values; Kustomize is the overlay-based alternative built into `kubectl -k`.

### Compared with Docker Swarm
| | Swarm ([[docker-swarm]], [[docker-swarm-stack-deploy]]) | Kubernetes |
|---|---|---|
| Unit | Service with replicas, tasks | Pod, Deployment, ReplicaSet |
| Networking | Overlay + routing mesh, DNS per service | CNI plugin, Service, Ingress, NetworkPolicy |
| Config | `docker config`/`secret` | ConfigMap/Secret, plus operators, CRDs |
| Health | Container HEALTHCHECK only | Three probe types, per-probe actions |
| Autoscaling | None built in | HPA/VPA/KEDA |
| Ecosystem | Small, simple, compose-file compatible | Huge, Helm, operators, managed offerings |
Swarm is fine for a handful of nodes run by a small team; Kubernetes pays off with many services, autoscaling, and standard tooling.

## Example
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
            failureThreshold: 24          # up to 2 min to start
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

## Pitfalls
- Liveness probe that checks the database: one DB hiccup restarts the whole fleet, which then hammers the DB on startup. Keep liveness trivial.
- Memory limit without `MaxRAMPercentage` and a `-Xmx` copied from a VM: the heap alone exceeds the limit → OOMKilled with no Java `OutOfMemoryError` in the logs.
- `latest` image tag + `imagePullPolicy: IfNotPresent`: nodes run different versions and rollbacks are impossible. Pin tags.
- No `requests` → HPA cannot compute utilisation and the scheduler packs pods badly; no PodDisruptionBudget → a node upgrade takes all replicas down at once.
- Secrets in Git in plain YAML: use sealed-secrets, SOPS or an external secrets operator.
- Related: [[docker-fundamentals]], [[dockerfile-and-images]], [[docker-swarm]], [[docker-containers-and-resources]], [[distributed-tracing]] (collector as DaemonSet), [[api-gateway-and-service-discovery]], [[devops-resources]].
