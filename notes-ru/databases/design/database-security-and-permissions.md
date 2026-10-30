---
title: Безопасность баз данных и права доступа
tags: [databases, security, postgres, permissions, least-privilege]
learned: 2026-10-30
---
## Кратко
Защита базы — это слои: убрать её из публичного интернета, требовать **TLS** и сильную аутентификацию (SCRAM, сертификаты) и давать каждому приложению только те **привилегии**, которые ему нужны. Обычная ошибка — одна учётная запись почти суперпользователя, которой все сервисы пользуются для всего. Лучше: отдельные роли на операцию (чтение, создание, обновление, удаление), роль миграций, которая владеет схемой, и роль приложения, которая может выполнять только DML, чтобы SQL-инъекция или скомпрометированный сервис не смогли выполнить `DROP TABLE` или прочитать чужие схемы. REST API перед базой никогда не должен вставлять ввод пользователя в текст SQL и должен сопоставлять эндпоинту самую узкую роль.

## Основное
### Сеть и транспорт
- Postgres слушает там, где указано в `listen_addresses`; привязывай к приватным интерфейсам, закрывай 5432 файрволом, никаких публичных IP ([[network-access-to-databases]]).
- `pg_hba.conf` решает, кто, откуда и каким методом может подключиться, срабатывает первое совпадение: `hostssl app_db app_rw 10.0.0.0/16 scram-sha-256`. Никогда `trust` вне локальной машины разработчика.
- **TLS**: `ssl = on` с сертификатом; клиенты используют `sslmode=verify-full` (проверяет CA и имя хоста). `require` шифрует, но не проверяет, поэтому уязвим для посредника (man-in-the-middle) ([[tls-handshake-and-certificates]], [[mtls-and-service-to-service-auth]]).
- Используй **SCRAM-SHA-256** (по умолчанию с 14); хэши паролей MD5 устарели. Меняй учётные данные через менеджер секретов ([[secrets-management]]).

### Роли и привилегии в Postgres
- Роли — это одновременно пользователи и группы. Привилегии: `CONNECT` на базу, `USAGE`/`CREATE` на схему, `SELECT/INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER` на таблицы, `USAGE` на последовательности, `EXECUTE` на функции.
- С Postgres 15 обычные пользователи по умолчанию больше не могут создавать объекты в схеме `public` (`CREATE` отозван у `PUBLIC`).
- **Минимальные привилегии**: роль приложения не владеет таблицами. Владельцы могут `DROP` и `ALTER`; ролям приложения — только DML.
- Шаблон: `app_owner` (NOLOGIN, владеет схемой, используется миграциями), `app_rw` (DML), `app_ro` (SELECT, для отчётов и реплик на чтение) и по желанию роли на каждую операцию.
- `ALTER DEFAULT PRIVILEGES` автоматически выдаёт нужные привилегии ролям приложения на будущие таблицы, созданные владельцем; иначе каждой миграции нужны GRANT.
- **Построчная безопасность** (row-level security: `ENABLE ROW LEVEL SECURITY` + `CREATE POLICY`) фильтрует строки по арендатору или пользователю ([[multi-tenancy-strategies]]).
- Предопределённые роли: `pg_read_all_data`, `pg_write_all_data` (14+), `pg_monitor` для агентов мониторинга.

### Пулы на каждую операцию (из курса)
- Идея: завести в приложении отдельные пулы соединений, каждый под ролью с одним видом привилегий: пул `reader` для GET-эндпоинтов, `creator` (INSERT), `updater`, `deleter`. Ошибка или инъекция в GET-обработчике тогда вообще не может изменить данные.
- Цена: больше пулов и соединений; часто хороший компромисс — `app_ro` + `app_rw`, с направлением чтений на реплики ([[read-replicas-and-stale-reads]]).

### REST API и база
- Только параметризованные запросы; никогда не собирать SQL из полей запроса ([[database-wire-protocols]], [[owasp-top-10-for-backend]]).
- Белый список колонок для сортировки и фильтрации; `ORDER BY ${userInput}` — тоже инъекция.
- Ограничивать размер страницы и время выражения (`statement_timeout` на роль), чтобы один запрос не мог выполняться 10 минут.
- Не показывать клиентам внутренние id и детали ошибок (текст SQLSTATE); писать их в лог.
- Аудит: `log_connections`, `pgaudit` для DDL и чувствительных таблиц.

## Пример
```sql
-- owner role used only by Flyway
CREATE ROLE app_owner NOLOGIN;
CREATE SCHEMA shop AUTHORIZATION app_owner;

CREATE ROLE app_rw LOGIN PASSWORD :'rw_pw';
CREATE ROLE app_ro LOGIN PASSWORD :'ro_pw';
GRANT CONNECT ON DATABASE shop_db TO app_rw, app_ro;
GRANT USAGE ON SCHEMA shop TO app_rw, app_ro;

-- future tables created by app_owner get the right grants automatically
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA shop
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA shop
  GRANT USAGE ON SEQUENCES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA shop
  GRANT SELECT ON TABLES TO app_ro;

ALTER ROLE app_rw SET statement_timeout = '5s';
```

## Подводные камни
- Приложения подключаются как `postgres` или как владелец таблиц: одна инъекция может удалить схему.
- `sslmode=require` и уверенность, что соединение проверено.
- Забытый `ALTER DEFAULT PRIVILEGES`: таблица из следующей миграции невидима приложению (permission denied в проде).
- Учётные данные базы в `application.yml` в git; используй секреты, передаваемые через окружение.
- Связанное: [[schema-migrations-flyway-liquibase]], [[api-security-checklist]], [[authentication-vs-authorization]].
