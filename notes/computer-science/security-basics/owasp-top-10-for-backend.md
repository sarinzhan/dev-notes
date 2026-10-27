---
title: OWASP Top 10 for backend developers
tags: [security, owasp, injection, ssrf, spring-security]
learned: 2026-10-27
---
## TL;DR
The OWASP Top 10 is the standard list of web application risk categories. For a Java/Spring backend the ones that actually produce incidents are: **injection** (SQL, OS command, LDAP, JPQL), **broken access control** (IDOR, missing authorisation on a method), **broken authentication** (weak sessions, tokens that never expire), **SSRF** (your server fetching an attacker-chosen URL), **insecure deserialization** (Java serialization, polymorphic JSON), **security misconfiguration** (actuator open, debug endpoints, default credentials) and **secrets or PII leaking into logs**. Almost all of them have the same fix shape: never trust input, bind parameters instead of concatenating, authorise every object access, and keep the framework's defaults on. Auth flows are in [[authentication-vs-authorization]] and [[spring-security-basics]]; endpoint hardening in [[api-security-checklist]].

## Key points
### The categories and what they look like in a Spring service
| OWASP category (2021 list) | Backend symptom | Primary defence |
|---|---|---|
| A01 Broken access control | `GET /orders/{id}` returns any user's order (IDOR); admin endpoint only hidden in the UI | authorise per object: `WHERE id = ? AND owner = ?`, `@PreAuthorize`, deny by default |
| A02 Cryptographic failures | plaintext PII, MD5 passwords, HTTP between services | [[hashing-vs-encryption]], [[symmetric-and-asymmetric-crypto]], TLS everywhere |
| A03 Injection | string-built SQL/JPQL, `Runtime.exec` with user input, log injection | bound parameters, allow-lists, no shell |
| A04 Insecure design | no rate limit on OTP, password reset by phone number only | threat-model the flow, limits, step-up auth |
| A05 Security misconfiguration | `/actuator/env` and `/actuator/heapdump` public, stack traces in responses, CORS `*` with credentials | lock down actuator, generic error bodies, explicit CORS ([[cookies-sessions-and-cors]]) |
| A06 Vulnerable components | Log4Shell, old Jackson, Spring4Shell | dependency scanning (OWASP Dependency-Check, Trivy), fast upgrade path |
| A07 Identification and auth failures | JWT with `alg: none`, no expiry, session fixation, no brute-force protection | [[jwt-authentication]], short-lived tokens, lockout/backoff |
| A08 Software and data integrity | `ObjectInputStream` on untrusted bytes, Jackson default typing, unsigned CI artifacts | avoid Java serialization, explicit polymorphic types, signed builds |
| A09 Logging and monitoring failures | no audit of auth events, PII and tokens in logs, nobody alerted | structured audit log, scrubbing, alerts ([[error-tracking-with-sentry]]) |
| A10 SSRF | "import from URL" or webhook URL field fetches `http://169.254.169.254/` or an internal service | allow-list hosts, resolve and block private ranges, egress proxy |

### Injection
- SQL: `"... WHERE msisdn = '" + msisdn + "'"` lets `' OR 1=1 --` through. `PreparedStatement`, `JdbcTemplate` with `?`, JPQL named parameters and Spring Data derived queries bind values so they are never parsed as SQL. Dynamic *identifiers* (sort column, table name) cannot be bound; map them through an allow-list.
- JPQL/HQL is injectable the same way; `@Query("... :status")` is safe, `em.createQuery("... '" + s + "'")` is not.
- OS commands: `ProcessBuilder(List.of("ffmpeg", "-i", file))` with an argument list, never `bash -c "..." + input`. Better: no shell at all.
- Log injection: a `\n` in a user field forges a fake log line; structured JSON logging ([[logging-stacks]]) prevents it. Log4Shell was injection through the logging pipeline. Also: the app's Postgres role must not be a superuser (`COPY ... TO PROGRAM` exists).

### Access control and authentication
- Authentication (who you are) is not authorisation (what you may do). A valid token on a request says nothing about whether *this* user may read *this* order. Enforce ownership in the query or in a domain check, and test it: a test with user B requesting user A's resource.
- Method-level security `@PreAuthorize("hasRole('OPS') or #orderId == principal.orderId")` and a deny-all default `.anyRequest().authenticated()`; put internal endpoints behind a separate port or network policy, not behind obscurity.
- Tokens: expire, bind to an audience, rotate refresh tokens, verify the signature with a pinned algorithm ([[jwt-authentication]]). Sessions: regenerate the id on login, `HttpOnly; Secure; SameSite`.
- Mass assignment: binding a request body straight to an entity lets a client set `role` or `balance`. Use a request DTO with only the allowed fields ([[spring-mvc-validation-and-uploads]]).

### SSRF and deserialization
- SSRF: any place the server makes a request to a user-supplied URL (webhook registration, avatar import, "test connection"). Cloud metadata endpoints and internal admin APIs are the usual targets. Defences: allow-list of hosts/schemes, resolve DNS and reject private/link-local ranges *at connection time* (DNS rebinding), disable redirects or re-check after each, route through an egress proxy with its own allow-list, use a client with timeouts ([[rest-clients]]).
- Java serialization (`ObjectInputStream`) on untrusted bytes is remote code execution via gadget chains; `ObjectInputFilter` (JEP 290) limits classes, but the answer is "do not" (JSON, protobuf). Jackson: never enable default typing globally; `@JsonTypeInfo` with an explicit, closed set of subtypes. XML: disable DTDs and external entities (XXE) on every parser.

### Logging, errors and misconfiguration
- Never log `Authorization` headers, passwords, OTP codes, full PAN, session cookies; mask in a Logback converter and in Sentry `beforeSend` so nothing reaches [[sebep-incident-analysis]] unscrubbed.
- Error responses: a generic message and a correlation id to the client; the stack trace stays in the log (`server.error.include-stacktrace=never` in production).
- Actuator: expose only `health,prometheus`, on a management port inside the cluster network; `heapdump` contains every secret in memory.
- Supply chain: pin versions, scan images, keep a one-day upgrade path for CVEs; Renovate and dependency scanning in CI make this routine ([[maven]]).

## Example
The same lookup written vulnerably and safely, plus an SSRF-safe URL check.
```java
@Repository
public class SubscriberRepository {
    private final JdbcTemplate jdbc;
    private static final Set<String> SORTABLE = Set.of("created_at", "msisdn", "status");

    SubscriberRepository(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    // VULNERABLE: msisdn = "' OR '1'='1" dumps the table; sort = "1; DROP TABLE ..." runs it.
    public List<Subscriber> findBad(String msisdn, String sort) {
        return jdbc.query("SELECT * FROM subscriber WHERE msisdn = '" + msisdn + "' ORDER BY " + sort,
                SubscriberRepository::map);
    }

    // SAFE: value is bound; identifier goes through an allow-list; ownership is in the query.
    public Optional<Subscriber> find(String msisdn, String sort, long accountId) {
        if (!SORTABLE.contains(sort)) throw new IllegalArgumentException("sort");
        var rows = jdbc.query(
                "SELECT * FROM subscriber WHERE msisdn = ? AND account_id = ? ORDER BY " + sort,
                SubscriberRepository::map, msisdn, accountId);
        return rows.stream().findFirst();
    }

    private static Subscriber map(ResultSet rs, int i) throws SQLException {
        return new Subscriber(rs.getLong("id"), rs.getString("msisdn"), rs.getString("status"));
    }
}

final class WebhookUrlValidator {
    static URI validate(String raw) throws Exception {
        URI uri = URI.create(raw);
        if (!"https".equals(uri.getScheme()) || uri.getPort() != -1) throw new IllegalArgumentException("scheme/port");
        for (InetAddress a : InetAddress.getAllByName(uri.getHost())) {
            if (a.isLoopbackAddress() || a.isSiteLocalAddress() || a.isLinkLocalAddress() || a.isAnyLocalAddress())
                throw new IllegalArgumentException("private address");
        }
        return uri; // and the HTTP client must not follow redirects, or must re-validate each hop
    }
}
```

## Pitfalls
- "We use an ORM so we are safe": JPQL concatenation, native queries and `ORDER BY` built from request params are still injectable.
- Checking authorisation in the controller and forgetting the Kafka consumer, the scheduled job and the GraphQL resolver that reach the same service method; put the check in the service or the query.
- A `@WithMockUser` test suite that only tests the happy path; add negative tests (wrong user, expired token, missing role) and treat them as regression tests.
- Masking PII in the app log but shipping full request bodies to Sentry, the tracing backend or a debug Kafka topic.
- Blocking SSRF by string-matching the host (`!host.startsWith("10.")`): bypassed by DNS rebinding, IPv6, decimal IPs and redirects. Validate the resolved address and the final hop.
- Related: [[api-security-checklist]] for rate limiting, replay protection and input validation at the edge; [[secrets-management]] for keeping keys out of code and logs.
