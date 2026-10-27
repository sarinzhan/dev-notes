---
title: API security checklist
tags: [networking, security, api, webhooks, rate-limiting]
learned: 2026-10-27
---
## TL;DR
Most API breaches are not cryptography failures; they are a missing ownership check, an unvalidated input, a secret in a log line, or an endpoint nobody rate-limited. This is the list to walk before exposing an endpoint: transport, authentication, authorization per object, input validation and size limits, rate limits, replay protection for anything with side effects, signed webhooks, and logs that never contain credentials. The generic vulnerability catalogue is in [[owasp-top-10-for-backend]]; this note is the operational checklist for a Spring service and what each item looks like on the wire.

## Key points
### Transport and edge
- TLS everywhere, including inside the cluster where feasible ([[tls-handshake-and-certificates]], [[mtls-and-service-to-service-auth]]); `Strict-Transport-Security` on anything a browser touches.
- Terminate at a gateway or reverse proxy that strips hop-by-hop and client-supplied `X-Forwarded-*` headers before adding its own ([[reverse-proxy-nginx-basics]]); otherwise IP-based rate limits and audit logs trust a spoofable header.
- Bind management endpoints (`/actuator/**`) to a separate port or network; expose only `health`/`prometheus` and protect the rest.
- Security headers for browser-facing responses: `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and correct CORS ([[cookies-sessions-and-cors]]).

### Authentication and authorization
- Every request authenticated except an explicit allow-list ([[authentication-vs-authorization]]); deny by default (`anyRequest().authenticated()` last).
- **Object-level authorization** on every path and body id (`/subscribers/{msisdn}`): compare against the caller's identity, not only the role. BOLA/IDOR is the number one API vulnerability.
- Scopes per client, least privilege; separate tokens for separate audiences.
- Fail closed: an IdP outage must produce 401/503, never "allow because we could not check".

### Input handling
| Control | How in Spring | Why |
|---|---|---|
| Schema validation | `@Valid` + Bean Validation, `@JsonIgnoreProperties(ignoreUnknown=false)` for strict APIs ([[spring-mvc-validation-and-uploads]]) | rejects garbage early, blocks mass assignment |
| Size limits | `server.max-http-request-header-size`, `spring.servlet.multipart.max-file-size`, `server.tomcat.max-swallow-size` | a 2 GB JSON body is a DoS |
| Pagination caps | `size` clamped to ≤ 200 | `?size=1000000` is a DoS against Postgres |
| Parameterised queries | JPA/JDBC placeholders, never string concatenation | SQL injection |
| Safe deserialisation | Jackson without default typing; no Java serialisation on the wire | RCE via gadget chains |
| SSRF guard | allow-list of hosts for any user-supplied URL (webhook targets, image fetch); block private ranges and `169.254.169.254` | cloud metadata theft |
| Output encoding | JSON only, `Content-Type` fixed, no HTML templates from user input | XSS in the rare HTML path |

### Rate limiting and abuse
- Per client/token, per IP (behind a trusted proxy only), per endpoint class (login and OTP endpoints much lower). Return `429` with `Retry-After` and `RateLimit-*` headers. Algorithms and the Redis implementation: [[rate-limiting]], [[design-rate-limiter]].
- Brute-force protection on login/OTP: exponential backoff, account lockout with care, CAPTCHA at the edge.
- Bulkheads and timeouts so one abusive client cannot exhaust threads or the DB pool ([[circuit-breaker]], [[sockets-and-connection-pooling]]).

### Replay protection and idempotency
- For state-changing requests from clients: `Idempotency-Key` header stored with the response for 24 h; duplicate key with same payload → same response, different payload → `422` ([[idempotency]]).
- For signed requests (webhooks, partner APIs): timestamp in the signed payload, reject if older than 5 minutes, plus a nonce/`event_id` dedup table so an intercepted valid request cannot be replayed within the window.
- 0-RTT TLS data and HTTP retries by proxies also replay; only idempotent requests may be retried without a key ([[http-fundamentals]]).

### Webhooks you send and receive
- **Receiving**: verify an HMAC signature over the raw body with a shared secret (`X-Signature: t=1730000000,v1=hex`), constant-time compare, check the timestamp, dedup by event id, respond `2xx` fast and process asynchronously (Kafka/outbox), never trust the `event.id` to fetch anything without re-checking ownership.
- **Sending**: sign the same way, include `event_id` and `occurred_at`, retry with backoff on non-2xx, give the receiver a way to rotate the secret (two active secrets during rotation), and let them allow-list your egress IPs.

### Secrets and logs
- Secrets from a vault or injected env, never in git, images or `application.yml` ([[secrets-management]]). Rotate on any suspicion.
- Logs never contain `Authorization`, cookies, API keys, OTPs, card data (PAN/CVV, [[emv-card-processing]]), full request bodies of auth endpoints. Scrub at the logging layer and in Sentry ([[error-tracking-with-sentry]]). Log the `jti`/`sub` for audit instead.
- Error responses: generic messages to clients, details in logs with a correlation id; stack traces and SQL in a 500 body are reconnaissance for an attacker.

## Example
Verifying a signed webhook from a payment provider in Spring: raw body (not the parsed object) is signed, the comparison is constant-time, the timestamp bounds replay, and processing is handed off.
```java
@RestController
@RequestMapping("/webhooks/payments")
public class PaymentWebhookController {
    private static final Duration MAX_AGE = Duration.ofMinutes(5);
    private final byte[] secret;                     // from vault, e.g. Environment or SecretsManager
    private final WebhookEventRepository events;     // UNIQUE(event_id) for dedup
    private final KafkaTemplate<String, String> kafka;

    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Void> receive(@RequestHeader("X-Signature") String header,
                                        @RequestBody byte[] rawBody) throws Exception {
        var parts = Arrays.stream(header.split(","))
                .map(p -> p.split("=", 2)).collect(Collectors.toMap(p -> p[0], p -> p[1]));
        long ts = Long.parseLong(parts.get("t"));
        if (Math.abs(Instant.now().getEpochSecond() - ts) > MAX_AGE.toSeconds()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();          // replay window
        }
        var mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret, "HmacSHA256"));
        mac.update((ts + ".").getBytes(StandardCharsets.UTF_8));
        byte[] expected = mac.doFinal(rawBody);
        if (!MessageDigest.isEqual(expected, HexFormat.of().parseHex(parts.get("v1")))) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();          // constant-time compare
        }
        String eventId = JsonPath.read(new String(rawBody, StandardCharsets.UTF_8), "$.id");
        if (!events.insertIfAbsent(eventId)) {
            return ResponseEntity.ok().build();                                      // duplicate delivery: ack, do nothing
        }
        kafka.send("payments.webhooks", eventId, new String(rawBody, StandardCharsets.UTF_8));
        return ResponseEntity.accepted().build();                                    // process asynchronously
    }
}
```
Checking from the command line what the provider sends (and that the proxy preserved the raw body):
```bash
$ body='{"id":"evt_42","type":"payment.succeeded","amount":1500}'; ts=$(date +%s)
$ sig=$(printf '%s.%s' "$ts" "$body" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | cut -d' ' -f2)
$ curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api.beeline.kg/webhooks/payments \
    -H "X-Signature: t=$ts,v1=$sig" -H 'Content-Type: application/json' --data-binary "$body"
202
```

## Pitfalls
- Signing the re-serialised JSON instead of the raw bytes: key order or whitespace changes and every signature fails; read the body as `byte[]`.
- `String.equals` for signature comparison leaks timing; use `MessageDigest.isEqual`.
- Rate-limiting by `X-Forwarded-For` taken from the client; the first hop must overwrite it.
- Validation annotations on the DTO but the endpoint missing `@Valid`: nothing runs.
- Letting `/actuator/env`, `/actuator/heapdump` or Swagger UI reach the internet; both have leaked production secrets in real incidents.
- Related: [[owasp-top-10-for-backend]] for the vulnerability classes behind each item, [[rate-limiting]] for the limiter design itself.
