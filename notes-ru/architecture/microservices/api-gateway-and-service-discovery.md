---
title: API gateway, обнаружение сервисов и сервер конфигурации
tags: [microservices, spring-cloud, networking, kubernetes]
learned: 2026-10-27
---
## Кратко
**API gateway** — единая точка входа для клиентов: он маршрутизирует запросы во внутренние сервисы и берёт на себя сквозную работу (аутентификация, ограничение частоты запросов, TLS, агрегация), чтобы сервисы её не повторяли. **Обнаружение сервисов** (service discovery) отвечает на вопрос «где сейчас `payment-service`», когда экземпляры появляются и исчезают: либо клиент спрашивает реестр (Eureka/Consul), либо платформа прячет это за стабильным DNS-именем (Kubernetes Service). **Сервер конфигурации** (config server) централизует конфигурацию. Всё это — инфраструктурная обвязка, которая маленькой команде может быть не нужна: модульный монолит часто оказывается правильным первым ответом.

## Какую проблему решает
- **Gateway:** без него каждый клиент знает адреса `esim-service`, `payment-service` и остальных, а каждый сервис сам проверяет JWT, ограничивает частоту запросов и завершает TLS, и каждый немного по-своему → он даёт клиентам одну точку входа и делает пограничную работу один раз.
- **Обнаружение сервисов:** без него вызывающие жёстко прописывают `host:port`, поэтому новая реплика не получает трафик, а мёртвая продолжает получать вызовы → оно держит актуальный список здоровых экземпляров за одним именем.
- **Сервер конфигурации:** без него каждый сервис носит свою конфигурацию для каждого окружения, и смена одной настройки означает пересборку и deploy каждой копии → он отдаёт конфигурацию из одного места (Git/Vault) и умеет её обновлять.
- **Когда не нужно:** у одной команды с модульным монолитом этих проблем нет; в Kubernetes DNS сервисов и ConfigMap уже закрывают обнаружение и конфигурацию, так что Eureka и сервер конфигурации — лишние компоненты, которые надо эксплуатировать.

## Основное

### Обязанности gateway
- **Маршрутизация**: путь/хост/заголовок → сервис (`/api/esim/**` → `esim-service`), с переписыванием пути и версионированием.
- **Аутентификация и авторизация на краю**: [[jwt-authentication|JWT]] проверяется один раз, дальше передаётся доверенный заголовок или сам токен; сервисы всё равно проверяют scopes.
- **Ограничение частоты запросов и квоты** (rate limiting) по клиенту/API-ключу (token bucket в Redis — см. [[redis-data-structures]]).
- **Терминирование TLS**: HTTPS наружу, обычный HTTP или mTLS внутри кластера.
- **Устойчивость**: таймауты, retry, [[circuit-breaker]] на маршрут, чтобы один медленный сервис не уронил gateway.
- **Агрегация / BFF**: один мобильный запрос расходится на несколько сервисов и объединяет результат (backend-for-frontend, один gateway на тип клиента).
- А также: логирование запросов, добавление [[distributed-tracing|заголовка трассировки]], CORS, кэширование ответов, публикация документации API ([[openapi-swagger]]).
- Варианты: Spring Cloud Gateway (реактивный, Java), Kong/Envoy/NGINX/Traefik (уровень инфраструктуры), облачные gateway. Бизнес-логику в gateway класть **нельзя**.

### Основы Spring Cloud Gateway
- Построен на Spring WebFlux + Netty (реактивный, неблокирующий); с Spring Cloud 2023/2024 есть и Servlet-вариант (`spring-cloud-starter-gateway-mvc`) для команд на Spring MVC.
- Маршрут = `id` + `uri` + **предикаты** (Path, Method, Header, Host, Query, Weight) + **фильтры** (RewritePath, StripPrefix, AddRequestHeader, RequestRateLimiter, Retry, CircuitBreaker, TokenRelay).
- `uri: lb://esim-service` использует Spring Cloud LoadBalancer с обнаружением; `uri: http://esim-service:8080` использует DNS платформы.
- Глобальные фильтры применяются ко всем маршрутам (проверка аутентификации, correlation id).

### Обнаружение сервисов
| | На стороне клиента | На стороне сервера |
|---|---|---|
| Как | Клиент запрашивает реестр (Eureka, Consul, ZooKeeper), выбирает экземпляр, вызывает его | Клиент вызывает стабильный адрес; прокси/DNS разрешает его в экземпляр |
| Балансировка нагрузки | В клиенте (Spring Cloud LoadBalancer: round-robin, random, с учётом зон) | В платформе (Kubernetes `Service` + kube-proxy/IPVS, облачный LB, Envoy sidecar) |
| Регистрация | Сервис регистрируется сам + heartbeat-ы | Платформа регистрирует по готовности pod-а |
| Подходит для | Развёртываний без Kubernetes, ландшафтов только на JVM | Kubernetes, Swarm, service mesh |

- В Kubernetes `http://payment-service.default.svc.cluster.local` — это и есть обнаружение; Eureka там избыточна ([[kubernetes-basics]]). Docker Swarm делает то же самое через внутренний DNS и routing mesh ([[docker-swarm]]).
- Здоровье: реестр удаляет экземпляры, пропустившие heartbeat-ы; Kubernetes удаляет pod-ы, не проходящие readiness probe. Оба полагаются на то, что сервис отдаёт честный health endpoint (Actuator `/actuator/health/readiness`).

### Сервер конфигурации
- Spring Cloud Config Server отдаёт `application-{profile}.yml` из Git/Vault; клиенты забирают конфигурацию при старте (`spring.config.import=configserver:`) и могут обновлять её через `@RefreshScope` + `/actuator/refresh` или Spring Cloud Bus.
- Альтернатива в Kubernetes: ConfigMap + Secret, смонтированные как файлы или переменные окружения, которые Spring Boot читает через `spring.config.import=kubernetes:` или обычные тома ([[spring-boot-properties-and-profiles]]).
- Секретам место в Vault / sealed secrets, никогда не в Git, даже приватном.

### Когда монолит — правильный ответ
- Микросервисы окупаются при многих командах, независимом ритме deploy и разных профилях масштабирования. При одной команде из 3–8 человек **модульный монолит** (чёткие границы модулей, одна единица развёртывания, одна БД со схемой на модуль — [[ddd-basics]]) избавляет от налога на сеть, обнаружение, трассировку, саги и gateway.
- Сигналы, что пора разделять: независимые циклы релизов блокируются одной сборкой, горячему модулю нужно в 10× больше реплик, другое требование к рантайму (ML на Python), изоляция ради соответствия требованиям (карточные данные под PCI DSS).
- Выделять по одному сервису за раз (паттерн strangler) за gateway; gateway скрывает миграцию от клиентов.

## Пример
Маршрутизация в gateway с передачей JWT, ограничением частоты на Redis и breaker-ом на маршруте provisioning.

```yaml
spring:
  cloud:
    gateway:
      default-filters:
        - TokenRelay=
        - AddRequestHeader=X-Gateway, edge-1
      routes:
        - id: esim
          uri: lb://esim-service
          predicates:
            - Path=/api/esim/**
          filters:
            - StripPrefix=1
            - name: RequestRateLimiter
              args:
                redis-rate-limiter.replenishRate: 50
                redis-rate-limiter.burstCapacity: 100
                key-resolver: "#{@apiKeyResolver}"
            - name: CircuitBreaker
              args:
                name: esimCb
                fallbackUri: forward:/fallback/esim
        - id: payments
          uri: lb://payment-service
          predicates:
            - Path=/api/payments/**
            - Method=POST,GET
          filters:
            - RewritePath=/api/payments/(?<rest>.*), /v2/$\{rest}
            - name: Retry
              args:
                retries: 2
                methods: GET
                backoff: { firstBackoff: 100ms, maxBackoff: 1s, factor: 2 }
  security:
    oauth2:
      resourceserver:
        jwt:
          issuer-uri: https://sso.example.kg/realms/beeline
```

## Подводные камни
- Gateway как единая точка отказа: нужно ≥ 2 реплик за балансировщиком платформы, и gateway должен быть без состояния.
- Блокирующий код (JDBC, `RestTemplate`) в фильтре реактивного gateway блокирует event loop-ы Netty и убивает пропускную способность; нужен `WebClient` ([[spring-reactive-webclient-sse]]) или MVC-gateway.
- Retry POST на gateway дублирует записи, если сервис не [[idempotency|идемпотентен]].
- Eureka плюс Kubernetes дают два источника истины о здоровье экземпляров; нужно выбрать один.
- «Микросервисная» архитектура, где каждый запрос синхронно проходит через 6 сервисов, — это распределённый монолит с худшей задержкой; между контекстами лучше асинхронные события ([[kafka-architecture]], [[outbox-pattern]]).
- Связанные заметки: [[rest-and-http]], [[spring-security-basics]], [[circuit-breaker]], [[kubernetes-basics]], [[system-design-basics]].
