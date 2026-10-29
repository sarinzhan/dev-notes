---
title: Набор инструментов для сетевой отладки
tags: [networking, troubleshooting, curl, tcpdump, dns]
learned: 2026-10-27
---
## Кратко
Когда вызов между сервисами падает, отлаживай снизу вверх, по одному уровню за раз: **имя** (разрешается ли DNS в правильный IP?), **маршрут и порт** (можно ли открыть TCP-соединение?), **TLS** (проходит ли рукопожатие с ожидаемым сертификатом?), **HTTP** (какой статус, заголовки и тайминги возвращаются?) и только потом **приложение**. На каждом уровне есть один-два инструмента, которые отвечают на его вопрос за секунды. Гадать на уровне приложения, когда настоящая проблема в DNS или в сертификате прокси, — самый распространённый способ потерять полдня.

## Основное
### Сценарий по уровням
| Вопрос | Инструмент | Как выглядит сбой |
|---|---|---|
| Разрешается ли имя и во что? | `dig +short api.example.com`, `dig @8.8.8.8 ...`, `getent hosts` | `NXDOMAIN`, старый IP после миграции, системный резолвер отвечает иначе, чем `dig` ([[dns-resolution]]) |
| Какой интерфейс и шлюз будут использованы? | `ip route get 10.20.0.5` | Трафик в подсеть VPN или Docker уходит через неверный интерфейс ([[linux-networking]]) |
| Доступен ли порт? | `nc -vz -w 3 host 5432` | `Connection refused` = хост доступен, никто не слушает; timeout = файрвол или security group отбрасывает пакеты |
| Где на пути всё останавливается? | `mtr -rwc 50 host`, `traceroute -T -p 443 host` | Потери, которые начинаются на одном хопе и продолжаются до конца; потери только на одном промежуточном хопе — обычно rate limit ICMP, а не настоящая проблема |
| Работает ли TLS и какой сертификат отдаётся? | `openssl s_client -connect host:443 -servername host` | Неверный издатель (корпоративный прокси переподписывает трафик), истёкшая цепочка, отсутствующий промежуточный сертификат, несовпадение SNI ([[tls-handshake-and-certificates]]) |
| Что сервер реально возвращает и на что уходит время? | `curl -v`, `curl -w` с переменными таймингов | 502/504 от прокси, а не от приложения, `time_connect` маленький, а `time_starttransfer` большой (медленный бэкенд) |
| Что в сети? | `tcpdump -i any -nn 'host X and port 443' -w cap.pcap` + Wireshark | SYN без SYN-ACK, RST сразу после подключения, повторные передачи ([[tcpdump]]) |
| Какие соединения держит этот хост? | `ss -tanp`, `ss -s`, `ss -ltnp` | Тысячи `TIME_WAIT` (нет keep-alive или пула), много `CLOSE_WAIT` (приложение не закрывает сокеты), ничего в `LISTEN` на ожидаемом порту ([[tcp-handshake-and-connection-states]]) |

### Чтение таймингов `curl`
`curl -w` показывает накопительные отметки времени: `time_namelookup` (DNS), `time_connect` (TCP установлен), `time_appconnect` (TLS завершён), `time_starttransfer` (первый байт), `time_total`. Вычитая соседние, получаешь длительность каждой фазы. Медленная фаза DNS указывает на резолвер, медленное подключение — на сеть или заполненную очередь accept, медленная фаза TLS — на проблему с OCSP или прокси, а большое время до первого байта — на сам сервер.

`--resolve host:443:10.0.0.7` отправляет запрос на выбранный IP, сохраняя правильные SNI и заголовок `Host`. Так проверяют один бэкенд за балансировщиком ([[load-balancers-l4-vs-l7]]) или новый сервер до переключения DNS.

### Refused, timeout и reset
- **Connection refused**: сразу пришёл RST. Хост работает, но на этом порту никто не слушает, или сервис внутри контейнера привязан только к `127.0.0.1`.
- **Timeout**: ответа нет вообще. Файрвол, security group или network policy отбрасывает пакеты, или маршрут неверный.
- **Connection reset посреди потока**: прокси или балансировщик закрыл простаивающее соединение, которое пул клиента всё ещё считал живым. Обычное решение — idle timeout в пуле короче, чем у прокси ([[sockets-and-connection-pooling]], [[hikaricp-pool-sizing]]).

### Внутри контейнеров и Kubernetes
В образах приложений редко есть `dig` или `tcpdump`. Подключи отладочный контейнер к сетевому пространству имён пода через `kubectl debug -it <pod> --image=nicolaka/netshoot --target=<container>` и запускай те же инструменты, видя сеть глазами пода ([[kubernetes-basics]]). Имена сервисов разрешаются только внутри кластера, а `NetworkPolicy` проявляется как timeout, но никогда как «refused».

### Проверки, специфичные для JVM
- `-Djavax.net.debug=ssl:handshake` печатает согласование TLS, включая то, каким сертификатам доверяет JVM.
- `PKIX path building failed` означает, что в truststore JVM нет выпустившего CA. При корпоративном прокси с инспекцией TLS, например FortiGate, импортируй CA прокси в truststore (`keytool -importcert -cacerts ...`) или задай `-Djavax.net.ssl.trustStore`. Никогда не отключай проверку.
- У Docker-образов на минимальных JRE свой `cacerts`, отдельный от trust store хоста.

## Пример
```bash
#!/usr/bin/env bash
# Использование: ./netcheck.sh billing.internal 443
# Проходит DNS -> TCP -> TLS -> HTTP и останавливается на первом упавшем уровне.
set -u
HOST=$1; PORT=${2:-443}

echo "== DNS"
IP=$(dig +short "$HOST" | tail -n1)
if [ -z "$IP" ]; then echo "no A record for $HOST"; exit 1; fi
echo "$HOST -> $IP via $(ip route get "$IP" | head -n1)"

echo "== TCP"
nc -vz -w 3 "$IP" "$PORT" || { echo "port closed or filtered"; exit 2; }

echo "== TLS"
echo | openssl s_client -connect "$IP:$PORT" -servername "$HOST" 2>/dev/null \
  | openssl x509 -noout -subject -issuer -enddate || { echo "TLS handshake failed"; exit 3; }

echo "== HTTP timings"
curl -sS -o /dev/null --resolve "$HOST:$PORT:$IP" \
  -w 'status=%{http_code} dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total}\n' \
  "https://$HOST:$PORT/actuator/health"
```

## Подводные камни
- `ping` мало что доказывает: ICMP часто заблокирован, когда TCP работает, и наоборот. Проверяй реальный порт через `nc` или `curl`.
- `curl -k` скрывает проблемы с сертификатами вместо того, чтобы их диагностировать, и тот же баг затем всплывает в JVM, которая сертификаты проверяет.
- `dig` обращается к DNS напрямую и игнорирует `/etc/hosts` и собственный DNS-кеш JVM. Используй `getent hosts`, чтобы увидеть, что возвращает системный резолвер, и помни, что JVM кеширует результаты (`networkaddress.cache.ttl`).
- Захватывай на правильном интерфейсе: в Docker или Kubernetes `-i any` на хосте может пропустить трафик, который остаётся внутри пространства имён. Захватывай из отладочного контейнера.
- Захваты пакетов могут содержать токены и персональные данные. Держи их короткими, отфильтрованными и не выкладывай в тикеты и чаты.
- Связанные заметки: [[linux-networking]], [[osi-and-tcp-ip-models]], [[reverse-proxy-nginx-basics]], [[distributed-tracing]] — чтобы проследить один запрос через сервисы, когда с сетью уже всё в порядке.
