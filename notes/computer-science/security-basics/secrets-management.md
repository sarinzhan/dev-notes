---
title: Secrets management
tags: [security, secrets, vault, kubernetes, configuration]
learned: 2026-10-27
---
## TL;DR
A secret is anything whose disclosure gives access: DB passwords, API keys, signing keys, TLS private keys, OAuth client secrets, webhook secrets. They must never be in git, in a Docker image, in `application.yml` committed to the repo, or in logs. The ladder of maturity: environment variables injected at deploy time → Kubernetes/Docker secrets mounted as files → a secret manager (HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager, Azure Key Vault) that the app reads at start or at runtime, with **short-lived, rotated** credentials and an audit trail. Spring Boot reads all of these as ordinary properties ([[spring-boot-properties-and-profiles]]), so the application code does not change between stages. Cryptographic use of keys is in [[symmetric-and-asymmetric-crypto]].

## Key points
### Where secrets must not be
- **Git**: history is forever; a secret committed once and "removed" in the next commit is still in every clone and in the GitLab UI. If it happened, rotate the secret first, then rewrite history. Pre-commit scanners (gitleaks, GitLab Secret Detection, `detect-secrets`) catch the common patterns (`AKIA...`, `-----BEGIN PRIVATE KEY-----`, `password=`).
- **Docker images**: a `COPY .env` or an `ENV DB_PASSWORD=` layer is readable with `docker history` and from any registry pull; build-time secrets go through `--mount=type=secret` ([[dockerfile-and-images]]).
- **Logs, traces, error trackers**: a `toString()` of a config object, a logged `DataSource` URL with the password, an `Authorization` header in a request log, a heap dump from actuator ([[owasp-top-10-for-backend]]).
- **Client-side**: anything shipped to a browser or a mobile app is public, including "obfuscated" API keys.

### Delivery mechanisms
| Mechanism | How the app gets it | Rotation | Audit | Fits |
|---|---|---|---|---|
| Environment variables | `${DB_PASSWORD}` in `application.yml`, set by CI/compose/K8s | redeploy | none | small setups, 12-factor apps |
| Mounted secret files | K8s `Secret` → volume → `spring.config.import=configtree:/etc/secrets/` | update the Secret, app re-reads or restarts | K8s audit log | Kubernetes default |
| Secret manager (Vault, cloud SM) | SDK or Spring Cloud Vault/AWS at startup, or agent sidecar writing files | dynamic secrets with TTL, automatic | full | production, compliance |
| Sealed/External Secrets | encrypted manifests in git (SOPS, Sealed Secrets) or an operator syncing from the SM into K8s Secrets | via the SM | via the SM | GitOps |

- Env vars are visible to every process in the container (`/proc/<pid>/environ`), get inherited by child processes and often end up in crash dumps. Files with `0400` permissions are a step up; a secret manager with per-service identity is the real answer.
- Kubernetes `Secret` objects are only base64-encoded; enable encryption at rest in etcd and RBAC-limit who can `get secrets` ([[kubernetes-basics]]). Prefer projecting as files over `envFrom`.
- Docker Compose on a single host: `secrets:` top-level block mounts files under `/run/secrets/`; Docker Swarm encrypts them in the Raft log ([[docker-swarm]]).

### Identity instead of static credentials
- The strongest pattern is no long-lived secret at all: the workload proves *who it is* (Kubernetes service account token, cloud IAM role, SPIFFE certificate, [[mtls-and-service-to-service-auth]]) and the secret manager hands out a **dynamic credential**: a Postgres role created on the fly with a 1-hour TTL, a cloud token valid for 15 minutes.
- Rotation then becomes routine, not an incident. For static secrets, rotation must be designed in: two valid keys at once (old and new), consumers reload without restart, the old key is revoked after a grace period. Spring Cloud's `@RefreshScope`, HikariCP's `setPassword` on the running pool, or a JWKS endpoint with multiple `kid`s ([[jwt-authentication]]) are the application-side hooks.
- Bootstrap problem ("secret zero"): the app needs a credential to talk to Vault. Solve it with the platform identity (K8s auth method, AWS IAM auth), not with another static token.

### Operational rules
- One secret per purpose and per environment; staging never shares a key with production.
- Least privilege: the app's DB role owns only its schema; the CI token can push images but not read production secrets.
- Audit and alert on reads of production secrets outside deploy windows.
- Incident playbook: rotate first, investigate second; keep an inventory of where each secret is used so rotation is a checklist, not archaeology.
- Local development: `.env` files in `.gitignore`, a `application-local.yml` template with placeholders, or Testcontainers so developers never hold real credentials ([[testcontainers]]).

## Example
Kubernetes secret projected as files, read by Spring Boot's config tree, with the datasource password rotated without changing the image.
```yaml
# k8s: secret created by External Secrets Operator from Vault, never written by hand
apiVersion: v1
kind: Secret
metadata:
  name: esim-api-secrets
type: Opaque
stringData:                      # shown for clarity; in git this is an ExternalSecret, not the values
  db-password: "..."
  smdp-api-key: "..."
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: esim-api
spec:
  template:
    spec:
      serviceAccountName: esim-api          # identity used for Vault/K8s auth, no static token
      containers:
        - name: app
          image: registry.example/esim-api:2026.10.27
          env:
            - name: SPRING_CONFIG_IMPORT
              value: "optional:configtree:/etc/secrets/"
          volumeMounts:
            - name: secrets
              mountPath: /etc/secrets
              readOnly: true
      volumes:
        - name: secrets
          secret:
            secretName: esim-api-secrets
            defaultMode: 0400
```
```yaml
# application.yml in the repo: no values, only references
spring:
  datasource:
    url: jdbc:postgresql://${DB_HOST}:5432/esim
    username: esim_api
    password: ${db-password}        # file /etc/secrets/db-password via configtree
smdp:
  api-key: ${smdp-api-key}
management:
  endpoints:
    web:
      exposure:
        include: health,prometheus   # never env, configprops or heapdump in production
```
With `configtree:` each file name becomes a property; the same `application.yml` runs locally with `DB_PASSWORD` from a `.env` and in production from the mounted Secret.

## Pitfalls
- `application-prod.yml` with real passwords "because it is a private repo": every developer, every CI runner and every future contractor has it; private is not secret.
- Logging the resolved configuration at startup (`Environment` dump, `/actuator/env`, `--debug`), or a `@ConfigurationProperties` class with a generated `toString()` that includes the key.
- Secrets in CI variables without `masked`/`protected` flags, or printed by a `set -x` in a pipeline script.
- Rotation that requires a restart of every consumer simultaneously; design for two valid versions at once or you will never rotate.
- Treating a `kid`-less, never-rotated HMAC key for JWTs as acceptable because "it is in Vault": storage is only half of the job; lifecycle is the other half.
- Related: [[spring-boot-properties-and-profiles]] for the property-source order that makes injection work, [[api-security-checklist]] for how keys are presented and validated at the API edge.
