---
title: Design a payment processing system
tags: [system-design, case-study, payments, idempotency, ledger]
learned: 2026-10-27
---
## TL;DR
A payment system moves money between accounts and external providers (card networks, bank rails, mobile wallets) and must never lose, duplicate or misstate a cent, while the network, the provider and the service itself fail in every way. The design is small on boxes and heavy on guarantees: idempotent APIs keyed by the client, a double-entry **ledger** as the source of truth, state machines per payment, the [[outbox-pattern]] for every event, provider calls protected by idempotency keys and timeouts, and **reconciliation** jobs that compare our ledger with provider statements and fix the drift. The EMV/authorisation side of card processing is in [[emv-card-processing]]; dedup theory in [[idempotency]].

## Key points

### Requirements and estimates
- Functional: create a payment (customer pays merchant), authorise then capture, refund, payout to merchants, balance queries, statements. Several providers (card acquirer, bank transfer, wallet).
- Non-functional: correctness over availability (reject when unsure, never double charge), full audit trail, PCI DSS scope minimised (no PAN in our systems; tokens only), 99.99% for authorisation, p99 < 1 s excluding provider time.
- Numbers (regional scale): 2 M payments/day ≈ 25/s, peak 300/s; ≈ 20 rows per payment → 40 M rows/day, a few GB. Provider latency 0.5–5 s. One Postgres primary with a synchronous standby handles this; the design is about guarantees, not throughput.

### API
```
POST /v1/payments        Idempotency-Key: <uuid>
  { amount: {value: "1500.00", currency: "KGS"}, source: {token}, merchantId, capture: AUTO|MANUAL }
  -> 201 { paymentId, status: AUTHORIZED|PENDING|DECLINED }       (same key → same response, 422 on payload mismatch)
POST /v1/payments/{id}/capture    Idempotency-Key
POST /v1/payments/{id}/refund     Idempotency-Key   { amount }
GET  /v1/payments/{id}
POST /v1/webhooks/{provider}       (provider → us: signed, idempotent by provider event id)
```
- Money as string decimals and ISO currency; never floats ([[numbers-bits-and-floating-point]], [[time-money-and-ids]]).

### Data model
- `payment(id, merchant_id, amount, currency, status, provider, provider_ref, created_at, version)` — status machine: `CREATED → AUTHORIZING → AUTHORIZED → CAPTURING → CAPTURED → (REFUNDED)`, plus `DECLINED`, `FAILED`, `UNKNOWN`.
- `idempotency_key(key, scope, request_hash, response, created_at)` — unique on `(scope, key)`.
- `ledger_entry(id, txn_id, account_id, debit, credit, currency, created_at)` — append-only; `ledger_txn(id, type, payment_id, posted_at)`; each txn's entries sum to zero. Balance = `SUM(credit − debit)` per account, cached in `account_balance` updated in the same transaction.
- `provider_attempt(id, payment_id, operation, idem_key, request, response, status, created_at)` — every call we made, for reconciliation.
- `outbox` for `PaymentAuthorized`, `PaymentCaptured` events.
- Postgres with `SERIALIZABLE` or careful row locks on `payment` and `account_balance` ([[postgres-isolation-levels]], [[postgres-locks]]).

### High-level design
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

### Deep dive: the three guarantees
1. **Idempotent create.** Insert the `idempotency_key` row first in the same transaction as the payment; a unique violation means "replay": return the stored response. Hash the request body; same key with a different body → `422` (a concurrent repeat while the first is still running → `409`). Keys live 24 h or more; in payments, keep them for the payment's life.
2. **Provider call with unknown outcome.** The call to the acquirer can time out after the charge happened. Rules: generate the provider idempotency key before the call (`paymentId:authorize`) and store the attempt row; set the payment to `AUTHORIZING`; on timeout do not retry blindly — query the provider by our reference, and only if it reports "not found" send again with the same key. If still unknown after N tries, mark `UNKNOWN` and let reconciliation decide. Never charge twice to resolve doubt ([[failure-modes-and-timeouts]], [[circuit-breaker]]).
3. **Ledger as truth.** State changes and money movement are one transaction: `CAPTURED` status, ledger txn (debit customer receivable, credit merchant payable, credit fee income), balance update, outbox event. Balances are derived, so a bug is fixable by replaying entries. Entries are never updated or deleted; corrections are new reversing entries ([[event-sourcing]] for the general idea, [[transactions-acid-and-isolation]]).

### Deep dive: reconciliation
- Daily settlement file from the provider plus hourly status polls for `AUTHORIZING`/`UNKNOWN` payments. Per record: match, missing on our side (we lost the response: post it now), or missing on their side (mark `FAILED`, release). Discrepancies that move money outward go to an operator queue, never auto-fixed. Reconciliation is what turns "at-least-once plus dedup" into something an auditor accepts.

### Trade-offs
| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Consistency | single primary, sync standby, serializable sections | multi-region active/active | money needs one truth; latency is paid gladly ([[multi-region-basics]]) |
| Ledger | double-entry, append-only | mutable balance column | auditability, derivable balances, reversals as entries |
| Orchestration | state machine + outbox; Temporal for multi-step payouts | choreographed saga | explicit states visible to support; retries and timers for free ([[temporal-concepts]], [[workflow-engines-vs-sagas]]) |
| Provider timeouts | query-then-retry with same key | immediate retry | unknown outcome is the common failure; retries double charge |
| Card data | tokenised, PAN never stored | store encrypted PAN | PCI scope and breach blast radius |

## Example
Idempotent create and the ledger posting in one transaction:

```sql
BEGIN;
INSERT INTO idempotency_key (scope, key, request_hash, created_at)
VALUES ('merchant-42', '7f3c…', 'sha256:…', now());
-- unique violation here → ROLLBACK, load stored response, return it

INSERT INTO payment (id, merchant_id, amount, currency, status, version)
VALUES ('p_01J…', 42, 1500.00, 'KGS', 'AUTHORIZING', 1);

INSERT INTO provider_attempt (id, payment_id, operation, idem_key, status)
VALUES ('a_01J…', 'p_01J…', 'AUTHORIZE', 'p_01J…:authorize', 'SENT');

INSERT INTO outbox (id, aggregate_id, type, payload)
VALUES (gen_random_uuid(), 'p_01J…', 'PaymentCreated', '{"paymentId":"p_01J…","amount":"1500.00"}');
COMMIT;

-- later, after the provider confirms capture: one ledger transaction, entries sum to zero
INSERT INTO ledger_txn (id, type, payment_id, posted_at) VALUES ('t_01J…', 'CAPTURE', 'p_01J…', now());
INSERT INTO ledger_entry (txn_id, account_id, debit, credit, currency) VALUES
  ('t_01J…', 'customer_receivable', 1500.00, 0,       'KGS'),
  ('t_01J…', 'merchant_payable:42', 0,       1470.00, 'KGS'),
  ('t_01J…', 'fee_income',          0,       30.00,   'KGS');
UPDATE payment SET status = 'CAPTURED', version = version + 1 WHERE id = 'p_01J…' AND version = 2;
```

Column conventions: `debit`/`credit` as `numeric(18,2)`, a `CHECK` that exactly one of them is non-zero, and a deferred trigger or application check that each `txn_id` sums to zero.

## Pitfalls
- Interviewers probe: "the acquirer timed out — do you retry?" Query first, same idempotency key, bounded attempts, `UNKNOWN` plus reconciliation. "How do you know you never double charged?" Unique idempotency rows, provider keys, reconciliation against statements.
- "Why a ledger and not a balance column?" Audit, reversals, derivable state; a balance column alone cannot explain how it got there.
- Floating-point money, timezone-naive timestamps, and `UPDATE balance = balance - x` without a lock are instant red flags.
- Webhooks from providers arrive out of order and more than once; process them by provider event id with [[idempotency]] and never move a state backwards.
- Related: [[emv-card-processing]] for authorisation and cryptograms, [[outbox-pattern]], [[saga]], [[api-security-checklist]] for webhook signatures.
