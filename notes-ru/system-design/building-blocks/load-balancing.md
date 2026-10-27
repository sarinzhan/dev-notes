---
title: Балансировка нагрузки — алгоритмы, health checks, привязка сессий и глобальная балансировка
tags: [system-design, load-balancing, availability, networking]
learned: 2026-10-27
---
## Кратко
Балансировщик нагрузки (load balancer) распределяет запросы по N репликам, чтобы система масштабировалась горизонтально и переживала потерю реплики. Вопросы проектирования: на каком уровне он работает (L4 пересылает TCP-соединения, L7 понимает HTTP и может маршрутизировать по пути, заголовку или cookie), какой алгоритм выбирает реплику (round-robin, least connections, взвешенный, хэширование), как он узнаёт, что реплика нездорова, и как трафик балансируется между регионами (DNS и anycast). Механика L4 и L7 и особенности NGINX/Envoy описаны в [[load-balancers-l4-vs-l7]]; эта заметка — о выборе.

## Основное

### Где происходит балансировка
| Уровень | Механизм | Гранулярность | Типичное применение |
|---|---|---|---|
| DNS | несколько записей A/AAAA, взвешенные или гео-ответы, низкий TTL | на резолвер клиента | глобальная точка входа, выбор региона ([[dns-resolution]]) |
| Anycast | один IP анонсируется из многих PoP, BGP направляет к ближайшему | на сетевой путь | edge CDN, поглощение DDoS ([[cdn]]) |
| L4 (транспортный) | NAT/пересылка TCP/UDP-соединений по хэшу 5-tuple | на соединение | облачный NLB, высокая пропускная способность, TLS passthrough, не-HTTP (gRPC, прокси БД) |
| L7 (прикладной) | терминирует HTTP/TLS, маршрутизирует каждый запрос | на запрос | маршрутизация по пути/хосту, canary, retry, авторизация, лимиты ([[reverse-proxy-nginx-basics]]) |
| На стороне клиента | клиент держит список реплик и выбирает сам | на запрос | sidecar в service mesh, gRPC-клиенты, Kafka-клиенты ([[api-gateway-and-service-discovery]]) |

В реальном развёртывании уровни накладываются: DNS/anycast → L4 на edge → L7 ingress → (опционально) клиентская балансировка внутри mesh. `Service` в Kubernetes — это L4 (iptables/IPVS), `Ingress`/Gateway API — L7 ([[kubernetes-basics]]).

### Алгоритмы
| Алгоритм | Выбирает | Хорош для | Слабое место |
|---|---|---|---|
| Round-robin | следующую в списке | одинаковые реплики, короткие однородные запросы | игнорирует медленные реплики |
| Weighted round-robin | пропорционально весу | экземпляры разного размера, canary 5 % | статические веса |
| Least connections | с наименьшим числом выполняющихся запросов | долгие или неравномерные запросы (загрузки, стримы) | нужно общее состояние в LB; видит соединения, а не CPU |
| Least response time / EWMA | с наименьшей недавней задержкой | разнородные бэкенды | может согнать всех на одну быструю реплику |
| Random / power of two choices | выбрать 2 случайные, взять менее загруженную | распределённая клиентская балансировка без центрального состояния | всё равно нужен сигнал нагрузки |
| IP hash / cookie hash | тот же клиент → та же реплика | привязка (stickiness) для состояния в памяти | горячие клиенты (NAT, один крупный тенант) перегружают одну реплику |
| Consistent hashing / кольцо | ключ → реплика с минимальной перетасовкой | кэши, WebSocket-серверы, привязка по ключу ([[consistent-hashing]]) | неравномерная нагрузка без виртуальных узлов |

Правило для собеседования: по умолчанию least connections на L7, round-robin на L4, consistent hashing — только когда реплика хранит состояние по ключу (кэш внутри процесса или открытый WebSocket).

### Health checks и исключение реплик
- **Активные**: LB опрашивает `/actuator/health/readiness` каждые несколько секунд; `N` неудач исключают реплику, `M` успехов возвращают её. Readiness должна проверять то, что нужно pod (пул БД, Kafka), liveness — только то, жив ли процесс, иначе сбой БД перезапустит все pod ([[leases-heartbeats-and-health]]).
- **Пассивные (outlier detection)**: подсчёт 5xx и таймаутов по каждой реплике, исключение её на период остывания. Ловит «здоровые, но сломанные» реплики, которые всё ещё отвечают на проверку.
- **Connection draining**: при уменьшении масштаба или deploy перестать отправлять новые запросы, дождаться выполняющихся (Kubernetes `preStop` sleep + `terminationGracePeriodSeconds`), затем завершить. Без этого каждый deploy даёт всплеск 502 ([[signals-and-process-lifecycle]]).
- **Slow start**: плавно поднимать вес новой реплики за 30–60 с, чтобы холодная JVM (JIT, пустые пулы) не получала полную долю сразу.

### Привязка сессий и отсутствие состояния
- Привязка (cookie или хэш) — обходной путь для состояния на реплике. Ответ в духе system design — убрать состояние: сессии в Redis или подписанный токен ([[horizontal-vs-vertical-scaling]], [[cookies-sessions-and-cors]]), кэши по ключу сделать общими ([[caching]]).
- Оправданная привязка: WebSocket-соединения (сокет *и есть* состояние, [[design-chat-service]]), схлопывание запросов в кэше внутри процесса, gRPC-стримы. Сочетай её с consistent hashing, чтобы смена реплики перемещала только 1/N клиентов.

### Глобальная балансировка
- Geo-DNS или DNS по задержке отправляет пользователей из Бишкека в регион Франкфурт, а пользователей из Джакарты — в Сингапур; держи TTL коротким (30–60 с), чтобы переключение при отказе было быстрым, но помни, что клиенты и резолверы кэшируют дольше TTL ([[multi-region-basics]]).
- Anycast + L7 на edge (CDN или глобальный балансировщик) дают один IP на весь мир и переключение при отказе без изменений DNS, а также поглощают объёмный DDoS на edge.
- DNS с учётом здоровья исключает упавший регион; слой данных должен быть готов к тому, что трафик придёт в другое место ([[leader-follower-replication]]).

### Ёмкость и сам LB
- L7-прокси на 2 vCPU обрабатывает ~10–30 K rps с терминированием TLS; L4 масштабируется до миллионов соединений, потому что ничего не разбирает. Терминируй TLS один раз на L7, внутри mesh используй mTLS ([[mtls-and-service-to-service-auth]]).
- Сам LB должен быть резервирован: облачные LB — управляемые пары; самостоятельно размещённые NGINX/HAProxy используют keepalived + VRRP для плавающего IP.
- Retry на LB — только для идемпотентных методов или запросов с [[idempotency|ключом идемпотентности]], с бюджетом (например, 10 % дополнительной нагрузки), чтобы избежать шторма повторов.

## Пример
Фрагмент Kubernetes ingress (NGINX ingress controller) для API уведомлений: least connections, пассивное обнаружение выбросов, retry только для идемпотентных запросов и sticky sessions только на пути WebSocket.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: notification-api
  annotations:
    nginx.ingress.kubernetes.io/load-balance: "ewma"              # с учётом задержки вместо простого round-robin
    nginx.ingress.kubernetes.io/proxy-next-upstream: "error timeout http_502 http_503"
    nginx.ingress.kubernetes.io/proxy-next-upstream-tries: "2"    # один retry, на следующую реплику
    nginx.ingress.kubernetes.io/proxy-next-upstream-timeout: "3"
    nginx.ingress.kubernetes.io/proxy-connect-timeout: "2"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "10"
    nginx.ingress.kubernetes.io/limit-rps: "200"                   # грубый лимит на IP клиента на edge
spec:
  rules:
    - host: api.example.kg
      http:
        paths:
          - path: /v1/notifications
            pathType: Prefix
            backend: { service: { name: notification-api, port: { number: 8080 } } }
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: notification-ws
  annotations:
    nginx.ingress.kubernetes.io/affinity: "cookie"                 # WebSocket-серверы хранят состояние соединений
    nginx.ingress.kubernetes.io/session-cookie-name: "ws-route"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
spec:
  rules:
    - host: api.example.kg
      http:
        paths:
          - path: /ws
            pathType: Prefix
            backend: { service: { name: notification-ws, port: { number: 8081 } } }
```

NGINX повторяет `POST` только тогда, когда соединение упало до отправки запроса, — это безопасное подмножество; для retry на стороне клиента приложению всё равно нужен `Idempotency-Key` ([[api-design-for-system-design]]).

## Подводные камни
- Readiness, которая ничего не проверяет: LB продолжает слать трафик в pod с исчерпанным пулом БД; или readiness, которая проверяет всё, так что один сбой Redis разом исключает все pod, и у LB не остаётся upstream.
- Retry неидемпотентных `POST` на прокси после таймаута: запрос мог выполниться, и у пользователя теперь два заказа.
- Sticky sessions скрывают проблему масштабирования: один корпоративный клиент за NAT или один крупный тенант попадает на одну реплику и перегружает её.
- Долгоживущие соединения HTTP/2 или gRPC обходят балансировку на L4: одно соединение несёт тысячи запросов на одну реплику. Балансируй на L7 или используй клиентскую балансировку с периодическим переподключением.
- Короткие TTL в DNS не дают мгновенного переключения; браузеры, JVM (`networkaddress.cache.ttl`) и резолверы кэшируют дольше, чем задано.
- Связанные заметки: [[load-balancers-l4-vs-l7]] — механика прокси; [[circuit-breaker]] и [[failure-modes-and-timeouts]] — что делает клиент, когда у LB нет здорового upstream.
