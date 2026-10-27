---
title: Project story — EMV card processing and personalization at MDigital
tags: [project-story, interview, fintech, emv, outbox]
learned: 2026-10-27
---
## TL;DR
At MDigital (fintech) I worked on the backend for EMV chip card processing and card personalization: talking to cards and readers over `javax.smartcardio` with APDU commands, parsing TLV data, handling the ARQC/ARPC cryptogram exchange with the issuer host, and publishing card lifecycle events reliably with the transactional outbox pattern, all under PCI-DSS-style constraints. The story I tell is a card-blocking incident I traced through APDU logs to a wrong tag in the personalization script.

## Context and goal
- Product: card issuing and personalization for a bank client: a blank chip card gets its applications, keys and data written (personalization), then transactions from that card are authorised through our processing host.
- My part: the Java/Spring services that drive the reader/personalization device, parse card responses, and emit events (card personalised, card activated, card blocked) to downstream systems (CRM, notifications, ledger).
- Team: [~5 backend developers, 1 security engineer] <!-- fill in real number -->; I joined as a junior and stayed about [~1.5 years] <!-- fill in real number -->.
- Constraints: no PAN or track data in logs, keys only inside the HSM, tokenised card references everywhere outside the secure zone, auditable change history for personalization scripts.

## Architecture
- Spring Boot services on PostgreSQL; a Kafka topic per card lifecycle event, fed by an outbox relay ([[outbox-pattern]]).
- Reader/personalization adapter using `javax.smartcardio` (`TerminalFactory` → `CardTerminal` → `Card` → `CardChannel.transmit(CommandAPDU)`); the same adapter wrapped a USB NFC reader for testing.
- EMV layer: APDU builders (`SELECT AID`, `GET PROCESSING OPTIONS`, `READ RECORD`, `GENERATE AC`), a BER-TLV parser/encoder, and a cryptogram verifier that forwards ARQC to the HSM and returns ARPC.
- HSM for key derivation and cryptogram verification; services only see key identifiers.
- Tokenisation service: PAN → token on entry; everything else (DB, events, logs) uses the token.

```
Personalization station / POS ──APDU──▶ Reader adapter (smartcardio)
                                              │ TLV parse
                                              ▼
                                      EMV service (Spring) ──▶ HSM (ARQC → ARPC, keys)
                                              │ same tx: card state + outbox row
                                              ▼
                                      Postgres ──relay──▶ Kafka ──▶ CRM / notify / ledger
```

## My role and decisions
- Wrote the TLV parser and the APDU command set as a small typed model instead of byte arrays everywhere, with unit tests from EMV spec examples ([[junit-and-mockito]]).
- Proposed and implemented the transactional outbox after we lost "card activated" events during a Kafka outage: the card was active in our DB but CRM still showed "in production".
- Designed the logging policy: a structured logger that masks PAN (`4111 **** **** 1111`) and only serialises APDU data for a configurable tag allow-list; reviewed by the security engineer.
- Built the reader test harness: a `CardChannelPort` interface with a hardware implementation and a scripted fake that replays recorded APDU dialogs, so CI runs without a reader.

## Hardest problem and how I solved it
A batch of newly personalised cards started getting blocked at ATMs on the second or third transaction. No exception on our side; the issuer host declined and the chip's PIN try counter hit zero. I enabled masked APDU-level logging on the personalization station and a test POS and compared a good card and a bad card step by step. The difference was one TLV object in the personalization script: the Cardholder Verification Method list was written with a wrong length byte, so the terminal fell back to offline PIN with a PIN block the card did not have, failed verification, and decremented the counter. The script template had been edited by hand and the TLV encoder did not validate nested lengths.
Fix in three steps: hotfix the script and reissue the affected batch (CRM notified through the now-reliable events); add length validation and a round-trip test (encode → decode → equals) to the TLV codec; version personalization scripts in git with review instead of editing on the station. Since then every produced card keeps a reproducible APDU trace, which made later support questions much faster.

## What I would do differently
- Add the outbox from the start: a "card activated" event is a business fact, and losing it was a visible customer problem.
- Write the scripted fake reader before the first feature instead of after the first incident; hardware-only testing hid defects until production.
- Push for property-based tests of the TLV codec (random nested structures) rather than only spec examples.
- Keep an explicit state machine for card status (issued → personalised → active → blocked) with allowed transitions in code, not in scattered `if`s.

## Likely interview questions
- What is an APDU? A command/response unit between terminal and chip: CLA, INS, P1, P2, data, Le; the response has data plus a two-byte status word (`9000` = OK).
- What is TLV and why BER-TLV? Tag-length-value encoding used by EMV; tags can be multi-byte and nested, lengths can be multi-byte, so a naive parser breaks.
- What are ARQC and ARPC? The card signs transaction data with its key (ARQC); the issuer verifies and answers with ARPC so the card knows the response is authentic.
- Why the outbox pattern there? Dual write between Postgres and Kafka lost events during a broker outage; outbox makes the event part of the same transaction.
- How do you keep PAN out of logs? Tokenise on entry, mask in a logging layer, allow-list which TLV tags may be logged, review by security.
- How did you test against hardware? Port/adapter split with a replaying fake; hardware runs only in a nightly job on a station with a reader.
- How did you find the blocking root cause? Diffed masked APDU traces of good and bad cards and found a wrong TLV length in the personalization script.
- What did fintech teach you that you use in telecom? Idempotency, audit trails, reliable events, and that observability is cheaper than reproduction.

## Related notes
- [[outbox-pattern]], [[idempotency]], [[kafka-producer]]
- [[spring-transactional]], [[postgres-isolation-levels]]
- [[java-io-streams]], [[junit-and-mockito]], [[testing-types-and-dev-cycles]]
