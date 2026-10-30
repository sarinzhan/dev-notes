---
title: Движки баз данных (storage engines)
tags: [databases, storage-engines, innodb, rocksdb, lsm]
learned: 2026-10-30
---
## Кратко
**Движок базы данных** (storage engine, встраиваемая база) — это библиотека, которая на самом деле хранит байты на диске и выполняет CRUD: она отвечает за страницы, индексы, транзакции и восстановление после сбоя. **СУБД** (MySQL, MariaDB, CockroachDB) — это сервер вокруг неё: парсер SQL, планировщик, сетевой протокол, пользователи, репликация. Некоторые СУБД позволяют выбрать движок для каждой таблицы (MySQL: InnoDB, MyISAM, MyRocks); у других он один встроенный (Postgres). Движки делятся на два семейства: движки на **B+-дереве** (InnoDB, SQLite, BerkeleyDB, WiredTiger), оптимизированные под чтение, и движки на **LSM-дереве** (LevelDB, RocksDB), оптимизированные под запись.

## Основное
### СУБД, движок и встраиваемая база
- Движок предоставляет низкоуровневый API: `get(key)`, `put(key, value)`, итераторы, транзакции. Ни SQL, ни сети.
- «Встраиваемый» значит, что движок работает внутри твоего процесса как библиотека: без перехода клиент-сервер. SQLite, RocksDB, LevelDB, BerkeleyDB так и используются напрямую.
- Многие системы — это фронтенд над встраиваемым движком: MySQL над InnoDB, MongoDB над WiredTiger, CockroachDB и TiKV над RocksDB/Pebble, state store в Kafka Streams над RocksDB.

### Движки
| Движок | Структура | Транзакции | Заметки |
|---|---|---|---|
| **MyISAM** | B-tree индексы, файл данных heap (`.MYD`) со смещениями | нет | исходный движок MySQL по умолчанию; блокировки на уровне таблицы; быстрые count и вставки в конец; сбой может испортить таблицы (нужен repair); нет внешних ключей. Перестал быть движком по умолчанию в MySQL 5.5 |
| **Aria** | преемник MyISAM | частично | устойчивая к сбоям замена MyISAM в MariaDB, используется для внутренних временных таблиц |
| **InnoDB** | B+-дерево, кластеризованное по PK | полный ACID, MVCC | движок MySQL по умолчанию с 5.5; блокировки строк, внешние ключи, журналы redo + undo, doublewrite buffer ([[primary-vs-secondary-indexes]]) |
| **XtraDB** | форк InnoDB | полные | улучшенный InnoDB от Percona; был движком MariaDB по умолчанию до 10.2, затем MariaDB вернулась к InnoDB |
| **SQLite** | B+-дерево, один файл | полный ACID (режим WAL) | встраиваемая SQL-база; самая распространённая база в мире (телефоны, браузеры); один писатель одновременно |
| **BerkeleyDB** | B-tree / хэш | да | старый встраиваемый key-value движок (1990-е); был движком MySQL; сейчас принадлежит Oracle |
| **LevelDB** | LSM-дерево | только атомарные пачки | Google, 2011, авторы Джефф Дин и Санджай Гемават; один процесс, уровни SSTable |
| **RocksDB** | LSM-дерево | да | форк LevelDB от Facebook (2012): многопоточный compaction, column families, транзакции; используется в MyRocks, Kafka Streams, CockroachDB (до Pebble), TiKV, Flink |
| **WiredTiger** | B+-дерево (есть вариант LSM) | да, MVCC | движок MongoDB с 3.2 ([[mongodb-basics]]) |

### B+-дерево и LSM-дерево
- **B+-дерево** обновляет страницы на месте ([[b-tree-vs-b-plus-tree]]): чтение — 3–4 чтения страниц, запись — случайный IO плюс WAL.
- **LSM-дерево** (log-structured merge): записи идут в WAL и в отсортированную таблицу в памяти (**memtable**); когда она заполнится, она сбрасывается в неизменяемый отсортированный файл (**SSTable**) на уровне 0. Фоновый **compaction** сливает SSTable в более крупные уровни, выбрасывая перезаписанные и удалённые ключи (tombstones).
- Чтение проверяет memtable, затем каждый уровень; **фильтры Блума** на каждый SSTable пропускают файлы, в которых ключа точно нет ([[bloom-filters-and-probabilistic]]).
- Компромисс: LSM даёт последовательную запись и высокую пропускную способность записи, хорошее сжатие, но чтение может затронуть несколько файлов (усиление чтения), а compaction переписывает данные много раз (усиление записи, [[wal-redo-undo-and-durability]]). B+-дерево даёт предсказуемое чтение.
- Правило: нагрузка с большим числом записей, в основном дописывание, временные ряды или key-value → LSM; OLTP с преобладанием чтения и диапазонными запросами → B+-дерево. Cassandra и ScyllaDB тоже на LSM ([[column-and-wide-column-stores]]).

### Смена движка в MySQL
- `ENGINE=` для каждой таблицы; `ALTER TABLE t ENGINE=InnoDB` перестраивает таблицу в новом движке.
- Каждый движок держит свои файлы и возможности: таблица MyISAM молча игнорирует `BEGIN/ROLLBACK`, поэтому транзакция, смешивающая таблицы InnoDB и MyISAM, транзакционна лишь наполовину.
- MyRocks (MySQL с RocksDB) сэкономил Facebook около половины места по сравнению с InnoDB для базы пользователей благодаря сжатию и меньшей фрагментации.

## Пример
```sql
-- MySQL: which engines does this server have, and what does each table use?
SHOW ENGINES;
SELECT table_name, engine FROM information_schema.tables WHERE table_schema = 'shop';

CREATE TABLE audit_log (id BIGINT AUTO_INCREMENT PRIMARY KEY, msg TEXT) ENGINE=MyISAM;
START TRANSACTION;
INSERT INTO audit_log(msg) VALUES ('test');
ROLLBACK;                          -- the row is still there: MyISAM has no transactions
ALTER TABLE audit_log ENGINE=InnoDB;  -- rebuild as transactional
```

## Подводные камни
- Оставшиеся таблицы MyISAM в схеме на InnoDB: нет транзакций, нет внешних ключей, блокировки таблиц, порча после сбоев.
- Выбрать RocksDB для OLTP с преобладанием чтения и множеством диапазонных сканирований; паузы compaction и усиление чтения бьют по задержке.
- Игнорировать настройки compaction в LSM-хранилищах: запись останавливается (write stall), когда уровень 0 заполняется быстрее, чем compaction его разгружает.
- Встраивать SQLite в сервер с несколькими писателями: одновременно пишет только один; подходит для приложений, edge и тестов, но не для нагруженного общего бэкенда.
- Связанное: [[how-tables-and-indexes-are-stored]], [[storage-choices]], [[sql-vs-nosql-choosing]].
