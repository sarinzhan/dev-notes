---
title: Чек-лист производительности Postgres для Spring-сервиса
tags: [postgres, performance, monitoring, connection-pool]
learned: 2026-10-27
---
## Кратко
Большинство проблем производительности Postgres в типичном Spring Boot сервисе возникают в пяти местах, в таком порядке: слишком много или слишком мало соединений, отсутствующие или неправильные индексы, N+1 и «болтливый» доступ через ORM, длинные транзакции и неограниченные операторы, устаревшая статистика или раздувание (bloat). Чек-лист ниже — то, что нужно проверить, прежде чем трогать параметры сервера, а `pg_stat_statements` — инструмент, который подскажет, с чего начать. У отдельных тем есть свои заметки; эта — упорядоченный обход для инцидента или ревью перед запуском.

## Основное

### 1. Соединения
- Postgres порождает (fork) один процесс на соединение; каждый стоит памяти и слота в таблице блокировок, а после нескольких сотен накладные расходы планировщика ОС начинают преобладать. Размер пула на экземпляр должен быть небольшим: `connections = cores × 2 + spindles` как отправная точка, обычно 10–20 на экземпляр сервиса ([[hikaricp-pool-sizing]]). Десять pod-ов × 20 = 200 backend-процессов; `max_connections = 300` с запасом для администраторов и репликации.
- Ставь PgBouncer (пулинг на уровне транзакций, transaction pooling) перед базой, когда растёт число сервисов; тогда возможности, основанные на `SET` (advisory-блокировки уровня сессии, подготовленные выражения со старым протоколом), требуют осторожности.
- Проверяй `pg_stat_activity` на `idle in transaction`: транзакция, открытая на время HTTP- или Kafka-вызова. Задай `idle_in_transaction_session_timeout` и вынеси удалённые вызовы из `@Transactional` ([[spring-transactional]]).

### 2. Найти дорогие операторы
- Включи `pg_stat_statements` (`shared_preload_libraries`), затем `SELECT queryid, calls, mean_exec_time, total_exec_time, rows, shared_blks_read FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20`. Сортируй по `total_exec_time` для проблем пропускной способности, по `mean_exec_time` для проблем задержки, по `calls` для N+1.
- `auto_explain` с `log_min_duration = 200ms` и `log_analyze = on` сохраняет в лог реальные планы медленных операторов; `log_min_duration_statement` сам по себе даёт только текст.
- Растущие `pg_stat_user_tables.seq_scan` и `seq_tup_read` на большой таблице означают отсутствующий индекс; `pg_stat_user_indexes.idx_scan = 0` — бесполезный ([[database-indexes]], [[postgres-indexes]]).

### 3. Форма запросов и ORM
- N+1: `calls` в сотнях тысяч для `SELECT ... WHERE id = $1`; исправляется fetch join, `@EntityGraph`, размером пачки ([[jpa-n-plus-one]]).
- Пагинация с `OFFSET 100000` читает и отбрасывает 100 000 строк; используй пагинацию по ключу (keyset pagination) (`WHERE (created_at, id) < (?, ?) ORDER BY created_at DESC, id DESC LIMIT 50`).
- `SELECT *` со столбцом `jsonb` или `text` подтягивает данные TOAST при каждом чтении; выбирай только нужные столбцы, особенно для эндпоинтов списков.
- Большие списки `IN (...)` из `findAllById`: переходи на `= ANY(?)` с параметром-массивом, что сохраняет один план в кэше ([[query-planning-and-execution]]).
- `count(*)` для общего числа в пагинации на больших таблицах — это полное сканирование; оценивай по `pg_class.reltuples` или откажись от общего числа.

### 4. Таймауты и ограничения
| Параметр | Где | Назначение |
|---|---|---|
| `statement_timeout` | на роль или в JDBC URL `options=-c statement_timeout=5s` | ни один запрос не выполняется вечно; отчётные роли получают больший таймаут |
| `lock_timeout` | миграции | DDL быстро падает вместо ожидания в очереди ([[postgres-locks]]) |
| `idle_in_transaction_session_timeout` | сервер | освобождает блокировки и позволяет vacuum продвигаться ([[postgres-vacuum-and-bloat]]) |
| `connection-timeout`, `max-lifetime` | HikariCP | исчерпание пула проявляется ошибкой, а не зависанием |
| `work_mem` | на сессию для отчётов | сортировки и хеши остаются в памяти, не создавая глобального риска для сервера |

### 5. Базовые настройки сервера
- `shared_buffers` ≈ 25 % RAM, `effective_cache_size` ≈ 70 %, `random_page_cost = 1.1` на SSD, `max_wal_size` достаточно большой, чтобы контрольные точки происходили по `checkpoint_timeout`, а не по размеру (`pg_stat_bgwriter.checkpoints_req` должен оставаться низким), `wal_compression = on`.
- Доля попаданий в кэш из `pg_stat_database` (`blks_hit / (blks_hit + blks_read)`) ниже ~99 % для OLTP означает, что рабочий набор не помещается в память или не хватает индекса.
- Autovacuum настроен для каждой горячей таблицы, `ANALYZE` после массовых загрузок, проверка раздувания раз в месяц; таблицы со сроком хранения — партиционировать ([[postgres-partitioning]]).
- Задержка репликации и удержание WAL слотами, если чтения идут с реплик ([[postgres-replication-and-failover]]).

### 6. Наблюдаемость
- Экспортируй `pg_stat_*` через `postgres_exporter` в Prometheus/Mimir и сделай дашборд: соединения по состояниям, транзакции в секунду, доля попаданий в кэш, возраст самой длинной транзакции, задержка репликации, мёртвые кортежи по таблицам, медленные запросы из `pg_stat_statements`. Алерты — на возраст `idle in transaction`, насыщение пула соединений (`hikaricp_connections_pending`) и `age(datfrozenxid)` ([[grafana-dashboards-and-red-use]], [[slo-sli-and-alerting]]).

## Пример
```yaml
# application.yml: ограниченный пул, statement timeout со стороны клиента, лог медленных запросов со стороны сервера
spring:
  datasource:
    url: jdbc:postgresql://pg-primary:5432/esim?options=-c%20statement_timeout=5000%20-c%20idle_in_transaction_session_timeout=30000
    hikari:
      maximum-pool-size: 16
      minimum-idle: 4
      connection-timeout: 3000     # провалить запрос вместо бесконечного ожидания в очереди
      max-lifetime: 1800000        # 30 мин, меньше любого idle timeout у LB/PgBouncer
      leak-detection-threshold: 20000
  jpa:
    properties:
      hibernate.jdbc.batch_size: 50
      hibernate.order_inserts: true
      hibernate.default_batch_fetch_size: 50
```
```sql
-- Еженедельная проверка: самые дорогие операторы по общему времени, с поведением кэша
SELECT calls, round(mean_exec_time::numeric, 2) AS mean_ms,
       round(total_exec_time::numeric / 1000, 1) AS total_s,
       shared_blks_hit, shared_blks_read, left(query, 80) AS query
FROM pg_stat_statements
ORDER BY total_exec_time DESC LIMIT 10;
```

## Подводные камни
- Увеличивать `maximum-pool-size`, чтобы «починить» медленные запросы: больше backend-процессов конкурируют за тот же CPU и те же блокировки, пропускная способность падает, а база становится единственным перегруженным ресурсом для всех сервисов.
- `statement_timeout` задан только в приложении: у миграции или разового запроса аналитика нет ограничения. Задай значение по умолчанию на роль.
- Настраивать `shared_buffers` и `work_mem` до того, как посмотреть в `pg_stat_statements`: самый дорогой запрос обычно исправляется индексом или устранением N+1, и никакой параметр это не заменит.
- Смотреть на `mean_exec_time` без `calls`: проблема — запрос на 2 мс, вызываемый 5 миллионов раз в день, а не отчёт на 3 с раз за ночь.
- Связанные заметки: [[explain-analyze]] — для отдельного запроса, [[database-scaling-path]] — что делать, когда чек-лист исчерпан.
