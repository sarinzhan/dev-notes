---
title: Проектирование системы обработки платежей
tags: [system-design, case-study, payments, idempotency, ledger]
learned: 2026-10-27
---
## Кратко
Платёжная система перемещает деньги между счетами и внешними провайдерами (карточные сети, банковские рельсы, мобильные кошельки) и не должна терять, дублировать или искажать ни копейки, хотя сеть, провайдер и сам сервис отказывают всеми возможными способами. Архитектура скромная по числу блоков и тяжёлая по гарантиям: идемпотентные API с ключом от клиента, **реестр двойной записи (ledger)** как источник истины, конечные автоматы для каждого платежа, [[outbox-pattern]] для каждого события, вызовы провайдеров, защищённые ключами идемпотентности и таймаутами, и задачи **сверки (reconciliation)**, которые сравнивают наш реестр с выписками провайдеров и исправляют расхождения. EMV и авторизация в карточном процессинге — в [[emv-card-processing]]; теория дедупликации — в [[idempotency]].

## Основное

### Требования и оценки
- Функциональные: создать платёж (клиент платит мерчанту), авторизация, затем списание (capture), возврат, выплата мерчантам, запросы баланса, выписки. Несколько провайдеров (эквайер, банковский перевод, кошелёк).
- Нефункциональные: корректность важнее доступности (отклонить, если не уверены, но никогда не списать дважды), полный аудиторский след, минимальная область PCI DSS (никакого PAN в наших системах, только токены), 99.99% для авторизации, p99 < 1 с без учёта времени провайдера.
- Числа (региональный масштаб): 2 M платежей в день ≈ 25/с, пик 300/с; ≈ 20 строк на платёж → 40 M строк в день, несколько GB. Задержка провайдера 0.5–5 с. С этим справляется один primary в Postgres с синхронным standby; архитектура про гарантии, а не про пропускную способность.

### API
```
POST /v1/payments        Idempotency-Key: <uuid>
  { amount: {value: "1500.00", currency: "KGS"}, source: {token}, merchantId, capture: AUTO|MANUAL }
  -> 201 { paymentId, status: AUTHORIZED|PENDING|DECLINED }       (same key → same response, 409 on payload mismatch)
POST /v1/payments/{id}/capture    Idempotency-Key
POST /v1/payments/{id}/refund     Idempotency-Key   { amount }
GET  /v1/payments/{id}
POST /v1/webhooks/{provider}       (provider → us: signed, idempotent by provider event id)
```
- Деньги — строковые десятичные числа и код валюты ISO; никогда не float ([[numbers-bits-and-floating-point]], [[time-money-and-ids]]).

### Модель данных
- `payment(id, merchant_id, amount, currency, status, provider, provider_ref, created_at, version)` — конечный автомат статусов: `CREATED → AUTHORIZING → AUTHORIZED → CAPTURING → CAPTURED → (REFUNDED)`, плюс `DECLINED`, `FAILED`, `UNKNOWN`.
- `idempotency_key(key, scope, request_hash, response, created_at)` — уникальность по `(scope, key)`.
- `ledger_entry(id, txn_id, account_id, debit, credit, currency, created_at)` — только добавление (append-only); `ledger_txn(id, type, payment_id, posted_at)`; сумма проводок каждой транзакции равна нулю. Баланс = `SUM(credit − debit)` по счёту, кешируется в `account_balance`, который обновляется в той же транзакции.
- `provider_attempt(id, payment_id, operation, idem_key, request, response, status, created_at)` — каждый сделанный нами вызов, для сверки.
- `outbox` для событий `PaymentAuthorized`, `PaymentCaptured`.
- Postgres с `SERIALIZABLE` или аккуратными блокировками строк `payment` и `account_balance` ([[postgres-isolation-levels]], [[postgres-locks]]).

### Общая архитектура
```
merchant / app ──▶ API gateway (auth, rate limit) ──▶ payment service (stateless)
                                                           │ tx: idempotency row + payment + ledger + outbox
                                                           ▼
                                                       Postgres (primary + sync standby)
                                                           │ relay
                                                           ▼
                                                   Kafka payment.events ──▶ notifications, analytics, merchant webhooks
payment service ──provider adapter (timeout, idem key, breaker)──▶ acquirer / bank / wallet
provider ──signed webhook──▶ API ──▶ status updater (idempotent by provider event id)
reconciliation job (nightly + hourly) ◀── provider statements / status API
```

### Подробно: три гарантии
1. **Идемпотентное создание.** Сначала вставляется строка `idempotency_key` в той же транзакции, что и платёж; нарушение уникальности означает «повтор»: возвращаем сохранённый ответ. Тело запроса хешируется; тот же ключ с другим телом → `409`. Ключи живут 24 ч или дольше; в платежах их хранят весь срок жизни платежа.
2. **Вызов провайдера с неизвестным исходом.** Вызов эквайера может завершиться таймаутом уже после списания. Правила: сгенерировать ключ идемпотентности для провайдера до вызова (`paymentId:authorize`) и сохранить строку попытки; перевести платёж в `AUTHORIZING`; при таймауте не повторять вслепую — запросить провайдера по нашей ссылке и отправить снова с тем же ключом, только если он ответил «не найдено». Если после N попыток исход всё ещё неизвестен, пометить `UNKNOWN` и оставить решение сверке. Никогда не списывать повторно, чтобы разрешить сомнение ([[failure-modes-and-timeouts]], [[circuit-breaker]]).
3. **Реестр как истина.** Изменение состояния и движение денег — одна транзакция: статус `CAPTURED`, транзакция реестра (дебет дебиторки клиента, кредит кредиторки мерчанту, кредит комиссионного дохода), обновление баланса, событие в outbox. Балансы производные, поэтому ошибку можно исправить, заново проиграв проводки. Проводки никогда не обновляются и не удаляются; исправления — это новые сторнирующие проводки ([[event-sourcing]] — общая идея, [[transactions-acid-and-isolation]]).

### Подробно: сверка
- Ежедневный файл расчётов от провайдера плюс ежечасный опрос статусов для платежей в `AUTHORIZING`/`UNKNOWN`. По каждой записи: совпадение, отсутствует у нас (мы потеряли ответ: проводим сейчас) или отсутствует у них (помечаем `FAILED`, освобождаем). Расхождения, которые уводят деньги наружу, попадают в очередь оператора и никогда не исправляются автоматически. Именно сверка превращает «at-least-once плюс дедупликация» в то, что примет аудитор.

### Компромиссы
| Решение | Выбрано | Альтернатива | Почему |
|---|---|---|---|
| Согласованность | один primary, синхронный standby, сериализуемые участки | multi-region active/active | деньгам нужна одна истина; за задержку платим охотно ([[multi-region-basics]]) |
| Реестр | двойная запись, append-only | изменяемая колонка баланса | проверяемость, вычислимые балансы, сторно как проводки |
| Оркестрация | конечный автомат + outbox; Temporal для многошаговых выплат | хореографическая сага | явные состояния видны поддержке; retry и таймеры бесплатно ([[temporal-concepts]], [[workflow-engines-vs-sagas]]) |
| Таймауты провайдера | сначала запрос, потом повтор с тем же ключом | немедленный повтор | неизвестный исход — самый частый сбой; повторы приводят к двойному списанию |
| Данные карт | токенизация, PAN никогда не хранится | хранить зашифрованный PAN | область PCI и радиус поражения при утечке |

## Пример
Идемпотентное создание и проводка в реестре в одной транзакции:

```sql
BEGIN;
INSERT INTO idempotency_key (scope, key, request_hash, created_at)
VALUES ('merchant-42', '7f3c…', 'sha256:…', now());
-- здесь нарушение уникальности → ROLLBACK, загрузить сохранённый ответ и вернуть его

INSERT INTO payment (id, merchant_id, amount, currency, status, version)
VALUES ('p_01J…', 42, 1500.00, 'KGS', 'AUTHORIZING', 1);

INSERT INTO provider_attempt (id, payment_id, operation, idem_key, status)
VALUES ('a_01J…', 'p_01J…', 'AUTHORIZE', 'p_01J…:authorize', 'SENT');

INSERT INTO outbox (id, aggregate_id, type, payload)
VALUES (gen_random_uuid(), 'p_01J…', 'PaymentCreated', '{"paymentId":"p_01J…","amount":"1500.00"}');
COMMIT;

-- позже, когда провайдер подтвердил списание: одна транзакция реестра, сумма проводок равна нулю
INSERT INTO ledger_txn (id, type, payment_id, posted_at) VALUES ('t_01J…', 'CAPTURE', 'p_01J…', now());
INSERT INTO ledger_entry (txn_id, account_id, debit, credit, currency) VALUES
  ('t_01J…', 'customer_receivable', 0,       1500.00, 'KGS'),
  ('t_01J…', 'merchant_payable:42', 1470.00, 0,       'KGS'),
  ('t_01J…', 'fee_income',          30.00,   0,       'KGS');
UPDATE payment SET status = 'CAPTURED', version = version + 1 WHERE id = 'p_01J…' AND version = 2;
```

Соглашения по колонкам: `debit`/`credit` как `numeric(18,2)`, `CHECK`, что ненулевая ровно одна из них, и отложенный триггер или проверка в приложении, что сумма по каждому `txn_id` равна нулю.

## Подводные камни
- На собеседовании спрашивают: «эквайер ответил таймаутом — повторяешь?» Сначала запрос, тот же ключ идемпотентности, ограниченное число попыток, `UNKNOWN` плюс сверка. «Откуда известно, что двойных списаний не было?» Уникальные строки идемпотентности, ключи провайдера, сверка с выписками.
- «Почему реестр, а не колонка баланса?» Аудит, сторно, вычислимое состояние; одна колонка баланса не может объяснить, как он таким стал.
- Деньги во float, метки времени без часового пояса и `UPDATE balance = balance - x` без блокировки — мгновенные красные флаги.
- Webhook-и от провайдеров приходят не по порядку и больше одного раза; их обрабатывают по id события провайдера с [[idempotency]] и никогда не двигают состояние назад.
- Связанные заметки: [[emv-card-processing]] — авторизация и криптограммы, [[outbox-pattern]], [[saga]], [[api-security-checklist]] — подписи webhook-ов.
