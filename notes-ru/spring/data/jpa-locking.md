---
title: Оптимистическая и пессимистическая блокировка в JPA
tags: [jpa, hibernate, locking, concurrency]
learned: 2026-10-22
---
## Кратко
Блокировки управляют конкурентным доступом к одним и тем же строкам, чтобы две транзакции молча не перезаписали друг друга (потерянное обновление, lost update). **Оптимистическая** блокировка не берёт блокировок в БД: при `UPDATE` сравнивается столбец `@Version`, а при несовпадении бросается `OptimisticLockException`, и вызывающий код повторяет операцию. **Пессимистическая** блокировка берёт блокировку строки уже при чтении (`SELECT ... FOR SHARE/UPDATE`), поэтому другие писатели ждут окончания транзакции. Оптимистическая — для редких конфликтов и пользовательского редактирования; пессимистическая — для горячих строк, счётчиков и обработки в стиле очереди.

## Основное

### Проблема, которую решают обе
- Транзакция A читает `balance = 100`, транзакция B читает `100`, A пишет `90`, B пишет `80`: обновление A потеряно. При READ COMMITTED (уровень по умолчанию в Postgres) это допустимо; см. [[transactions-acid-and-isolation]] и [[postgres-isolation-levels]].
- Блокировки — инструменты уровня JPA; альтернативы — уровень изоляции `SERIALIZABLE` (обнаруживает конфликт и прерывает транзакцию) или атомарный SQL (`update ... set balance = balance - 10 where ...`).

### Оптимистическая блокировка
- Добавь поле `@Version` (`int`, `long`, `Integer`, `Long`, `short` или `Timestamp`/`Instant`; число надёжнее временной метки). Им управляет Hibernate: никогда не задавай его вручную.
- Каждый `UPDATE` превращается в `update orders set ..., version = 2 where id = ? and version = 1`. Ноль затронутых строк → `jakarta.persistence.OptimisticLockException` (в Hibernate — `StaleObjectStateException`, Spring оборачивает его в `ObjectOptimisticLockingFailureException`) при flush/commit.
- Блокировки в БД не удерживаются, поэтому нет ни deadlock'ов, ни ожидания; проигравший повторяет попытку или сообщает о конфликте. Работает между HTTP-запросами: отправь `version` клиенту в DTO и прими обратно при обновлении — тогда отправка устаревшей формы упадёт, а не перезапишет данные.
- `LockModeType.OPTIMISTIC` (он же `READ`): при commit проверить также версию сущности, которую ты только *читал*, чтобы убедиться, что её никто не изменил. `OPTIMISTIC_FORCE_INCREMENT` (`WRITE`): увеличить версию даже без изменений, например чтобы версионировать родителя при изменении его дочерних элементов.
- Без `@Version` Hibernate всё равно может это делать через `@OptimisticLocking(type = ALL|DIRTY)` и `@DynamicUpdate`, сравнивая все (или изменённые) столбцы в `WHERE`; редко оправдано.

### Пессимистическая блокировка
- Исходит из того, что конфликты вероятны; строка блокируется при чтении и остаётся заблокированной до commit или отката транзакции, поэтому использовать её нужно внутри транзакции (см. [[spring-transactional]]).
| `LockModeType` | SQL в Postgres | Эффект |
|---|---|---|
| `PESSIMISTIC_READ` | `SELECT ... FOR SHARE` | другие могут читать и брать разделяемые блокировки; писатели ждут |
| `PESSIMISTIC_WRITE` | `SELECT ... FOR UPDATE` | другие по-прежнему могут *читать* (MVCC: обычные select никогда не блокируются), но `FOR SHARE`/`FOR UPDATE`/`UPDATE`/`DELETE` ждут |
| `PESSIMISTIC_FORCE_INCREMENT` | `FOR UPDATE` + увеличение версии | та же блокировка, и `@Version` увеличивается |
- Формулировка источника «запрещает чтение» — это мышление в стиле MySQL/SQL Server; в Postgres блокировка `FOR UPDATE` блокирует другие *блокирующие* чтения и записи, но не обычные `SELECT`.
- Где применять: `em.find(Order.class, id, LockModeType.PESSIMISTIC_WRITE)`, `em.lock(entity, mode)`, `query.setLockMode(...)` или `@Lock(LockModeType.PESSIMISTIC_WRITE)` на методе репозитория.
- Таймаут: `@QueryHints(@QueryHint(name = "jakarta.persistence.lock.timeout", value = "3000"))`; `0` означает `NOWAIT`, `-2` в Hibernate означает `SKIP LOCKED`. Postgres применяет только `NOWAIT`/`SKIP LOCKED`, другого таймаута блокировки на уровне отдельного запроса, кроме `SET lock_timeout`, у него нет.
- `FOR UPDATE SKIP LOCKED` — стандартный способ дать нескольким воркерам забирать задания из одной таблицы (ретранслятор [[outbox-pattern]], планировщики) без конкуренции.

### Как выбрать
| | Оптимистическая | Пессимистическая |
|---|---|---|
| Частота конфликтов | низкая | высокая |
| Цена на пути без конфликта | один дополнительный `where version = ?` | удерживается блокировка строки, соединение занято |
| Между запросами (форма редактирования) | да | нет |
| Риск deadlock | нет | есть, блокируй в одном и том же порядке |
| Сбой | исключение при commit, retry | ожидание, таймаут, ошибка deadlock |

## Пример
```java
public interface AccountRepository extends JpaRepository<Account, Long> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @QueryHints(@QueryHint(name = "jakarta.persistence.lock.timeout", value = "2000"))
    @Query("select a from Account a where a.id = :id")
    Optional<Account> findForUpdate(@Param("id") Long id);
}

@Service
@RequiredArgsConstructor
public class TransferService {
    private final AccountRepository accounts;

    @Transactional
    public void transfer(Long fromId, Long toId, BigDecimal amount) {
        // блокируем в фиксированном порядке, чтобы избежать deadlock между встречными переводами
        Long first = Math.min(fromId, toId), second = Math.max(fromId, toId);
        Account a = accounts.findForUpdate(first).orElseThrow();
        Account b = accounts.findForUpdate(second).orElseThrow();
        Account from = a.getId().equals(fromId) ? a : b;
        Account to   = from == a ? b : a;
        from.withdraw(amount);   // бросает исключение при нехватке средств
        to.deposit(amount);
    }                            // блокировки снимаются при commit

    @Retryable(retryFor = ObjectOptimisticLockingFailureException.class, maxAttempts = 3)
    @Transactional
    public void rename(Long id, String name) {   // у Account есть @Version
        accounts.findById(id).orElseThrow().setName(name);
    }
}
```

## Подводные камни
- `@Retryable` на том же методе, что и `@Transactional`, работает, только если прокси retry находится **снаружи** транзакционного прокси (у advice Spring Retry по умолчанию более высокий приоритет); иначе retry переиспользует «мёртвую» транзакцию. Надёжнее делать retry из вызывающего кода.
- Исключения оптимистической блокировки появляются при **commit**, уже после того, как метод нормально вернул управление; перехват внутри метода не работает.
- Пессимистические блокировки плюс внешний HTTP-вызов внутри транзакции = строки заблокированы на секунды; держи заблокированные участки короткими.
- Блокировка родителя не блокирует дочерние элементы; `FOR UPDATE` в запросе с `JOIN FETCH` блокирует все присоединённые строки, а это обычно больше, чем задумано.
- Отображение `@Version` с `@Column(updatable = false)` или копирование его из DTO через `BeanUtils` молча отключает проверку.
- Аналоги на уровне Java (compare-and-set, мониторы): [[concurrency-hazards]], [[locks-and-lock-free]]. Как сделать повторяемые записи безопасными: [[idempotency]].
