---
title: Чек-лист безопасности API
tags: [networking, security, api, webhooks, rate-limiting]
learned: 2026-10-27
---
## Кратко
Большинство взломов API — это не сбои криптографии, а пропущенная проверка владения, непроверенный ввод, секрет в строке лога или эндпоинт, на который никто не поставил rate limit. Это список, который нужно пройти перед тем, как открыть эндпоинт: транспорт, аутентификация, авторизация на уровне объекта, валидация ввода и лимиты размера, rate limit, защита от повтора (replay) для всего с побочными эффектами, подписанные webhook-и и журналы, в которых никогда нет учётных данных. Общий каталог уязвимостей — в [[owasp-top-10-for-backend]]; эта заметка — эксплуатационный чек-лист для Spring-сервиса и то, как каждый пункт выглядит в сети.

## Основное
### Транспорт и граница
- TLS везде, включая внутренность кластера, где это осуществимо ([[tls-handshake-and-certificates]], [[mtls-and-service-to-service-auth]]); `Strict-Transport-Security` на всём, чего касается браузер.
- Завершай соединение на шлюзе или обратном прокси, который удаляет hop-by-hop-заголовки и присланные клиентом `X-Forwarded-*`, прежде чем добавить свои ([[reverse-proxy-nginx-basics]]); иначе rate limit по IP и журналы аудита доверяют заголовку, который можно подделать.
- Привязывай эндпоинты управления (`/actuator/**`) к отдельному порту или сети; открывай только `health`/`prometheus`, остальное защищай.
- Заголовки безопасности для ответов браузеру: `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `Referrer-Policy` и корректный CORS ([[cookies-sessions-and-cors]]).

### Аутентификация и авторизация
- Каждый запрос аутентифицирован, кроме явного списка разрешённых ([[authentication-vs-authorization]]); по умолчанию запрещать (`anyRequest().authenticated()` последним).
- **Авторизация на уровне объекта** для каждого id в пути и теле (`/subscribers/{msisdn}`): сравнивать с идентичностью вызывающего, а не только с ролью. BOLA/IDOR — уязвимость API номер один.
- Scopes на каждого клиента, минимальные привилегии; отдельные токены для разных аудиторий (audiences).
- Отказ в закрытую сторону (fail closed): сбой IdP должен давать 401/503, но никогда «разрешить, потому что не смогли проверить».

### Обработка ввода
| Мера | Как в Spring | Зачем |
|---|---|---|
| Валидация схемы | `@Valid` + Bean Validation, `@JsonIgnoreProperties(ignoreUnknown=false)` для строгих API ([[spring-mvc-validation-and-uploads]]) | отсекает мусор рано, блокирует mass assignment |
| Лимиты размера | `server.max-http-request-header-size`, `spring.servlet.multipart.max-file-size`, `server.tomcat.max-swallow-size` | тело JSON на 2 ГБ — это DoS |
| Ограничение пагинации | `size` зажат до ≤ 200 | `?size=1000000` — это DoS против Postgres |
| Параметризованные запросы | плейсхолдеры JPA/JDBC, никогда конкатенация строк | SQL-инъекция |
| Безопасная десериализация | Jackson без default typing; никакой Java-сериализации в сети | RCE через цепочки гаджетов |
| Защита от SSRF | белый список хостов для любого URL от пользователя (цели webhook, загрузка картинок); блокировать частные диапазоны и `169.254.169.254` | кража метаданных облака |
| Кодирование вывода | только JSON, фиксированный `Content-Type`, никаких HTML-шаблонов из пользовательского ввода | XSS на редком HTML-пути |

### Rate limit и злоупотребления
- На клиента/токен, на IP (только за доверенным прокси), на класс эндпоинтов (для входа и OTP — значительно ниже). Возвращай `429` с `Retry-After` и заголовками `RateLimit-*`. Алгоритмы и реализация на Redis: [[rate-limiting]], [[design-rate-limiter]].
- Защита от перебора на входе/OTP: экспоненциальный backoff, осторожная блокировка аккаунта, CAPTCHA на границе.
- Переборки (bulkheads) и таймауты, чтобы один злоупотребляющий клиент не мог исчерпать потоки или пул БД ([[circuit-breaker]], [[sockets-and-connection-pooling]]).

### Защита от повтора и идемпотентность
- Для запросов клиентов, меняющих состояние: заголовок `Idempotency-Key`, хранящийся вместе с ответом 24 ч; повтор ключа с тем же payload → тот же ответ, с другим payload → `422` ([[idempotency]]).
- Для подписанных запросов (webhook-и, партнёрские API): временная метка внутри подписанного payload, отклонять, если старше 5 минут, плюс таблица дедупликации по nonce/`event_id`, чтобы перехваченный валидный запрос нельзя было повторить в пределах окна.
- Данные 0-RTT в TLS и повторы HTTP-запросов прокси тоже являются повтором; без ключа можно повторять только идемпотентные запросы ([[http-fundamentals]]).

### Webhook-и, которые ты отправляешь и получаешь
- **Получение**: проверять HMAC-подпись над сырым телом общим секретом (`X-Signature: t=1730000000,v1=hex`), сравнение за постоянное время, проверка временной метки, дедупликация по id события, быстро отвечать `2xx` и обрабатывать асинхронно (Kafka/outbox), никогда не доверять `event.id` для получения чего-либо без повторной проверки владения.
- **Отправка**: подписывать так же, включать `event_id` и `occurred_at`, повторять с backoff при не-2xx, дать получателю способ ротировать секрет (два активных секрета на время ротации) и позволить ему внести твои исходящие IP в белый список.

### Секреты и журналы
- Секреты — из vault или внедрённых переменных окружения, никогда в git, образах или `application.yml` ([[secrets-management]]). При любом подозрении — ротация.
- В журналах никогда нет `Authorization`, cookies, API-ключей, OTP, данных карт (PAN/CVV, [[emv-card-processing]]), полных тел запросов к эндпоинтам аутентификации. Вычищай на уровне логирования и в Sentry ([[error-tracking-with-sentry]]). Для аудита логируй `jti`/`sub`.
- Ответы с ошибками: общие сообщения клиентам, подробности — в журналах с correlation id; stack trace и SQL в теле 500 — это разведданные для атакующего.

## Пример
Проверка подписанного webhook от платёжного провайдера в Spring: подписано сырое тело (а не разобранный объект), сравнение за постоянное время, временная метка ограничивает повтор, обработка передаётся дальше.
```java
@RestController
@RequestMapping("/webhooks/payments")
public class PaymentWebhookController {
    private static final Duration MAX_AGE = Duration.ofMinutes(5);
    private final byte[] secret;                     // из vault, например Environment или SecretsManager
    private final WebhookEventRepository events;     // UNIQUE(event_id) для дедупликации
    private final KafkaTemplate<String, String> kafka;

    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Void> receive(@RequestHeader("X-Signature") String header,
                                        @RequestBody byte[] rawBody) throws Exception {
        var parts = Arrays.stream(header.split(","))
                .map(p -> p.split("=", 2)).collect(Collectors.toMap(p -> p[0], p -> p[1]));
        long ts = Long.parseLong(parts.get("t"));
        if (Math.abs(Instant.now().getEpochSecond() - ts) > MAX_AGE.toSeconds()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();          // окно повтора
        }
        var mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret, "HmacSHA256"));
        mac.update((ts + ".").getBytes(StandardCharsets.UTF_8));
        byte[] expected = mac.doFinal(rawBody);
        if (!MessageDigest.isEqual(expected, HexFormat.of().parseHex(parts.get("v1")))) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();          // сравнение за постоянное время
        }
        String eventId = JsonPath.read(new String(rawBody, StandardCharsets.UTF_8), "$.id");
        if (!events.insertIfAbsent(eventId)) {
            return ResponseEntity.ok().build();                                      // повторная доставка: подтвердить, ничего не делать
        }
        kafka.send("payments.webhooks", eventId, new String(rawBody, StandardCharsets.UTF_8));
        return ResponseEntity.accepted().build();                                    // обработать асинхронно
    }
}
```
Проверка из командной строки того, что отправляет провайдер (и что прокси сохранил сырое тело):
```bash
$ body='{"id":"evt_42","type":"payment.succeeded","amount":1500}'; ts=$(date +%s)
$ sig=$(printf '%s.%s' "$ts" "$body" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | cut -d' ' -f2)
$ curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api.beeline.kg/webhooks/payments \
    -H "X-Signature: t=$ts,v1=$sig" -H 'Content-Type: application/json' --data-binary "$body"
202
```

## Подводные камни
- Подпись пересериализованного JSON вместо сырых байтов: меняется порядок ключей или пробелы — и все подписи не сходятся; читай тело как `byte[]`.
- `String.equals` для сравнения подписи даёт утечку по времени; используй `MessageDigest.isEqual`.
- Rate limit по `X-Forwarded-For`, взятому от клиента; первый хоп должен его перезаписывать.
- Аннотации валидации на DTO есть, но у эндпоинта нет `@Valid`: ничего не выполняется.
- Доступ к `/actuator/env`, `/actuator/heapdump` или Swagger UI из интернета; и то и другое приводило к утечке боевых секретов в реальных инцидентах.
- Связанные заметки: [[owasp-top-10-for-backend]] — классы уязвимостей за каждым пунктом, [[rate-limiting]] — устройство самого ограничителя.
