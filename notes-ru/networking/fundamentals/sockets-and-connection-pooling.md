---
title: Сокеты и пулы соединений
tags: [networking, sockets, connection-pool, java]
learned: 2026-10-27
---
## Кратко
Сокет — это файловый дескриптор, который ядро связывает с четвёркой адресов (4-tuple) и двумя буферами; создание TCP-сокета стоит рукопожатия (плюс TLS), медленного старта (slow start) и эфемерного порта, который блокируется на 60 секунд после закрытия. Пулы соединений (connection pools) нужны, чтобы заплатить эту цену один раз и переиспользовать сокет для многих запросов, а также чтобы жёстко ограничить число соединений, которые открывает сервис. Каждая клиентская библиотека, которую ты используешь (HikariCP, Apache HttpClient, Lettuce, клиенты Kafka, JDK `HttpClient`), — это пул с немного разными настройками; сценарии отказов одинаковы: утечки, устаревшие соединения после простоя и исчерпание пула, которое маскируется под медлительность.

## Основное
### Жизненный цикл сокета
| Шаг | Сторона сервера | Сторона клиента | Состояние в ядре |
|---|---|---|---|
| создание | `socket()` | `socket()` | выделен fd, адреса нет |
| именование | `bind(0.0.0.0:8080)`, `listen(backlog)` | неявная привязка к эфемерному порту | `LISTEN` / нет |
| соединение | `accept()` возвращает новый fd на каждого клиента | `connect()` выполняет рукопожатие | `ESTABLISHED` |
| передача | `read()`/`write()` над буферами сокета | то же | буферы отправки/приёма, окно |
| завершение | `close()` или `shutdown()` | `close()` | обмен FIN, `TIME_WAIT` у активно закрывающей стороны |

- Слушающий сокет и сокеты отдельных соединений — разные fd; сервер с 10k клиентов держит 10k fd. `ulimit -n` (в некоторых образах по умолчанию 1024) — реальный потолок; Spring Boot в Kubernetes стоит запускать с 65536.
- У каждого сокета есть буферы отправки/приёма в ядре; медленный читатель заполняет буфер отправителя, и `write()` блокируется (или возвращает `EAGAIN` в неблокирующем режиме). Это обратное давление (backpressure) на самом нижнем уровне ([[backpressure-and-flow-control]], [[io-models-blocking-nonblocking-epoll]]).
- В Java: `java.net.Socket` (блокирующий, поток на соединение, нормально работает с [[virtual-threads]]), `SocketChannel` + `Selector` ([[java-nio]], то, что используют Netty и Tomcat NIO).

### Зачем нужны пулы
1. **Задержка**: рукопожатие TCP — 1 RTT, TLS 1.3 — ещё один RTT, аутентификация в Postgres — ещё несколько round trip; 5–50 мс на соединение внутри дата-центра, 100+ мс между регионами. Соединение из пула готово за микросекунды.
2. **Порты**: клиенты без пула тратят эфемерный порт на каждый запрос и упираются в исчерпание из-за `TIME_WAIT` уже на нескольких сотнях запросов в секунду к одному upstream ([[tcp-handshake-and-connection-states]]).
3. **Стоимость на стороне сервера**: бэкенд Postgres — это процесс с ~10 МБ памяти; `max_connections=100` — жёсткий лимит, и именно пул защищает базу от 20 подов, каждый из которых открывает по 50 соединений ([[hikaricp-pool-sizing]]).
4. **Контроль допуска** (admission control): ограниченный пул превращает перегрузку в очередь с таймаутом вместо неограниченного веера запросов, который роняет upstream.

### Параметры пула, важные везде
- **Максимальный размер**: всего соединений; для баз данных классическая отправная точка — `cores * 2 + spindles`, а не «столько же, сколько потоков».
- **Минимум простаивающих / прогрев**: держать часть соединений открытыми, чтобы первый запрос после затишья не был медленным.
- **Максимальное время жизни** (max lifetime): пересоздавать соединения до того, как их убьёт инфраструктура (idle timeout балансировщика, изменение DNS, ротация сертификата). В HikariCP по умолчанию 30 мин; задай меньше любого idle timeout у NAT/LB.
- **Idle timeout / keep-alive**: обнаружение полуоткрытых соединений ([[tcp-handshake-and-connection-states]]).
- **Таймаут получения** (acquire timeout): сколько вызывающий ждёт свободное соединение, прежде чем быстро упасть. Без него исчерпание пула превращается в невидимое зависание; с ним получаешь понятное исключение и метрику.
- **Валидация**: дешёвая проверка при выдаче (`isValid`, JDBC4) или фоновая проверка; test-on-borrow — это потерянный RTT, если keep-alive уже настроен.

### Пулы, которые у тебя уже работают
| Клиент | Названия настроек пула | Примечания |
|---|---|---|
| HikariCP (JDBC) | `maximumPoolSize`, `connectionTimeout`, `maxLifetime`, `keepaliveTime` | одно физическое TCP-соединение на соединение в пуле |
| JDK `HttpClient` (Java 11+) | внутренний пул, `jdk.httpclient.connectionPoolSize`, `keepalive.timeout` | HTTP/2 мультиплексирует много запросов в одном соединении, поэтому «размер пула» значит меньше |
| Apache HttpClient 5 / `RestClient` | `maxConnTotal`, `maxConnPerRoute`, `connectionRequestTimeout`, TTL | значение по умолчанию 5 на маршрут слишком мало для реальных сервисов ([[rest-clients]]) |
| Reactor Netty `WebClient` | максимум соединений в `ConnectionProvider`, `maxIdleTime`, `pendingAcquireTimeout` | ([[spring-reactive-webclient-sse]]) |
| Продюсер/консьюмер Kafka | одно соединение на брокер, пул настраивать не нужно | долгоживущие; `connections.max.idle.ms` ([[kafka-producer]]) |
| Lettuce (Redis) | по умолчанию одно мультиплексированное соединение | пул только для блокирующих команд/транзакций |

## Пример
`HttpClient` из Java 21, настроенный как общий bean с пулом и явными таймаутами — именно здесь большинство команд ошибаются, создавая новый клиент на каждый вызов (каждый клиент владеет собственным пулом и executor).
```java
@Configuration
public class SmdpHttpConfig {

    @Bean
    HttpClient smdpHttpClient() {
        return HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_2)          // откат на 1.1, если сервер не умеет h2
                .connectTimeout(Duration.ofSeconds(3))        // бюджет на рукопожатие
                .executor(Executors.newVirtualThreadPerTaskExecutor())
                .build();                                     // ОДИН экземпляр: владеет пулом соединений
    }

    @Bean
    SmdpClient smdpClient(HttpClient client) {
        return new SmdpClient(client, URI.create("https://smdp.example.com/gsma/rsp2/es2plus/"));
    }
}

public record SmdpClient(HttpClient client, URI base) {
    public String confirmOrder(String iccid) throws IOException, InterruptedException {
        var req = HttpRequest.newBuilder(base.resolve("confirmOrder"))
                .timeout(Duration.ofSeconds(10))               // бюджет на чтение для запроса
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{\"iccid\":\"" + iccid + "\"}"))
                .build();
        return client.send(req, HttpResponse.BodyHandlers.ofString()).body(); // тело прочитано полностью: соединение возвращается в пул
    }
}
```
Запускай JVM с `-Djdk.httpclient.keepalive.timeout=60`, если idle timeout upstream короче значения JDK по умолчанию в 1200 с для HTTP/1.1, иначе первый вызов после паузы получит reset.

## Подводные камни
- Создание клиента/пула на каждый запрос (`new RestTemplate()`, `HttpClient.newHttpClient()` внутри метода) сводит смысл на нет и даёт утечку потоков и сокетов.
- Тело ответа не прочитано и не закрыто; соединение никогда не возвращается, пул голодает, а `ss` показывает `CLOSE-WAIT`.
- Размер пула равен числу потоков, «чтобы не ждать»: 200 подов × 200 соединений убивают Postgres; размер подбирай под upstream, а очередь пусть будет в пуле.
- Удержание соединения с БД из пула во время удалённого HTTP-вызова внутри `@Transactional` ([[spring-transactional]]): медленный upstream теперь исчерпывает ещё и пул БД.
- Устаревший DNS: пул держит соединения к старому IP после failover; ограничивай `maxLifetime` и держи TTL кеша DNS в JVM коротким ([[dns-resolution]]).
- Связанные заметки: [[tcp-vs-udp]] — сколько стоит соединение, [[circuit-breaker]] — что делать, когда начинает срабатывать acquire timeout пула.
