---
title: Чтение планов EXPLAIN ANALYZE в Postgres
tags: [postgres, performance, query-planner, explain]
learned: 2026-10-27
---
## Кратко
`EXPLAIN` выводит план, выбранный планировщиком, с оценочными стоимостями; `EXPLAIN (ANALYZE, BUFFERS)` выполняет запрос и добавляет для каждого узла фактическое число строк, время и прочитанные страницы. Читать план нужно снизу вверх и изнутри наружу: найди узел, где **фактическое число строк расходится с оценкой**, узел с наибольшей долей фактического времени и любую сортировку или хеш, сброшенные на диск. Общая теория планировщика, стоимостей и статистики — в [[query-planning-and-execution]]; эта заметка — про вывод Postgres.

## Основное

### Опции
- `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)` — повседневная форма. `ANALYZE` выполняет оператор, поэтому запрос на запись оборачивай в `BEGIN; ... ROLLBACK;`.
- `BUFFERS` показывает `shared hit` (из `shared_buffers`), `read` (из кэша ОС или с диска), `dirtied`, `written` и `temp read/written` для сбросов на диск. Postgres 17 добавляет `SERIALIZE` для стоимости преобразования вывода и `MEMORY`.
- `VERBOSE` выводит выходные столбцы каждого узла (полезно, чтобы понять, почему не выбран index-only scan), `SETTINGS` показывает GUC-параметры планировщика, отличные от значений по умолчанию, `COSTS OFF` — для вставки диффов.
- `auto_explain` логирует в production планы операторов, которые медленнее `auto_explain.log_min_duration`; в сочетании с `pg_stat_statements` помогает понять, на что смотреть ([[postgres-performance-checklist]]).

### Анатомия одного узла
```
Index Scan using sim_msisdn_idx on sim  (cost=0.43..8.45 rows=1 width=120) (actual time=0.031..0.033 rows=1 loops=1)
  Index Cond: (msisdn = '996555123456'::text)
  Filter: (status = 'ACTIVE'::text)
  Rows Removed by Filter: 2
  Buffers: shared hit=4
```
- `cost=startup..total`: безразмерные оценки. `rows` — оценка на один цикл; `width` — байт на строку.
- `actual time=first..last` в мс на цикл, `rows` на цикл, `loops`: общая работа = значение на цикл × loops. Внутренняя сторона nested loop с `rows=1 loops=50000` — это 50 000 поисков по индексу.
- `Index Cond` — то, на что ответил индекс; `Filter` применяется потом к извлечённым строкам. Большой `Rows Removed by Filter` означает, что индекс плохо соответствует предикату: добавь столбец в индекс или создай частичный индекс ([[postgres-indexes]]).

### Узлы сканирования
| Узел | Значение | Хорош, когда |
|---|---|---|
| `Seq Scan` | читает каждую страницу heap | маленькие таблицы, предикаты, подходящие под > ~10 % строк |
| `Index Scan` | проходит по индексу, для каждой подходящей записи читает страницу heap, в порядке индекса | высокая селективность или `ORDER BY` совпадает с индексом |
| `Index Only Scan` | отвечает из индекса; `Heap Fetches: N` показывает промахи по карте видимости | покрывающий индекс и недавно очищенная vacuum таблица |
| `Bitmap Index Scan` + `Bitmap Heap Scan` | собирает id подходящих страниц в битовую карту, затем читает страницы в физическом порядке | средняя селективность, `OR` по нескольким индексам; `Recheck Cond` и `lossy` появляются, когда не хватает `work_mem` |
| `Parallel Seq Scan` под `Gather` | несколько воркеров | большие сканирования; проверь, что `Workers Launched` равно `Workers Planned` |

### Узлы join
- `Nested Loop`: для каждой внешней строки выполняется внутренний план. Хорошо, когда внешняя сторона мала, а внутренняя — index scan; катастрофа, когда планировщик ожидал 10 внешних строк, а получил 100 000.
- `Hash Join`: `Hash` строит таблицу из меньшего входа (`Batches: 1` означает, что она поместилась в `work_mem`; `Batches: 8` — что был сброс на диск). Затем по ней проверяется другой вход.
- `Merge Join`: оба входа отсортированы; ищи под ним `Sort` или индекс, дающий порядок.
- Порядок join читается изнутри наружу: самые глубокие узлы выполняются первыми.

### Другие узлы, которые стоит узнавать
- `Sort` с `Sort Method: quicksort Memory: 25kB` против `external merge Disk: 48000kB`: второму нужно больше `work_mem` или индекс, дающий порядок.
- `HashAggregate` / `GroupAggregate`, `Unique`, `Limit`, `Materialize` (кэширует внутренний результат для повторных итераций nested loop), `CTE Scan` (CTE с `MATERIALIZED` или используемые дважды не встраиваются), `SubPlan` (коррелированный подзапрос, выполняемый для каждой строки; часто стоит переписать как join), `InitPlan` (некоррелированный подзапрос, выполняемый один раз).
- `Incremental Sort` (PG 13+), когда префикс порядка уже отсортирован.

### Порядок чтения
1. Сравни общее `actual time` с реальным временем выполнения запроса, чтобы убедиться, что смотришь на нужный оператор.
2. Найди узел с наибольшим **собственным** временем (его время минус время дочерних узлов). Инструменты вроде визуализатора планов в pgAdmin или `explain.depesz` делают это вычитание; вручную начинай с самого глубокого узла.
3. В этом узле сравни оценку `rows` с фактом. Расхождение в 10 раз и больше: устаревшая статистика (`ANALYZE`), коррелированные столбцы (`CREATE STATISTICS`) или выражение без статистики.
4. Проверь `Buffers`: `read` ≫ `hit` означает холодный кэш или рабочий набор больше памяти; `temp written` означает сброс на диск.
5. Исправь первопричину (индекс, статистика, форма запроса), перезапусти, сравни.

## Пример
```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT o.id, s.msisdn
FROM esim_orders o
JOIN subscribers s ON s.id = o.subscriber_id
WHERE o.status = 'RESERVED' AND o.created_at > now() - interval '1 hour';

--  Nested Loop  (cost=0.86..412.10 rows=12 width=24) (actual time=0.05..318.40 rows=18450 loops=1)
--    Buffers: shared hit=74213
--    ->  Index Scan using esim_orders_created_idx on esim_orders o
--          (cost=0.43..90.20 rows=12 width=16) (actual time=0.03..41.20 rows=18450 loops=1)
--          Index Cond: (created_at > (now() - '01:00:00'::interval))
--          Filter: (status = 'RESERVED'::text)
--          Rows Removed by Filter: 61200
--    ->  Index Scan using subscribers_pkey on subscribers s
--          (cost=0.43..26.8 rows=1 width=16) (actual time=0.01..0.01 rows=1 loops=18450)
--  Planning Time: 0.4 ms
--  Execution Time: 320.1 ms
```
Две проблемы в одном плане: оценка `rows=12` против фактических 18 450 (во время инцидента статус и время коррелированы, а планировщик предположил 1 % RESERVED) и `Rows Removed by Filter: 61200`, потому что индекс не включает `status`. Частичный индекс `ON esim_orders (created_at) WHERE status = 'RESERVED'` убирает работу фильтра; после `ANALYZE` планировщик выбирает hash join по `subscribers` вместо 18 450 циклов.

## Подводные камни
- `EXPLAIN` без `ANALYZE` не может показать ошибку оценки; `ANALYZE` без `BUFFERS` не может показать ввод-вывод. Используй оба.
- Накладные расходы на замер времени: на машинах с медленным чтением часов `actual time` раздувает время маленьких узлов; `EXPLAIN (ANALYZE, TIMING OFF)` сохраняет число строк без замера времени по узлам.
- План подготовленного выражения с параметрами привязки может отличаться от плана с литералами (generic plan). Проверяй через `PREPARE`/`EXPLAIN EXECUTE` или `plan_cache_mode = force_generic_plan`, чтобы воспроизвести то, что видит JDBC-драйвер.
- План, снятый на ноутбуке разработчика с 1 000 строк, ничего не говорит о production; копируй статистику или используй набор данных размером с production.
- Основы индексов и планировщика: [[postgres-indexes]], [[query-planning-and-execution]]; блокировки, из-за которых быстрый план всё равно ждёт: [[postgres-locks]].
