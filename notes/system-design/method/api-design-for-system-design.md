---
title: API design for system design — REST vs gRPC vs async, pagination, versioning, idempotency
tags: [system-design, api, rest, grpc, interview]
learned: 2026-10-27
---
## TL;DR
In a design interview the API is the contract that fixes the data model and the failure semantics: which operations are synchronous and return a result, which only accept work and return `202`, how clients page through large lists, how writes survive retries, and how the contract evolves without breaking old clients. Choose the protocol by audience: REST/JSON for public and browser clients, gRPC for internal service-to-service calls with tight latency, and asynchronous messaging when the caller does not need the answer now. Every mutating endpoint should be idempotent, every list endpoint cursor-paginated, and every version change additive.

## Key points

### Style by audience
| | REST + JSON | gRPC + Protobuf | Async (Kafka/queue) |
|---|---|---|---|
| Typical use | public API, mobile, partners | internal services, low latency, streaming | work that can finish later, fan-out, integration |
| Contract | OpenAPI ([[openapi-swagger]]) | `.proto`, codegen | schema registry, Avro/Protobuf ([[message-schema-evolution]]) |
| Transport | HTTP/1.1 or 2, text | HTTP/2, binary, bidirectional streams | broker, at-least-once |
| Payload cost | ~2–5× larger than binary | compact, fast to parse | n/a |
| Browser support | native | needs gRPC-Web/proxy | n/a |
| Errors | HTTP status + problem body | status codes + details | retries, DLQ ([[dead-letter-queues-and-retries]]) |

- Details of REST conventions are in [[rest-and-http]], of gRPC in [[grpc-and-protobuf]], of the sync/async choice in [[service-communication-sync-vs-async]]. The design-interview question is only: *who calls it and does the caller wait?*
- Long-running work (provisioning an eSIM, sending 1 M notifications, generating a report): accept with `202 Accepted`, return an id and a status endpoint or a callback/webhook; execute behind a queue or a [[temporal-concepts|workflow]]. Never hold an HTTP connection for minutes.
- Real-time to clients: [[websockets-sse-and-long-polling]]; SSE for one-way server push, WebSocket for chat-like duplex ([[design-chat-service]]).

### Resource and endpoint design
- Nouns for resources, HTTP verbs for actions; sub-resources for ownership (`/subscribers/{id}/notifications`). Actions that are not CRUD get a verb resource: `POST /payments/{id}/refunds`, not `PUT /payments/{id}?action=refund`.
- Return the created resource's id and location; keep responses small, with `fields=` or separate detail endpoints for heavy data. Avoid chatty designs that force N calls per screen ([[bff-and-api-composition]]).
- Errors: a consistent problem body (`type`, `title`, `status`, `detail`, `traceId`), 4xx for the client's fault, 5xx for yours, `429` with `Retry-After` for [[rate-limiting]], `503` with `Retry-After` when shedding load ([[error-handling-strategies]]).
- Authentication at the edge ([[api-gateway-and-service-discovery]], [[authentication-vs-authorization]]), service-to-service via [[mtls-and-service-to-service-auth]].

### Pagination
| Strategy | How | Pros | Cons |
|---|---|---|---|
| Offset | `?page=3&size=50` → `OFFSET 100` | simple, random page access | `OFFSET n` scans n rows; items shift when rows are inserted; slow after ~10⁵ |
| Cursor (keyset) | `?after=<opaque cursor>&limit=50`, cursor = encoded `(created_at, id)` | O(log n) with an index, stable under inserts | no "jump to page 7", needs a total order |
| Time-window | `?from=…&to=…` | natural for logs/history | unbounded page size without a limit |

Use keyset pagination for anything that grows: `WHERE (created_at, id) < (:c_ts, :c_id) ORDER BY created_at DESC, id DESC LIMIT 50` with a composite index ([[postgres-indexes]]). Make the cursor opaque (base64 of the tuple) so clients cannot build them by hand and you can change the encoding.

### Idempotent endpoints
- `GET`, `PUT`, `DELETE` are idempotent by definition; `POST` is not, and clients retry on timeouts. A retried `POST /payments` without protection charges twice.
- Standard answer: an `Idempotency-Key` header (client-generated UUID), stored with the response for 24 h; a repeat with the same key returns the stored response, a repeat with the same key and a different body returns `422`. Store the key in the same transaction as the business write ([[idempotency]], [[design-payment-processing]]).
- For async consumers the equivalent is a `processed_events` table keyed by message id ([[outbox-pattern]]).

### Versioning and evolution
- Prefer **additive, backward-compatible changes**: new optional fields, new endpoints, new enum values clients are told to ignore. Unknown fields must be ignored by both sides (`@JsonIgnoreProperties(ignoreUnknown = true)`).
- When a breaking change is unavoidable: path version (`/v2/notifications`) for public APIs, header or media-type version internally; run v1 and v2 side by side with a sunset date and metrics on who still calls v1.
- Never reuse a field name with a different meaning or type; for Kafka the same rule is enforced by the schema registry's compatibility mode ([[message-schema-evolution]]).

### Rate limits and quotas in the contract
Document per-client limits, return `X-RateLimit-Remaining` style headers and `429`, and offer batch endpoints (`POST /notifications:batch`, up to 100 items) so well-behaved clients do fewer calls ([[rate-limiting]]).

## Example
The public surface of a notification service as an OpenAPI fragment; it encodes every decision above: accept-then-process, idempotency, keyset pagination, additive versioning.

```yaml
openapi: 3.1.0
info: { title: Notification API, version: "1.3.0" }     # minor bump = additive change only
paths:
  /v1/notifications:
    post:
      summary: Accept a notification for asynchronous delivery
      parameters:
        - in: header
          name: Idempotency-Key
          required: true
          schema: { type: string, format: uuid }
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [subscriberId, template, channels]
              properties:
                subscriberId: { type: string }
                template:     { type: string, example: "topup.success" }
                params:       { type: object, additionalProperties: true }
                channels:     { type: array, items: { type: string, enum: [PUSH, SMS, EMAIL] } }
                priority:     { type: string, enum: [HIGH, NORMAL, LOW], default: NORMAL }   # added in 1.2, optional
      responses:
        "202": { description: Accepted, content: { application/json: { schema: { $ref: "#/components/schemas/Accepted" } } } }
        "422": { description: Same Idempotency-Key with a different body }
        "429": { description: Rate limited, headers: { Retry-After: { schema: { type: integer } } } }
    get:
      summary: List notifications of a subscriber, newest first
      parameters:
        - { in: query, name: subscriberId, required: true, schema: { type: string } }
        - { in: query, name: after, schema: { type: string }, description: opaque cursor from previous page }
        - { in: query, name: limit, schema: { type: integer, maximum: 100, default: 20 } }
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  items:      { type: array, items: { $ref: "#/components/schemas/Notification" } }
                  nextCursor: { type: [string, "null"] }
  /v1/notifications/{id}:
    get: { summary: Delivery status per channel, responses: { "200": { description: OK } } }
components:
  schemas:
    Accepted:
      type: object
      properties: { id: { type: string }, status: { type: string, enum: [ACCEPTED] } }
```

The server side of the cursor is one indexed query, `ORDER BY created_at DESC, id DESC LIMIT :limit`, and the cursor is `base64(created_at + ":" + id)` of the last item.

## Pitfalls
- A synchronous `POST` that waits for the SMS provider: the API's p99 becomes the provider's p99 and an outage becomes your outage ([[failure-modes-and-timeouts]]).
- Offset pagination on a growing table: page 2 000 of the notification history runs a 100 000-row scan and shows duplicates after new inserts.
- Idempotency key checked outside the transaction: two concurrent retries both pass the check and both write; use a unique constraint and handle the violation.
- Breaking clients with "harmless" changes: renaming a field, making an optional field required, changing a number to a string, or removing an enum value.
- Leaking internal ids or database shapes into the public contract, which then makes [[sharding-strategies|sharding]] or a storage change a breaking API change.
- Related: [[rest-and-http]] and [[grpc-and-protobuf]] for protocol details; [[system-design-basics]] for where the API step sits in the interview flow.
