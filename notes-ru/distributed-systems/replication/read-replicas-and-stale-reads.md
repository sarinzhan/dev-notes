---
title: Реплики для чтения и устаревшие чтения на практике
tags: [distributed-systems, replication, postgres, spring]
learned: 2026-10-27
---
## Кратко
Реплики для чтения (read replicas) — первый шаг масштабирования для сервиса с преобладанием чтений: направлять `SELECT` на асинхронные follower, а primary оставить для записей. Цена — **отставание репликации** (replication lag), которое превращает каждое чтение с реплики в чтение с согласованностью в конечном счёте. Основная инженерная работа — решить, **какие чтения могут быть устаревшими** (каталог, история, дашборды), а **какие нет** (то, что пользователь только что изменил, всё, что используется для принятия решения), и маршрутизировать соответственно, с запасным вариантом, когда отставание превышает порог.

## Основное

### Откуда берётся отставание
- Задержка сети и применения: обычно миллисекунды.
- Реплика занята: долгий отчётный запрос на standby Postgres конфликтует с применением WAL; при `hot_standby_feedback=off` запрос отменяется, при включённом — primary хранит мёртвые кортежи и раздувается ([[postgres-vacuum-and-bloat]]).
- Всплески на primary: массовый `UPDATE`, построение индекса, миграция генерируют WAL быстрее, чем реплика его применяет.
- Рестарт реплики или сетевое разделение: отставание вырастает до минут; с точки зрения балансировщика реплика «здорова» и отдаёт данные из прошлого ([[failure-modes-and-timeouts]]).

### Классификация чтений
| Чтение | Устаревание допустимо? | Куда направлять |
|---|---|---|
| Каталог товаров, список тарифов, справочные данные | да (от секунд до минут) | реплика, даже кэш ([[caching]]) |
| Собственный профиль пользователя сразу после редактирования | нет | primary или реплика с ожиданием LSN |
| Баланс перед списанием, остаток перед резервированием | нет, это решение | primary внутри пишущей транзакции (`SELECT ... FOR UPDATE`) ([[postgres-locks]]) |
| Отчёты, выгрузки, аналитика | да | выделенная реплика с `hot_standby_feedback` и длинными таймаутами |
| Результаты поиска, ленты | да, с монотонными чтениями на пользователя | реплика, «липкая» на сессию |

- «Чтения для решений» — опасная категория: прочитать устаревший баланс на реплике, а затем записать на primary — это потерянное обновление (lost update) в замедленной съёмке, и никакой уровень изоляции не поможет, потому что два выражения выполняются на разных узлах ([[postgres-isolation-levels]]).

### Стратегии маршрутизации
- **Статически по сценарию**: отдельный `DataSource`/репозиторий для сценариев только на чтение; самый простой и явный вариант ([[read-write-splitting-and-cqrs-lite]]).
- **По атрибуту транзакции**: Spring `@Transactional(readOnly = true)` плюс маршрутизирующий `DataSource` (`AbstractRoutingDataSource` или `LazyConnectionDataSourceProxy` с ключом по `TransactionSynchronizationManager.isCurrentTransactionReadOnly()`). Легко применить, легко ошибиться: метод `readOnly`, вызванный сразу после записи в том же запросе, читает устаревшие данные.
- **На уровне прокси**: PgBouncer/pgpool/ProxySQL или reader endpoint облака разделяют по типу выражения; непрозрачно для приложения и ничего не знает о чтении своих записей.
- **С учётом отставания**: проверка здоровья опрашивает `pg_last_wal_replay_lsn()` или `now() - pg_last_xact_replay_timestamp()`; реплики сверх порога исключаются из пула чтения ([[load-balancing]]).

### Как дать пользователям чтение своих записей
- **Закрепление после записи**: после записи направлять чтения этого пользователя на primary в течение N секунд (cookie или ключ в Redis). Просто, немного избыточная маршрутизация.
- **LSN-токен**: запись возвращает `pg_current_wal_insert_lsn()`; последующие чтения ждут на реплике, пока `pg_last_wal_replay_lsn() >= token`, или откатываются на primary. Точно, но больше кода (реализация — в [[consistency-models]]).
- **Монотонные чтения**: хешировать id пользователя в реплику, чтобы одна сессия никогда не прыгала между репликами с разным отставанием.
- Если в цепочке есть Kafka: consumer, который читает с реплики, чтобы «обогатить» событие, которое он сам только что вызвал, может не увидеть собственную запись; в consumer читай с primary или передавай нужные данные в самом событии ([[event-driven-architecture]]).

### Наблюдаемость
- Выставляй отставание в байтах и секундах по каждой реплике; алерт — на пороге, привязанном к обещанию продукта (если интерфейс говорит «обновления в течение 5 с», алерт на 3 с) ([[slo-sli-and-alerting]]).
- Помечай запросы целью (комментарии `/* replica */` или атрибут span `db.role`), чтобы медленное или устаревшее чтение можно было атрибутировать в трейсах ([[distributed-tracing]]).

## Пример
Маршрутизирующий `DataSource` для Spring Boot: транзакции только на чтение идут в пул реплики, всё остальное — на primary; реплика обходится, когда её отставание превышает лимит.
```java
@Configuration
public class DataSourceConfig {
    @Bean @Primary
    DataSource routingDataSource(@Qualifier("primary") DataSource primary,
                                 @Qualifier("replica") DataSource replica,
                                 ReplicaLagMonitor lag) {
        var routing = new AbstractRoutingDataSource() {
            @Override protected Object determineCurrentLookupKey() {
                boolean readOnly = TransactionSynchronizationManager.isCurrentTransactionReadOnly();
                return readOnly && lag.withinLimit(Duration.ofSeconds(2)) ? "replica" : "primary";
            }
        };
        routing.setTargetDataSources(Map.of("primary", primary, "replica", replica));
        routing.setDefaultTargetDataSource(primary);
        // LazyConnectionDataSourceProxy откладывает getConnection() до первого выражения,
        // поэтому флаг readOnly уже выставлен, когда вычисляется ключ поиска
        return new LazyConnectionDataSourceProxy(routing);
    }
}

@Service
public class TariffQueryService {
    @Transactional(readOnly = true)       // -> реплика, когда отставание в норме
    public List<Tariff> listActive() { ... }
}
```
`ReplicaLagMonitor` каждую секунду выполняет `SELECT extract(epoch from now() - pg_last_xact_replay_timestamp())` на реплике. Без `LazyConnectionDataSourceProxy` Spring получил бы соединение до того, как `readOnly` станет видимым, и всё шло бы на primary ([[spring-transactional]], [[hikaricp-pool-sizing]]).

## Подводные камни
- `@Transactional(readOnly = true)` на методе, вызываемом из пишущей транзакции: побеждает внешняя транзакция (propagation `REQUIRED`), и чтение всё равно идёт на primary; и наоборот, вызов только на чтение верхнего уровня сразу после отдельной записи попадает на реплику и может её не увидеть.
- Реплика используется и как цель failover, *и* для масштабирования чтений: во время failover теряются одновременно и standby, и мощность чтения.
- Долгие отчёты на реплике без `hot_standby_feedback`: запросы отменяются с «conflict with recovery»; с ним включённым — раздувание на primary. Выдели аналитике собственную реплику ([[postgres-replication-and-failover]]).
- Полагаться на `now()` реплики для проверок TTL или истечения, когда её часы и позиция применения отличаются от primary ([[time-and-clocks]]).
- Связанные заметки: [[leader-follower-replication]] — механика отставания, [[database-scaling-path]] — что делать, когда реплик мало, [[distributed-cache-coherence]] — та же проблема устаревания уровнем выше.
