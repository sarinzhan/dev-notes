---
title: Основы обратного прокси на nginx
tags: [networking, nginx, reverse-proxy, tls-termination, timeouts]
learned: 2026-10-27
---
## Кратко
Обратный прокси (reverse proxy) стоит перед твоими сервисами, принимает соединения клиентов и открывает собственные, отдельные соединения к upstream-серверам. Этот хоп даёт завершение TLS (TLS termination), один публичный порт для многих сервисов, маршрутизацию по пути, кеширование, canary, буферизацию, которая защищает потоки Tomcat от медленных клиентов, и место для rate limit и журналов доступа. nginx — тот, который чаще всего придётся настраивать вручную; у Envoy, Traefik, Caddy и HAProxy те же понятия. Каждый «502 Bad Gateway» или «504 Gateway Timeout», который ты видел, пришёл именно от этого слоя.

## Основное
### Прямой и обратный прокси
| | Прямой прокси (forward proxy) | Обратный прокси (reverse proxy) |
|---|---|---|
| Где стоит | рядом с клиентами, настраивается ими (`HTTP_PROXY`, браузер, JVM `-Dhttps.proxyHost`) | рядом с серверами; клиенты думают, что он *и есть* сервер |
| Что скрывает | клиентов (цель видит IP прокси) | серверы (топологию, число экземпляров) |
| Сценарии | allow-листы для исходящего трафика, журналирование исходящих вызовов, общий кеш (Squid, зеркало Maven), анонимность, инспекция TLS ([[tls-handshake-and-certificates]]) | балансировка нагрузки, завершение TLS, кеширование (`proxy_cache`), canary по весу или заголовку, ingress для микросервисов (маршрутизация по пути), API-шлюз (аутентификация, rate limit), централизованные журналы доступа |

- Балансировщик нагрузки, API-шлюз и ingress-контроллер — это обратные прокси с дополнительными возможностями ([[load-balancers-l4-vs-l7]], [[api-gateway-and-service-discovery]]).
- **Прозрачный прокси** (transparent proxy): клиенты вообще не настраиваются; сеть сама перенаправляет их трафик на прокси (iptables `REDIRECT`/`TPROXY`, policy routing). Так работают корпоративные веб-фильтры и sidecar-прокси service mesh (Envoy в Istio так перехватывает каждое соединение пода). Без перехвата TLS он видит у HTTPS-трафика только IP, порты и SNI.

### Два TCP-соединения, а не одно
- Прокси завершает соединение клиента и открывает собственное к upstream: два рукопожатия, две TLS-сессии, два набора буферов, таймаутов и настроек keepalive. HTTP/2 от клиента может превратиться в HTTP/1.1 к upstream. L4-балансировщик в режиме NAT, наоборот, переписывает пакеты одного соединения.
- Последствия: upstream видит IP прокси (отсюда `X-Forwarded-For`); отключение клиента не отменяет запрос к upstream; каждое соединение к upstream занимает эфемерный порт на прокси, поэтому без keepalive нагруженный прокси исчерпывает порты к одному `IP:port` (по умолчанию около 28 000) и копит `TIME_WAIT` ([[connection-establishment-cost]], [[sockets-and-connection-pooling]]).

### Строительные блоки nginx
| Директива | Что делает | Типичное значение |
|---|---|---|
| `upstream api { server ...; }` | пул бэкендов; `least_conn`, `ip_hash`, `keepalive N` | `keepalive 32` для переиспользования соединений к upstream |
| `proxy_pass http://api;` | перенаправить запрос; завершающий слеш в `proxy_pass` переписывает префикс пути | следи за слешем |
| `proxy_set_header Host $host;` | по умолчанию nginx отправляет имя upstream как `Host` | задавай всегда |
| `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` | дописать IP клиента | плюс `X-Forwarded-Proto $scheme`, `X-Real-IP $remote_addr` |
| `proxy_http_version 1.1;` + `Connection ""` | обязательно для keepalive к upstream и WebSocket | по умолчанию 1.0 |
| `proxy_connect_timeout` | TCP-подключение к upstream | 2–5 с |
| `proxy_read_timeout` | ожидание между двумя чтениями из upstream (не общее) | 30–60 с; часы для SSE/WebSocket |
| `client_max_body_size` | лимит тела запроса; сверх него `413` | `10m` для загрузок |
| `proxy_buffering on` | прочитать весь ответ upstream, затем отдавать клиенту в его темпе | включено для API, выключено для потоков |
| `proxy_next_upstream error timeout http_502` | повторить на другом бэкенде | добавляй `non_idempotent`, только если действительно это имеешь в виду |
| `limit_req zone=api burst=20 nodelay;` | rate limit по алгоритму leaky bucket на ключ | первая линия обороны ([[rate-limiting]]) |

### Буферизация: зачем она нужна и когда её выключать
- С включённой буферизацией nginx быстро забирает весь ответ у Tomcat и отдаёт его медленному мобильному клиенту в течение секунд; поток Tomcat освобождается сразу. Без неё поток сервлета привязан к пропускной способности клиента. По умолчанию включена, для обычных API её стоит оставить.
- Выключай её (`proxy_buffering off` или заголовок `X-Accel-Buffering: no` от приложения) для SSE, потоковых chunked-ответов, потоков токенов от LLM ([[websockets-sse-and-long-polling]]). С телами запросов так же: `proxy_request_buffering off` для больших загрузок, которые нужно передавать в приложение потоком.

### Завершение TLS
- nginx хранит сертификат и закрытый ключ (`ssl_certificate`, `ssl_certificate_key`), говорит с клиентом по HTTPS и HTTP/2, а с upstream в частной сети — обычно по простому HTTP. Тогда приложение должно строить URL с учётом `X-Forwarded-Proto` (Spring: `server.forward-headers-strategy`).
- Повторное шифрование до upstream (`proxy_pass https://...`, `proxy_ssl_verify on`) — когда сети нельзя доверять; passthrough на L4 (блок `stream {}` с `ssl_preread` для маршрутизации по SNI) — когда бэкенд должен видеть клиентский сертификат ([[mtls-and-service-to-service-auth]]).
- OCSP stapling, `ssl_protocols TLSv1.2 TLSv1.3` и HSTS настраиваются здесь, а не в каждом сервисе.

### Коды статуса, которые он генерирует
- `502 Bad Gateway`: upstream отказал в соединении, сбросил его или прислал некорректный ответ (часто при deploy, когда nginx переиспользует keepalive-соединение, закрытое приложением; задай `keepalive_timeout` в Tomcat ≥ `keepalive_time` в nginx).
- `504 Gateway Timeout`: сработал `proxy_read_timeout` или `proxy_connect_timeout`; приложение может всё ещё обрабатывать запрос — так и возникает повторная обработка без идемпотентности ([[idempotency]]).
- `499`: клиент закрыл соединение до ответа nginx (только в журналах); волна 499 означает, что клиенты отваливаются по таймауту раньше тебя.
- `413`, `429`, `503` от `limit_req`/`limit_conn`.

### Наблюдаемость
- Журнал доступа в JSON с `$request_time` (общее время со стороны клиента) и `$upstream_response_time` (доля приложения); разница — это буферизация и медленные клиенты. Передавай `$request_id` как `X-Request-ID`, чтобы связать журналы приложения и трейсы ([[distributed-tracing]], [[observability-three-pillars]]).

## Пример
Блок `server` для Spring Boot API в боевом виде: TLS, HTTP/2, keepalive к upstream, forwarded-заголовки, потоковый location с выключенной буферизацией, rate limit на эндпоинте аутентификации и JSON-журналы доступа.
```nginx
upstream esim_api {
    least_conn;
    server 10.20.6.10:8080 max_fails=2 fail_timeout=10s;
    server 10.20.6.11:8080 max_fails=2 fail_timeout=10s;
    keepalive 32;                       # пул соединений к upstream
}

limit_req_zone $binary_remote_addr zone=auth:10m rate=5r/s;

log_format json escape=json '{"time":"$time_iso8601","request_id":"$request_id","status":$status,'
    '"method":"$request_method","uri":"$request_uri","rt":$request_time,'
    '"upstream_rt":"$upstream_response_time","upstream":"$upstream_addr","client":"$remote_addr"}';

server {
    listen 443 ssl;
    http2 on;
    server_name api.beeline.kg;
    ssl_certificate     /etc/letsencrypt/live/api.beeline.kg/fullchain.pem;   # конечный + промежуточные
    ssl_certificate_key /etc/letsencrypt/live/api.beeline.kg/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=31536000" always;
    access_log /var/log/nginx/api.json json;
    client_max_body_size 5m;

    location /api/ {
        proxy_pass http://esim_api;                          # без завершающего слеша: путь передаётся без изменений
        proxy_http_version 1.1;
        proxy_set_header Connection "";                      # включает keepalive к upstream
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;
        proxy_connect_timeout 3s;
        proxy_read_timeout 30s;
        proxy_next_upstream error timeout http_502;          # по умолчанию только идемпотентные
    }

    location /api/v1/provisioning/ {                        # SSE: поток, длинный таймаут
        proxy_pass http://esim_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_read_timeout 1h;
    }

    location /api/v1/auth/ {
        limit_req zone=auth burst=10 nodelay;
        proxy_pass http://esim_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /actuator/ { deny all; }                        # никогда не публичный
}
```
Проверка и перезагрузка без разрыва соединений:
```bash
$ sudo nginx -t && sudo nginx -s reload
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
$ curl -sI https://api.beeline.kg/api/v1/tariffs | grep -iE 'HTTP|x-request-id|strict'
HTTP/2 200
```

## Подводные камни
- `proxy_pass http://api/` с завершающим слешем отрезает префикс `location`; без него передаётся полный путь. Половина всех тикетов «404 за nginx».
- Нет `proxy_http_version 1.1` + `Connection ""`: нет keepalive к upstream, новое TCP-соединение на каждый запрос, скопление `TIME_WAIT` на прокси ([[tcp-handshake-and-connection-states]]).
- `proxy_read_timeout` — это время между чтениями, а не общий дедлайн; медленно «капающий» upstream может держать запрос минутами.
- Забыли передать `Host` и `X-Forwarded-Proto`: Spring строит редиректы `http://esim_api/...` и заголовки `Location`, указывающие на имя upstream.
- nginx разрешает имена хостов в `proxy_pass` один раз при старте, если не задан `resolver` и не используется переменная; после изменения DNS он продолжает использовать старый IP ([[dns-resolution]]).
- Связанные заметки: [[load-balancers-l4-vs-l7]] — сторона балансировки того же узла, [[cdn]] — слой перед ним.
