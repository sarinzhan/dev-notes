---
title: Разрешение имён в DNS
tags: [networking, dns, kubernetes, java, caching]
learned: 2026-10-27
---
## Кратко
DNS превращает имя в адреса через цепочку кешей: собственный кеш приложения (у JVM он есть), stub-резолвер ОС (`/etc/resolv.conf`, `systemd-resolved`), рекурсивный резолвер, который обходит root → TLD → авторитетные серверы, и авторитетный сервер, которому принадлежит зона. Каждый ответ несёт TTL, ограничивающий, насколько он может устареть. В Kubernetes CoreDNS отвечает на `service.namespace.svc.cluster.local` адресом ClusterIP (или IP подов для headless-сервисов), а список `search` пода вместе с `ndots:5` позволяет использовать короткие имена ценой лишних запросов. Большинство DNS-инцидентов в Java-сервисе — это кеширование: JVM или пул соединений всё ещё держит адрес, который перестал быть действительным.

## Основное
### Путь разрешения
1. Приложение вызывает `getaddrinfo()` / `InetAddress.getByName()`; JVM сначала проверяет собственный кеш.
2. Stub-резолвер на хосте: `/etc/hosts` и `/etc/resolv.conf` (`nameserver`, `search`, `options ndots:N`), порядок задаёт `/etc/nsswitch.conf`. В современном Linux `127.0.0.53` — это `systemd-resolved`; в Docker это `127.0.0.11`, встроенный DNS ([[linux-networking]]).
3. **Рекурсивный резолвер** (провайдер, `1.1.1.1`, CoreDNS, корпоративный DNS): отвечает из кеша или обходит иерархию: корневые серверы → серверы TLD `.kg` → **авторитетные** серверы зоны (записи `NS`), кешируя каждый шаг.
4. Авторитетный ответ с TTL; каждый кеш на пути хранит его в пределах этого TTL.
- Транспорт: UDP 53 с классическим лимитом 512 байт (EDNS0 его поднимает); для больших ответов (крупные `TXT`, DNSSEC) и передачи зон — откат на TCP. DoT/DoH шифруют участок stub → резолвер ([[tcp-vs-udp]]).

### Записи, которые реально используются
| Запись | Отображает | Значение для бэкенда |
|---|---|---|
| `A` / `AAAA` | имя → IPv4 / IPv6 | обычный случай; несколько записей `A` = DNS round robin |
| `CNAME` | имя → другое имя | `api.beeline.kg` → `lb-123.cloud.example`; не может сосуществовать с другими записями на вершине зоны (apex) |
| `NS` | зона → авторитетные серверы | делегирование |
| `SRV` | сервис → хост + порт + приоритет/вес | Consul, headless-сервисы Kubernetes так публикуют порты |
| `TXT` | произвольный текст | ACME-проверки, SPF/DKIM, подтверждение домена |
| `MX` | почтовые серверы | только если отправляешь почту |
| `PTR` | IP → имя (обратная зона) | медленный вход по `ssh`/в Postgres, когда обратный DNS сломан |
| `HTTPS`/`SVCB` | параметры сервиса, включая ALPN `h3` | обнаружение HTTP/3 ([[http-1-1-vs-2-vs-3]]) |

### TTL и слои кеширования
- Авторитетный TTL — это максимум; каждый кеш отсчитывает его вниз. Понижать TTL перед миграцией нужно за один старый TTL заранее, потому что кеши всё ещё хранят старое значение со старым TTL.
- Отрицательные ответы (`NXDOMAIN`) тоже кешируются, по полю `minimum` записи SOA; имя, запрошенное до его создания, остаётся «не найдено» несколько минут.
- **Кеш JVM**: успешные запросы — 30 с по умолчанию (`networkaddress.cache.ttl`), неудачные — 10 с (`networkaddress.cache.negative.ttl`); при наличии `SecurityManager` раньше кешировалось навсегда — отсюда фольклор «Java кеширует DNS навсегда». Для сервисов за меняющимися IP задай `-Dnetworkaddress.cache.ttl=10` или пропиши в `java.security`.
- Клиентские библиотеки кешируют поверх JVM: пул соединений держит сокеты к старому IP до `maxLifetime` ([[sockets-and-connection-pooling]]); `DnsNameResolver` в Netty (используется в `WebClient` на Reactor Netty) имеет свой кеш с учётом TTL; клиент Kafka заново разрешает bootstrap-серверы при каждом переподключении.

### DNS в Kubernetes
- CoreDNS обслуживает зону кластера. Имена: `<svc>.<ns>.svc.cluster.local` → ClusterIP; **headless**-сервис (`clusterIP: None`) возвращает IP подов напрямую — это то, что нужно для балансировки на стороне клиента (gRPC `round_robin`, Kafka, Cassandra) ([[grpc-and-protobuf]]). Для StatefulSet существуют имена `<pod-name>.<svc>`.
- `/etc/resolv.conf` пода: `nameserver 10.96.0.10`, `search esim.svc.cluster.local svc.cluster.local cluster.local`, `options ndots:5`. Имя, в котором меньше 5 точек (в `api.beeline.kg` их 2), сначала пробуется с каждым суффиксом поиска: 3–4 round trip с NXDOMAIN перед настоящим запросом. Используй завершающую точку (`api.beeline.kg.`) или задай `dnsConfig.options ndots:2` для сервисов, которые обращаются ко многим внешним хостам.
- IP сервиса стабилен, пока поды за ним меняются; переключение при отказе (failover) на основе DNS внутри кластера — это проблема kube-proxy/Endpoints, а не TTL ([[kubernetes-basics]], [[ip-addressing-ports-nat]]).
- Перегрузка CoreDNS (conntrack, потери UDP) выглядит как случайные 5-секундные зависания: это таймаут резолвера. `NodeLocal DNSCache` решает проблему кешем на каждом узле.

### DNS как инструмент балансировки и failover
- Записи `A` по round robin дают грубое распределение без проверок здоровья (health checks); клиенты кешируют, пулы соединений «прилипают», поэтому перекос нормален. DNS на основе геолокации/задержки (Route 53, Cloudflare) направляет в ближайший регион ([[multi-region-basics]]); failover DNS подменяет записи при провале health check, с точностью до TTL. Для чего-то более тонкого используй настоящий балансировщик нагрузки ([[load-balancers-l4-vs-l7]]).

## Пример
Трассировка запроса так, как это делает резолвер, затем проверка того, что закеширует JVM.
```bash
# полная секция ответа, с TTL и цепочкой CNAME
$ dig api.beeline.kg
;; ANSWER SECTION:
api.beeline.kg.        300   IN  CNAME  lb-7f3.eu-central.cloud.example.
lb-7f3.eu-central.cloud.example.  60  IN  A  52.18.7.90
lb-7f3.eu-central.cloud.example.  60  IN  A  52.18.7.91
;; Query time: 23 msec
;; SERVER: 127.0.0.53#53(127.0.0.53)      # ответил локальный stub -> иди к рекурсивному, чтобы увидеть реальные TTL

# авторитетный ответ в обход всех кешей
$ dig +short NS beeline.kg.
ns1.beeline.kg.
$ dig @ns1.beeline.kg. api.beeline.kg. +norecurse

# пройти делегирование самому (то, что делает рекурсивный резолвер с холодным кешем)
$ dig +trace api.beeline.kg. | grep -E '^(kg\.|beeline\.kg\.|api)'

# внутри пода: почему один внешний запрос стоит пяти
$ kubectl exec -it esim-api-0 -- cat /etc/resolv.conf
search esim.svc.cluster.local svc.cluster.local cluster.local
nameserver 10.96.0.10
options ndots:5
$ kubectl exec -it esim-api-0 -- dig +short smdp.vendor.example.     # завершающая точка: один запрос
```
Фиксация кеша JVM, чтобы поды видели переключение базы данных за секунды:
```yaml
# JAVA_TOOL_OPTIONS в Deployment или аргументы JVM в Dockerfile
JAVA_TOOL_OPTIONS: "-Dnetworkaddress.cache.ttl=10 -Dnetworkaddress.cache.negative.ttl=5"
```

## Подводные камни
- Понижение TTL в день миграции: старые кеши держат старое значение в течение старого TTL. Понижай за день до, повышай после.
- `CNAME` на вершине зоны (`beeline.kg`) недопустим; провайдеры предлагают «сплющивание» через `ALIAS`/`ANAME`.
- Сервисы, которые разрешают имя один раз при старте (`new InetSocketAddress(host, port)`, сохранённый в поле), никогда не увидят failover; разрешай имя на каждое соединение.
- `ndots:5` в Kubernetes плюс нестабильный CoreDNS превращают каждый внешний вызов в несколько таймаутов; всегда проверяй разрешение имён изнутри пода, а не с узла.
- Split-horizon DNS (разные ответы внутри офиса и снаружи) объясняет ситуацию «работает через VPN, падает из CI»; `dig @8.8.8.8` против `dig @corp-dns` это показывает.
- Связанные заметки: [[linux-networking]] — настройка резолвера на хосте, [[network-debugging-toolkit]] — рецепты для `dig`/`drill`/`nslookup`.
