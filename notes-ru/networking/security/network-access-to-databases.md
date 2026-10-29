---
title: Сетевой контроль доступа к серверам баз данных
tags: [networking, security, postgres, firewall, pg-hba]
learned: 2026-10-29
---
## Кратко
База данных должна быть доступна только тем сервисам, которым она нужна, по частной сети и никогда из интернета. Доступ контролируется слоями: где сервер слушает (`listen_addresses`), каким сетям разрешено достучаться до порта (файрвол, security groups, частные подсети), кто, откуда и как может аутентифицироваться (`pg_hba.conf`), шифруется ли трафик (TLS) и что каждой роли разрешено после подключения (минимальные привилегии, least privilege). Люди попадают в продакшен через бастион или SSH-туннель, а не через открытый порт. Каждый слой исходит из того, что предыдущий уже пробит.

## Основное
### Слои снаружи внутрь
| Слой | Настройка Postgres / инфраструктуры | Что блокирует |
|---|---|---|
| Размещение в сети | частная подсеть, нет публичного IP, нет маршрута от интернет-шлюза | весь интернет |
| Пакетный фильтр | security group / `nftables`: 5432 разрешён только из подсети приложения или его security group | другие хосты в VPC, боковое перемещение |
| Адрес прослушивания | `listen_addresses = '10.20.5.10'` вместо `'*'` | непредусмотренные интерфейсы (публичная сетевая карта, мост Docker) |
| Аутентификация по хосту | `pg_hba.conf`: тип, база, пользователь, адрес, метод | чужой IP клиента, чужая база, слабый метод аутентификации |
| Шифрование | `ssl = on`, строки `hostssl`, `sslmode=verify-full` на клиенте | прослушивание и man-in-the-middle на пути ([[tls-handshake-and-certificates]]) |
| Авторизация | роли с `GRANT` на конкретные схемы, никакого superuser для приложений | взломанное приложение удаляет таблицы или читает данные других сервисов |

### `listen_addresses` и порт
- По умолчанию `localhost`: только локальные подключения. Значение `'*'` привязывает все интерфейсы, включая публичный, если он есть у хоста. Когда БД работает на ВМ, явно указывай частный адрес.
- Смена порта с 5432 — не защита; сканеры находят его за секунды. Защита — порт, закрытый файрволом.
- В Docker `ports: ["5432:5432"]` публикует порт на `0.0.0.0` хоста, а iptables-правила Docker обходят `ufw`. Используй `127.0.0.1:5432:5432` или вообще не публикуй порт и давай сервисам ходить к БД по сети Compose ([[docker-networking]]).

### `pg_hba.conf`
- Каждая строка: `TYPE DATABASE USER ADDRESS METHOD`. Побеждает **первая** подходящая строка сверху вниз; перехода к следующим нет. Конкретные строки ставь первыми, а последней — строку `reject`, если нужен явный запрет.
- `TYPE`: `local` (Unix-сокет), `host` (TCP с TLS или без), `hostssl` (только TCP с TLS), `hostnossl`.
- `METHOD`: `scram-sha-256` (по умолчанию с Postgres 14; используй его), `md5` (устаревший), `cert` (клиентский сертификат, см. [[mtls-and-service-to-service-auth]]), `peer` (пользователь ОС = пользователь БД, только локально), `reject` и `trust` (без пароля; никогда для TCP).
- Перечитать конфигурацию: `SELECT pg_reload_conf();`, а то, что Postgres на самом деле разобрал, смотри в `pg_hba_file_rules` (ошибки — в колонке `error`).

### Частные подсети, бастион и туннели
- Типичная схема в облаке: балансировщик в публичной подсети, приложение в частной, база в отдельной подсети данных без NAT-маршрута наружу ([[ip-addressing-and-nat]], [[network-routing]]). Управляемые базы (RDS, Cloud SQL) дают тот же выбор: «publicly accessible: no».
- Люди и задачи миграций подключаются через бастион или локальный проброс SSH: `ssh -L 15432:db.internal:5432 bastion`, затем `psql -h 127.0.0.1 -p 15432`. Открыт только SSH-порт бастиона, защищённый ключами и журналом аудита. Облачные альтернативы (SSM Session Manager, IAP-туннели, VPN) обходятся даже без этого открытого порта.

### Прокси соединений
- PgBouncer (или RDS Proxy, Pgpool-II) стоит между приложениями и Postgres: он мультиплексирует множество клиентских соединений на небольшой пул серверных ([[hikaricp-pool-sizing]], [[sockets-and-connection-pooling]]). Для контроля доступа он становится единственным хостом в `pg_hba.conf`, так что база принимает ровно один адрес источника.
- У прокси своя аутентификация (`auth_type = scram-sha-256`, `auth_query`) и свои настройки TLS с обеих сторон (`client_tls_*`, `server_tls_*`); если завершить TLS на PgBouncer и ходить в Postgres открытым текстом, шифрование на этом участке теряется.
- L4-балансировщик перед репликами тоже подходит ([[load-balancers-l4-vs-l7]]), но он скрывает IP клиентов от `pg_hba.conf`, если не поддерживается PROXY protocol.

### Роли с минимальными привилегиями
- Одна роль на сервис, владеющая только своей схемой. Роль миграций (DDL, используется Flyway при deploy) отдельно от рабочей роли (только DML) ([[schema-migrations-flyway-liquibase]]).
- `REVOKE CREATE ON SCHEMA public FROM PUBLIC` (по умолчанию с Postgres 15), `CONNECT` выдаётся на каждую базу отдельно, роли только для чтения — для отчётов и поддержки.
- Учётные данные — из хранилища секретов, с ротацией, никогда не в образе ([[secrets-management]]).

## Пример
Postgres на частной ВМ: привязка к частному адресу, обязательные TLS и SCRAM из подсети приложения, для рабочей роли — только PgBouncer, и проверка.
```bash
# postgresql.conf
listen_addresses = '10.20.5.10'
ssl = on
ssl_cert_file = '/etc/postgresql/tls/server.crt'
ssl_key_file  = '/etc/postgresql/tls/server.key'
password_encryption = scram-sha-256

# pg_hba.conf  (побеждает первое совпадение)
# TYPE    DATABASE  USER          ADDRESS          METHOD
local     all       postgres                       peer
hostssl   esim      esim_migrate  10.20.6.0/24     scram-sha-256
hostssl   esim      esim_app      10.20.6.20/32    scram-sha-256   # только PgBouncer
hostssl   esim      esim_readonly 10.20.9.0/24     scram-sha-256   # подсеть бастиона / BI
host      all       all           0.0.0.0/0        reject

# файрвол хоста: 5432 только из подсетей приложения и бастиона
sudo nft add rule inet filter input tcp dport 5432 ip saddr '{ 10.20.6.0/24, 10.20.9.0/24 }' accept
sudo nft add rule inet filter input tcp dport 5432 drop

# проверка с ноутбука через бастион
ssh -fN -L 15432:10.20.5.10:5432 bastion.internal
psql "host=127.0.0.1 port=15432 dbname=esim user=esim_readonly sslmode=require" \
     -c "select rule_number, type, database, user_name, address, auth_method, error from pg_hba_file_rules;"
```
```sql
-- рабочая роль: только данные, только своя схема
CREATE ROLE esim_app LOGIN PASSWORD :'app_pw';
GRANT CONNECT ON DATABASE esim TO esim_app;
GRANT USAGE ON SCHEMA esim TO esim_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA esim TO esim_app;
ALTER DEFAULT PRIVILEGES FOR ROLE esim_migrate IN SCHEMA esim
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO esim_app;
```

## Подводные камни
- `host all all 0.0.0.0/0 md5` плюс публичный IP: подбор паролей со всего интернета; тысячи открытых инстансов Postgres и Redis ежегодно захватывают именно так.
- `sslmode=require` шифрует, но не проверяет сертификат; только `verify-full` (JDBC: `sslmode=verify-full`, `sslrootcert=...`) останавливает man-in-the-middle.
- Строка `trust`, оставшаяся после локальной отладки и совпадающая с TCP-диапазоном раньше настоящих правил: побеждает первое совпадение.
- Публикация порта БД в Docker Compose «только для локальных инструментов» на общем сервере: DNAT-правило iptables игнорирует файрвол хоста.
- Тайм-ауты вместо «connection refused» обычно означают отбрасывание пакетов security group или файрволом, а `no pg_hba.conf entry for host` — что сеть в порядке и отказал HBA ([[network-debugging-toolkit]]).
- Связанные заметки: [[api-security-checklist]], [[owasp-top-10-for-backend]], [[postgres-replication-and-failover]] (соединениям репликации нужны свои строки HBA с `replication`).
