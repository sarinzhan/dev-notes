---
title: Сетевые протоколы баз данных (Postgres и MongoDB)
tags: [databases, protocols, postgres, mongodb, networking]
learned: 2026-10-30
---
## Кратко
Драйвер базы общается с сервером по TCP через бинарный **сетевой протокол** (wire protocol): сообщения с префиксом длины и байтом типа. Postgres использует собственный **протокол frontend/backend v3**: обмен при старте и аутентификация, затем либо поток **простого запроса** (simple query: одна строка SQL, текстовые результаты), либо поток **расширенного запроса** (extended query: Parse/Bind/Execute/Sync — подготовленные выражения, бинарные параметры, конвейерная отправка). MongoDB использует **OP_MSG** с документами BSON. Знание протокола объясняет поведение подготовленных выражений, почему PgBouncer ломает некоторые возможности, как bind-параметры защищают от SQL-инъекций и откуда берутся сетевые круговые задержки.

## Основное
### Протокол Postgres v3
- Каждое сообщение: 1 байт типа, 4-байтная длина (включая саму себя), полезная нагрузка. У стартового сообщения байта типа нет.
- **Старт**: клиент отправляет `StartupMessage` (протокол 3.0, `user`, `database`, `application_name`…). Перед ним может идти `SSLRequest`, чтобы перейти на TLS ([[tls-handshake-and-certificates]]).
- **Аутентификация**: сервер отвечает `AuthenticationSASL` (SCRAM-SHA-256, по умолчанию с Postgres 14), `AuthenticationMD5Password` (устаревшее) или `AuthenticationOk` для trust. Затем сообщения `ParameterStatus` (server_version, client_encoding, TimeZone…), `BackendKeyData` (pid + секрет, нужен для отмены запроса через отдельное соединение) и `ReadyForQuery` со статусом транзакции: `I` простаивает, `T` в транзакции, `E` транзакция с ошибкой.
- **Простой запрос**: `Query("SELECT ...")` → `RowDescription` (имена колонок и OID типов) → `DataRow` × n (значения текстом) → `CommandComplete("SELECT 3")` → `ReadyForQuery`. Строка может содержать несколько выражений — именно этим и пользуется SQL-инъекция.
- **Расширенный запрос**: `Parse` (SQL с `$1, $2`, необязательное имя) → `Bind` (значения параметров, текст или бинарно; форматы результатов) → `Describe` → `Execute` (с лимитом строк, так что результат можно забирать частями) → `Sync`. Параметры никогда не разбираются как SQL, поэтому не могут внедрить код. JDBC и большинство драйверов используют этот поток.
- **Конвейер** (pipelining): клиент может отправить много Parse/Bind/Execute, не дожидаясь ответов, и один Sync в конце; сервер отвечает по порядку. Один круговой обмен на пачку (batch в JDBC, pipeline в pgx, режим pipeline в libpq с Postgres 14).
- Подпротокол **COPY** потоково передаёт строки массово (`CopyData`), намного быстрее INSERT.
- Ошибки приходят как `ErrorResponse` с полями: уровень, код SQLSTATE (`23505` нарушение уникальности, `40001` сбой сериализации, `40P01` взаимоблокировка), сообщение, детали, подсказка.
- Соединение = один backend-процесс на сервере ([[postgres-architecture]]); TCP 5432.

### Протокол MongoDB
- Заголовок сообщения: `messageLength`, `requestID`, `responseTo`, `opCode`. С MongoDB 3.6 основной opcode — **OP_MSG** (opcode 2013); старые OP_QUERY/OP_INSERT удалены в 5.1+.
- Тело OP_MSG: флаги и секции с документами **BSON**. Команда — это документ: `{ find: "orders", filter: { status: "NEW" }, limit: 10, $db: "shop" }`. Результаты возвращаются документами с id курсора; `getMore` забирает следующую пачку ([[database-cursors]]).
- Секция типа 1 несёт последовательность документов (например, много документов для массовой вставки), не оборачивая их в один огромный массив.
- Рукопожатие: команда `hello` (раньше `isMaster`) возвращает топологию: primary, участников replica set, максимальный размер сообщения (48 МБ), максимальный размер BSON (16 МБ).
- TCP 27017; TLS и аутентификация SCRAM / x.509.

### Самое большое SQL-выражение, которое можно отправить
- Postgres: длина сообщения — 32-битное целое, но сервер ограничивает строку запроса примерно 1 ГБ (`MaxAllocSize`), и одно значение параметра — 1 ГБ. Огромный список `IN (...)` или `VALUES` на 100 000 строк работает, но медленно разбирается и съедает память; протокол bind ограничивает число параметров **65 535** на выражение (16-битный счётчик).
- MySQL: `max_allowed_packet` (64 МБ по умолчанию в 8.0) ограничивает выражение или строку.
- Практический ответ: большие данные отправлять через COPY, пачками от нескольких сотен до нескольких тысяч строк или через `unnest($1::bigint[])` с одним параметром-массивом.

```java
// one array parameter instead of 50,000 placeholders
try (PreparedStatement ps = conn.prepareStatement(
        "SELECT id, status FROM orders WHERE id = ANY(?)")) {
    ps.setArray(1, conn.createArrayOf("bigint", ids.toArray()));
    try (ResultSet rs = ps.executeQuery()) { while (rs.next()) handle(rs); }
}
```

## Пример
```bash
# watch the protocol: run a query and capture port 5432 (no TLS)
sudo tcpdump -i lo -A -s0 'tcp port 5432'
psql "host=127.0.0.1 sslmode=disable user=app dbname=shop" -c "SELECT 1"
# you will see: StartupMessage (user, database), 'R' auth, 'S' ParameterStatus,
# 'K' BackendKeyData, 'Z' ReadyForQuery, 'Q' SELECT 1, 'T' RowDescription,
# 'D' DataRow, 'C' CommandComplete "SELECT 1", 'Z' ReadyForQuery
```

## Подводные камни
- Вклеивать значения в текст SQL (простой запрос) вместо bind: SQL-инъекция и нет переиспользования плана.
- Именованные подготовленные выражения через PgBouncer в режиме transaction: следующая транзакция может попасть на другой backend, у которого этого выражения нет (PgBouncer 1.21+ умеет отслеживать подготовленные выражения на уровне протокола).
- Превысить 65 535 bind-параметров в одном сгенерированном `INSERT ... VALUES`: драйвер бросит исключение; делить на пачки.
- Забирать огромный результат без лимита строк или курсора: драйвер буферизует всё в памяти ([[database-cursors]]).
- Связанное: [[network-access-to-databases]], [[sockets-and-connection-pooling]], [[tcpdump]], [[database-security-and-permissions]].
