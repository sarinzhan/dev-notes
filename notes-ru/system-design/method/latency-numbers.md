---
title: Числа задержек — лестница от кеша L1 до межрегиональных вызовов и что из неё следует
tags: [system-design, performance, latency, interview]
learned: 2026-10-27
---
## Кратко
Задержка растёт примерно в десять раз на каждой ступени лестницы: наносекунды внутри CPU, сотни наносекунд для RAM, десятки микросекунд для SSD, сотни микросекунд для сетевого запроса и ответа (round trip) внутри дата-центра, миллисекунды для запроса к базе данных, от десятков до сотен миллисекунд между континентами. Зная ступени, можно сложить **бюджет задержки (latency budget)** запроса и увидеть, на что он уходит: один межрегиональный вызов стоит дороже тысячи чтений из Redis, а «болтливый» паттерн N+1 по сети — самая частая причина, по которой взрывается p99.

## Основное

### Лестница (железо около 2026 года, порядок величины)
| Операция | Время | В одной единице (мкс) |
|---|---|---|
| Попадание в кеш L1 | 1 нс | 0.001 |
| Неверное предсказание ветвления | 3 нс | 0.003 |
| Попадание в кеш L2 | 4 нс | 0.004 |
| Захват/освобождение мьютекса без конкуренции | 20 нс | 0.02 |
| Чтение из основной памяти (DRAM) | 100 нс | 0.1 |
| Сжатие 1 KB (LZ4/Snappy) | 2 мкс | 2 |
| Случайное чтение 4 KB с NVMe SSD | 20–100 мкс | 20–100 |
| Последовательное чтение 1 MB из RAM | 10 мкс | 10 |
| Последовательное чтение 1 MB с NVMe | 0.3–1 мс | 300–1 000 |
| Round trip внутри одного дата-центра | 0.2–0.5 мс | 200–500 |
| Redis GET, тот же ДЦ | 0.2–1 мс | 200–1 000 |
| Точечное чтение по индексу в Postgres, тёплый кеш | 0.5–2 мс | 500–2 000 |
| Commit в Postgres с `fsync` на NVMe | 1–3 мс | 1 000–3 000 |
| Kafka produce, `acks=all`, 3 реплики | 2–10 мс | 2 000–10 000 |
| Позиционирование головки HDD | 5–10 мс | 5 000–10 000 |
| Round trip между AZ в одном регионе | 1–2 мс | 1 000–2 000 |
| Round trip Бишкек ↔ Франкфурт | 70–90 мс | 70 000–90 000 |
| Round trip Европа ↔ восток США | 80–100 мс | 80 000–100 000 |
| Round trip Европа ↔ Сингапур | 150–200 мс | 150 000–200 000 |
| Рукопожатие TLS 1.3, новое соединение, тот же регион | 1 RTT + CPU ≈ 2–5 мс | — |
| Пауза GC в JVM (G1, young) | 5–50 мс | — |

Правила, которые следуют из таблицы:
- **RAM в 100× быстрее SSD, SSD в 100× быстрее сетевого перехода, межконтинентальный переход в 100× медленнее перехода внутри ДЦ.** Каждая пересечённая граница стоит два порядка величины.
- **Последовательное быстрее случайного**: 1 MB последовательно с NVMe (~1 мс) против 250 случайных чтений по 4 KB (~10 мс). Поэтому Kafka и базы данных на основе WAL быстры: они дописывают в конец ([[kafka-architecture]], [[postgres-indexes]]).
- **Скорость света — нижняя граница**: ~200 км за миллисекунду в оптоволокне; путь 4 000 км — это ≥ 20 мс в одну сторону, ≥ 40 мс RTT ещё до любого коммутатора или очереди. Никакая оптимизация не исправит географию; помогает только перенос данных ближе ([[cdn]], [[multi-region-basics]]).
- **Соединения дороги**: установка TCP + TLS занимает несколько RTT, поэтому соединения держат в пуле ([[sockets-and-connection-pooling]], [[tls-handshake-and-certificates]]). Мультиплексирование HTTP/2 помогает так же ([[http-1-1-vs-2-vs-3]]).

### Бюджет задержки: складываем запрос
Задержка запроса — это сумма последовательных переходов плюс максимум параллельных, плюс время в очередях. Когда SLO жёсткий, её расписывают для каждого эндпоинта ([[requirements-and-constraints]]):

```text
GET /v1/subscribers/{id}/notifications   target p99 = 300 ms, client in Bishkek, service in Frankfurt
  client → edge (Bishkek→Frankfurt RTT)         80 ms   (already 27 % of the budget, and unavoidable)
  TLS resumed (0-RTT / session ticket)            0 ms   (new connection would add 1 RTT = +80 ms)
  LB → service, in DC                            0.5 ms
  auth: JWT verify in process                    0.1 ms  (an introspection call to an IdP would add 2–5 ms)
  Redis GET prefs                                 1 ms
  Postgres: SELECT last 20 by (user_id, created_at DESC) via index, warm   2 ms
  JSON serialisation 10 KB                        0.5 ms
  response 10 KB over the WAN ≈ 1 RTT + transfer  80 ms
  ------------------------------------------------------
  p50 ≈ 165 ms; p99 adds queueing + GC + a cold DB page: ≈ 220–250 ms → fits, barely.
  Any sequential extra call to another service (+1 ms in DC, +80 ms if cross-region) must be parallelised.
```

Выводы уровня собеседования:
- **Усиление хвостовой задержки (tail latency amplification)**: если запрос расходится на 10 сервисов, и у каждого 1 % шанс оказаться медленным, то медленными будут 10 % пользовательских запросов. Лечится меньшим числом переходов, страхующими запросами (hedged requests) или более жёстким p99 каждого перехода ([[failure-modes-and-timeouts]]).
- **N+1 по сети** — главный баг в реальной жизни: 100 последовательных GET в Redis = 100 мс; один `MGET` = 1 мс ([[jpa-n-plus-one]] — та же ошибка в версии ORM).
- **Синхронная репликация между регионами** добавляет полный RTT по WAN к каждому commit (70–100 мс); внутри региона — ~1–2 мс. Одно это число объясняет большинство решений active/active против active/passive ([[leader-follower-replication]]).
- **Кеширование выигрывает ступенями, а не процентами**: перенос чтения из Postgres (2 мс) в Redis (0.5 мс) — это 4×; в кеш внутри процесса (100 нс) — 20 000× ([[caching]]).
- **Таймауты должны быть выше реального p99 перехода**, а не выше среднего: таймаут 50 мс на вызове с p99 60 мс превращает 1 % трафика в retry и в шторм, устроенный своими руками.

### Пропускная способность из задержки
Закон Литтла: `concurrency = throughput × latency`. Pod с 200 потоками запросов и средним вызовом нижестоящей системы 100 мс обрабатывает максимум `200 / 0.1 s = 2 000 rps`, каким бы ни был его CPU. Потолок поднимает меньшая задержка или неблокирующий ввод-вывод ([[virtual-threads]], [[io-models-blocking-nonblocking-epoll]]), а также пул побольше — до лимита соединений базы данных ([[hikaricp-pool-sizing]]).

## Пример
Микробенчмарк на Java, который делает лестницу видимой на твоей машине: map внутри процесса против Redis против Postgres, по 1 000 последовательных точечных чтений. Запусти один раз на локальном стеке в Docker и запомни числа.

```java
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;

public class LatencyLadder {
    static long measureAvgNanos(String label, int n, Supplier<?> op) {
        for (int i = 0; i < 100; i++) op.get();                // прогрев: JIT, пулы соединений, кеш БД
        long start = System.nanoTime();
        for (int i = 0; i < n; i++) op.get();
        long avg = (System.nanoTime() - start) / n;
        System.out.printf("%-28s avg %8d ns  (%.3f ms)%n", label, avg, avg / 1_000_000.0);
        return avg;
    }

    public static void main(String[] args) {
        var local = new ConcurrentHashMap<String, String>();
        local.put("prefs:42", "{\"sms\":true}");
        try (var jedis = new redis.clients.jedis.JedisPooled("localhost", 6379);
             var ds = pgDataSource()) {                       // HikariDataSource, здесь достаточно пула размером 1
            jedis.set("prefs:42", "{\"sms\":true}");
            measureAvgNanos("in-process map get", 1_000, () -> local.get("prefs:42"));          // ~20–50 нс
            measureAvgNanos("redis GET (localhost)", 1_000, () -> jedis.get("prefs:42"));       // ~100–300 мкс
            measureAvgNanos("postgres point SELECT", 1_000, () -> {
                try (var c = ds.getConnection();
                     var ps = c.prepareStatement("select prefs from subscriber where id = ?")) {
                    ps.setLong(1, 42L);
                    try (var rs = ps.executeQuery()) { return rs.next() ? rs.getString(1) : null; }
                } catch (java.sql.SQLException e) { throw new IllegalStateException(e); }
            });                                                                                  // ~300 мкс–1 мс
        }
    }

    static com.zaxxer.hikari.HikariDataSource pgDataSource() {
        var cfg = new com.zaxxer.hikari.HikariConfig();
        cfg.setJdbcUrl("jdbc:postgresql://localhost:5432/app");
        cfg.setUsername("app"); cfg.setPassword("app"); cfg.setMaximumPoolSize(1);
        return new com.zaxxer.hikari.HikariDataSource(cfg);
    }
}
```

На ноутбуке три строки различаются примерно в 10 000× и 3×; в реальной сети к двум последним добавь 0.2–0.5 мс. Смысл упражнения — в соотношении, а не в абсолютных значениях.

## Подводные камни
- Цитировать таблицу без единиц: «Redis — это 1» ничего не значит; всегда говори миллисекунды или микросекунды.
- Измерять средние. SLO касаются p99; бюджет ломает переход со средним 1 мс и p99 50 мс (GC, конкуренция за блокировки, холодная страница). Смотри на гистограммы ([[grafana-dashboards-and-red-use]]).
- Игнорировать первый запрос: холодный JIT, пустой пул соединений, холодный буферный кеш БД и новое рукопожатие TLS делают первый вызов в 10–100× медленнее; прогревай пулы и держи keep-alive ([[sockets-and-connection-pooling]]).
- Считать, что облачный регион за углом: пользователь из Бишкека, обращающийся во Франкфурт, платит ~80 мс за каждый round trip; три последовательных round trip уже превышают SLO в 200 мс.
- Верить, что более быстрое железо исправит «болтливую» архитектуру: 100 последовательных вызовов по 1 мс останутся 100 мс на любой машине; настоящие рычаги — пакетирование, параллельные вызовы и кеширование.
- Связанные заметки: [[back-of-envelope-estimation]] — сторона мощности; [[cpu-caches-and-cache-lines]] — верхние ступени лестницы.
