---
title: Проектирование API в system design — REST, gRPC или async, пагинация, версионирование, идемпотентность
tags: [system-design, api, rest, grpc, interview]
learned: 2026-10-27
---
## Кратко
На собеседовании по проектированию API — это контракт, который фиксирует модель данных и семантику отказов: какие операции синхронные и возвращают результат, какие только принимают работу и возвращают `202`, как клиенты листают большие списки, как запись переживает повторы и как контракт развивается, не ломая старых клиентов. Протокол выбирают по аудитории: REST/JSON для публичных и браузерных клиентов, gRPC для внутренних вызовов между сервисами с жёсткими требованиями к задержке и асинхронный обмен сообщениями, когда вызывающему не нужен ответ прямо сейчас. Каждый изменяющий эндпоинт должен быть идемпотентным, каждый эндпоинт списка — с курсорной пагинацией, а каждое изменение версии — добавляющим.

## Основное

### Стиль по аудитории
| | REST + JSON | gRPC + Protobuf | Async (Kafka/очередь) |
|---|---|---|---|
| Типичное применение | публичный API, мобильные клиенты, партнёры | внутренние сервисы, низкая задержка, стриминг | работа, которая может завершиться позже, рассылка (fan-out), интеграция |
| Контракт | OpenAPI ([[openapi-swagger]]) | `.proto`, кодогенерация | schema registry, Avro/Protobuf ([[message-schema-evolution]]) |
| Транспорт | HTTP/1.1 или 2, текст | HTTP/2, бинарный, двунаправленные потоки | брокер, at-least-once |
| Размер полезной нагрузки | ~в 2–5× больше бинарного | компактный, быстро разбирается | н/п |
| Поддержка браузером | нативная | нужен gRPC-Web/прокси | н/п |
| Ошибки | HTTP-статус + тело problem | коды статуса + details | retry, DLQ ([[dead-letter-queues-and-retries]]) |

- Подробности соглашений REST — в [[rest-and-http]], gRPC — в [[grpc-and-protobuf]], выбора между sync и async — в [[service-communication-sync-vs-async]]. На собеседовании по проектированию вопрос только один: *кто вызывает и ждёт ли вызывающий?*
- Долгая работа (выпуск eSIM, отправка 1 M уведомлений, генерация отчёта): принять с `202 Accepted`, вернуть id и эндпоинт статуса или callback/webhook; выполнять за очередью или в [[temporal-concepts|workflow]]. Никогда не держать HTTP-соединение минутами.
- Реальное время для клиентов: [[websockets-sse-and-long-polling]]; SSE — для одностороннего push с сервера, WebSocket — для двустороннего обмена как в чате ([[design-chat-service]]).

### Проектирование ресурсов и эндпоинтов
- Существительные для ресурсов, HTTP-глаголы для действий; вложенные ресурсы — для принадлежности (`/subscribers/{id}/notifications`). Действия, которые не укладываются в CRUD, получают ресурс-глагол: `POST /payments/{id}/refunds`, а не `PUT /payments/{id}?action=refund`.
- Возвращать id и location созданного ресурса; ответы держать маленькими, а для тяжёлых данных — `fields=` или отдельные эндпоинты деталей. Избегать «болтливых» схем, которые требуют N вызовов на экран ([[bff-and-api-composition]]).
- Ошибки: единообразное тело problem (`type`, `title`, `status`, `detail`, `traceId`), 4xx — вина клиента, 5xx — твоя, `429` с `Retry-After` для [[rate-limiting]], `503` с `Retry-After` при сбросе нагрузки (load shedding) ([[error-handling-strategies]]).
- Аутентификация на периметре ([[api-gateway-and-service-discovery]], [[authentication-vs-authorization]]), между сервисами — через [[mtls-and-service-to-service-auth]].

### Пагинация
| Стратегия | Как | Плюсы | Минусы |
|---|---|---|---|
| Offset | `?page=3&size=50` → `OFFSET 100` | просто, доступ к произвольной странице | `OFFSET n` сканирует n строк; элементы сдвигаются при вставке строк; медленно после ~10⁵ |
| Курсор (keyset) | `?after=<opaque cursor>&limit=50`, курсор = закодированная пара `(created_at, id)` | O(log n) с индексом, стабильно при вставках | нельзя «перейти на страницу 7», нужен полный порядок |
| Временное окно | `?from=…&to=…` | естественно для логов и истории | неограниченный размер страницы без лимита |

Для всего, что растёт, используй keyset-пагинацию: `WHERE (created_at, id) < (:c_ts, :c_id) ORDER BY created_at DESC, id DESC LIMIT 50` с составным индексом ([[postgres-indexes]]). Курсор делай непрозрачным (base64 от кортежа), чтобы клиенты не собирали его вручную, а ты мог поменять кодировку.

### Идемпотентные эндпоинты
- `GET`, `PUT`, `DELETE` идемпотентны по определению; `POST` — нет, а клиенты повторяют запросы при таймаутах. Повторный `POST /payments` без защиты спишет деньги дважды.
- Стандартный ответ: заголовок `Idempotency-Key` (UUID, сгенерированный клиентом), который хранится вместе с ответом 24 ч; повтор с тем же ключом возвращает сохранённый ответ, повтор с тем же ключом и другим телом возвращает `422`. Ключ сохраняется в той же транзакции, что и бизнес-запись ([[idempotency]], [[design-payment-processing]]).
- Для асинхронных consumer-ов аналог — таблица `processed_events` с ключом по id сообщения ([[outbox-pattern]]).

### Версионирование и развитие
- Предпочитай **добавляющие, обратно совместимые изменения**: новые необязательные поля, новые эндпоинты, новые значения enum, которые клиенты должны игнорировать. Неизвестные поля обязаны игнорировать обе стороны (`@JsonIgnoreProperties(ignoreUnknown = true)`).
- Когда ломающее изменение неизбежно: версия в пути (`/v2/notifications`) для публичных API, версия в заголовке или media type внутри; v1 и v2 работают параллельно с датой отключения (sunset) и метриками, кто ещё вызывает v1.
- Никогда не переиспользовать имя поля с другим смыслом или типом; в Kafka то же правило обеспечивает режим совместимости schema registry ([[message-schema-evolution]]).

### Лимиты и квоты в контракте
Документируй лимиты на клиента, возвращай заголовки в стиле `X-RateLimit-Remaining` и `429`, предлагай пакетные эндпоинты (`POST /notifications:batch`, до 100 элементов), чтобы аккуратные клиенты делали меньше вызовов ([[rate-limiting]]).

## Пример
Публичная поверхность сервиса уведомлений в виде фрагмента OpenAPI; в ней закодированы все решения выше: принять, потом обработать, идемпотентность, keyset-пагинация, добавляющее версионирование.

```yaml
openapi: 3.1.0
info: { title: Notification API, version: "1.3.0" }     # повышение minor = только добавляющее изменение
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
                priority:     { type: string, enum: [HIGH, NORMAL, LOW], default: NORMAL }   # добавлено в 1.2, необязательное
      responses:
        "202": { description: Accepted, content: { application/json: { schema: { $ref: "#/components/schemas/Accepted" } } } }
        "409": { description: Same Idempotency-Key with a different body }
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

На стороне сервера курсор — это один индексированный запрос `ORDER BY created_at DESC, id DESC LIMIT :limit`, а сам курсор — `base64(created_at + ":" + id)` последнего элемента.

## Подводные камни
- Синхронный `POST`, который ждёт SMS-провайдера: p99 API становится p99 провайдера, а его сбой — твоим сбоем ([[failure-modes-and-timeouts]]).
- Offset-пагинация по растущей таблице: страница 2 000 истории уведомлений сканирует 100 000 строк и показывает дубликаты после новых вставок.
- Ключ идемпотентности проверяется вне транзакции: два одновременных повтора оба проходят проверку и оба пишут; используй уникальное ограничение и обрабатывай его нарушение.
- Поломка клиентов «безобидными» изменениями: переименование поля, превращение необязательного поля в обязательное, замена числа на строку или удаление значения enum.
- Утечка внутренних id или структуры базы данных в публичный контракт — после этого [[sharding-strategies|шардирование]] или смена хранилища становятся ломающим изменением API.
- Связанные заметки: [[rest-and-http]] и [[grpc-and-protobuf]] — детали протоколов; [[system-design-basics]] — где шаг API находится в ходе собеседования.
