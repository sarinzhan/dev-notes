---
title: Потоковая репликация и механика failover в Postgres
tags: [postgres, replication, high-availability, wal]
learned: 2026-10-27
---
## Кратко
Postgres реплицирует данные, передавая **журнал предзаписи (write-ahead log, WAL)** с primary на standby. Физическая потоковая репликация копирует WAL на уровне байтов, так что standby — точные read-only копии; логическая репликация декодирует WAL в изменения строк для выбранных таблиц и может работать между разными версиями. По умолчанию репликация асинхронная; `synchronous_commit` плюс `synchronous_standby_names` заставляют коммит ждать одного или нескольких standby. Сам Postgres не выбирает новый primary; это делает инструмент вроде Patroni. Теория репликации leader-follower, задержки и потерянных записей — в [[leader-follower-replication]]; эта заметка — про механику Postgres.

## Основное

### Физическая потоковая репликация
- Standby стартует с базового бэкапа (`pg_basebackup`) и подключается через репликационное соединение (`primary_conninfo`); процесс **walsender** на primary передаёт записи WAL потоком, **walreceiver** на standby записывает их, а процесс startup непрерывно их воспроизводит («горячий резерв», hot standby: доступен для чтения во время воспроизведения).
- Реплицируется всё: все базы кластера, DDL, индексы, vacuum. Standby должен быть той же мажорной версии и архитектуры.
- Standby работает **только на чтение**; запись падает с `cannot execute INSERT in a read-only transaction`. Spring направляет read-only трафик через маршрутизирующий `DataSource` ([[read-replicas-and-stale-reads]], [[read-write-splitting-and-cqrs-lite]]).
- **Слоты репликации (replication slots)** (`pg_create_physical_replication_slot`) заставляют primary хранить WAL, пока standby его не прочитает, так что standby, недоступный час, может догнать, а не требовать нового базового бэкапа. Цена: мёртвый слот хранит WAL вечно и заполняет диск; `max_slot_wal_keep_size` (PG 13+) ограничивает это и помечает слот как `lost`.
- Каскадная репликация: standby может питать другие standby, чтобы разгрузить сеть primary.

### Синхронная и асинхронная
| `synchronous_commit` | Коммит возвращается, когда | Что теряется при падении primary | Типичное применение |
|---|---|---|---|
| `off` | WAL буферизован локально | последние ~200 мс коммитов | массовые загрузки, некритичные данные |
| `local` (поведение по умолчанию без синхронного standby) | WAL сброшен через fsync на primary | локально ничего; всё, что ещё не передано, если умрёт диск primary | по умолчанию |
| `remote_write` | standby получил WAL и записал в кэш ОС | только если оба упадут одновременно | HA с небольшой ценой по задержке |
| `on` | standby сбросил WAL через fsync | ничего | деньги, заказы |
| `remote_apply` | standby воспроизвёл WAL, изменения видны запросам | ничего, плюс read-your-writes на standby | чтения направляются на реплики |

- `synchronous_standby_names = 'FIRST 1 (replica1, replica2)'` или `'ANY 1 (...)'` указывает, какие standby учитываются. Если ни один из перечисленных standby не подключён, **коммиты зависают**: синхронной конфигурации нужно минимум два standby или оператор, готовый переключить настройку.
- `synchronous_commit` можно задать на уровне транзакции (`SET LOCAL synchronous_commit = off` для вставки аудита), что дешевле глобального выбора.

### Мониторинг задержки
- На primary: `pg_stat_replication` даёт для каждого standby `sent_lsn`, `write_lsn`, `flush_lsn`, `replay_lsn` и `write_lag`/`flush_lag`/`replay_lag`.
- На standby: `pg_last_wal_receive_lsn()`, `pg_last_wal_replay_lsn()`, `now() - pg_last_xact_replay_timestamp()` как оценка задержки по времени (вводит в заблуждение при простаивающем primary).
- Воспроизведение может отставать от получения, когда на standby выполняются длинные запросы, конфликтующие с vacuum: `max_standby_streaming_delay` решает, отменить запрос (`canceling statement due to conflict with recovery`) или задержать воспроизведение. `hot_standby_feedback = on` говорит primary не очищать vacuum-ом строки, ещё нужные standby, ценой раздувания на primary ([[postgres-vacuum-and-bloat]]).

### Failover и switchover
- **Повышение (promotion)**: `pg_ctl promote` или `SELECT pg_promote()` превращает standby в primary на новой линии времени (timeline). Старый primary больше никогда не должен принимать запись без пересборки (`pg_rewind` может его пересинхронизировать, если WAL доступен), иначе получится split brain.
- Встроенных выборов лидера в Postgres нет. **Patroni** работает рядом с каждым экземпляром, держит аренду лидера в etcd/Consul/Kubernetes, при истечении аренды повышает самый актуальный standby и переписывает `primary_conninfo` на остальных. Клиенты попадают на текущего лидера через health check HAProxy к REST API Patroni или через сервис Kubernetes, метки которого меняет Patroni ([[quorums-and-leader-election]], [[leases-heartbeats-and-health]]).
- Асинхронный failover теряет непереданный хвост WAL: коммиты, которые приложение уже подтвердило пользователям. `maximum_lag_on_failover` в Patroni отказывается повышать слишком отставший standby; синхронный режим (`synchronous_mode: true`) убирает потери ценой задержки.
- В строке подключения можно перечислить несколько хостов с `target_session_attrs=read-write` (PG 10+); JDBC-драйвер переподключается к тому хосту, который принимает запись.

### Логическая репликация
- `CREATE PUBLICATION` на источнике, `CREATE SUBSCRIPTION` на приёмнике; walsender декодирует WAL во вставки/обновления/удаления (плагин `pgoutput`) через логический слот. Для обновлений и удалений таблицам нужен `REPLICA IDENTITY` (по умолчанию первичный ключ).
- Сценарии: обновление мажорной версии с минимальным простоем, репликация подмножества таблиц в отчётную базу, наполнение Debezium/CDC для [[outbox-pattern]]. DDL и последовательности (до PG 17) не реплицируются; миграции нужно выполнять на обеих сторонах.

## Пример
```bash
# Задержка по каждому standby, выполнять на primary
psql -x -c "SELECT application_name, state, sync_state,
                   pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS replay_bytes_behind,
                   replay_lag
            FROM pg_stat_replication;"
# -[ RECORD 1 ]-------+----------------
# application_name    | replica1
# state               | streaming
# sync_state          | sync
# replay_bytes_behind | 16384
# replay_lag          | 00:00:00.004

# Слоты, которые держат WAL на диске, даже когда их никто не читает
psql -c "SELECT slot_name, active, wal_status,
                pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
         FROM pg_replication_slots;"
```
```yaml
# фрагмент patroni.yml: один синхронный standby, отказ от failover при задержке больше 1 MB
bootstrap:
  dcs:
    synchronous_mode: true
    maximum_lag_on_failover: 1048576
    postgresql:
      parameters:
        wal_level: replica
        max_wal_senders: 10
        hot_standby_feedback: "on"
```

## Подводные камни
- Осиротевший слот репликации (выведенная из эксплуатации реплика, остановленный коннектор Debezium) заполняет том с данными WAL-ом; поднимай алерт по `pg_replication_slots` и задавай `max_slot_wal_keep_size`.
- Чтение с асинхронной реплики сразу после записи: строки там ещё нет. Направляй чтение после записи на primary или используй `remote_apply`.
- Считать `synchronous_commit = on` с одним standby «высокой доступностью»: когда этот standby умирает, каждый коммит блокируется, пока кто-нибудь не вмешается.
- Повысить standby, пока старый primary ещё работает и доступен клиентам: два primary, разошедшиеся данные, никакого автоматического слияния. Изолируй (fence) старый узел (Patroni это делает, самописные скрипты — редко).
- Теория и семантика задержки: [[leader-follower-replication]]; место этого на пути масштабирования: [[database-scaling-path]].
