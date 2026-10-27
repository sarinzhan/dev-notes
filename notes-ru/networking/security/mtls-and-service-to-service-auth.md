---
title: mTLS и аутентификация между сервисами
tags: [networking, mtls, service-auth, spiffe, zero-trust]
learned: 2026-10-27
---
## Кратко
Взаимный TLS (mutual TLS, mTLS) добавляет в рукопожатие клиентский сертификат, поэтому обе стороны доказывают свою идентичность ещё до отправки любого запроса; идентичность сервиса становится «тот, у кого есть ключ к этому сертификату», а не «тот, кто знает этот API-ключ». Это самый надёжный и самый дорогой в эксплуатации вариант: сертификаты нужно выпускать, ротировать и отзывать для каждой рабочей нагрузки (workload), поэтому на практике mTLS почти всегда делает sidecar или платформа (Istio/Linkerd с идентичностями SPIFFE, облачные балансировщики), а не код на Spring. Для нескольких сервисов подписанный токен или API-ключ поверх обычного TLS — всё ещё обоснованный выбор, если у него ограниченная область действия, он ротируется и никогда не попадает в журналы.

## Основное
### Чем mTLS отличается от TLS
- Обычный TLS ([[tls-handshake-and-certificates]]): сервер предъявляет сертификат, клиент его проверяет; на транспортном уровне клиент анонимен.
- mTLS: сервер отправляет в рукопожатии `CertificateRequest`; клиент отвечает своим `Certificate` и подписью `CertificateVerify`. Сервер проверяет цепочку по своему **хранилищу доверенных клиентских сертификатов** (client trust store) и затем может считать subject/SAN идентичностью вызывающего.
- Сценарий отказа: сервер требует клиентский сертификат, у клиента его нет → alert `handshake_failure` или `bad_certificate` ещё до HTTP. По HTTP-журналам это трудно отлаживать, потому что их нет.
- Идентичность находится в SAN сертификата, обычно в виде URI, например `spiffe://cluster.local/ns/esim/sa/provisioning-api`; приложение отображает её в роли.

### Сравнение вариантов
| Метод | Доказывает | Ротация | Где ломается | Подходит для |
|---|---|---|---|---|
| mTLS | владение закрытым ключом, на каждую рабочую нагрузку | автоматизирована mesh/cert-manager (от часов до дней) | корпоративная инспекция TLS, L7-прокси, завершающие TLS, клиентские библиотеки без поддержки клиентских сертификатов | внутренние сети zero-trust, партнёрские API по договору, регулируемый трафик |
| Подписанный JWT (client credentials, OAuth2) | обладание токеном, выданным IdP ([[authentication-vs-authorization]]) | минуты (TTL токена), ключи у IdP | сбой IdP блокирует все вызовы; повтор токена в пределах TTL | сервис-сервис через шлюзы, смешанная инфраструктура |
| API-ключ / общий секрет | знание строки | вручную, болезненно | утечки в журналах, репозиториях, заголовках; нет детализации идентичности | интеграции с низким риском, webhook-и (как HMAC, см. [[api-security-checklist]]) |
| Только сеть (VPC, security group) | нахождение в сети | неприменимо | боковое перемещение (lateral movement) после одной компрометации | эшелонированная защита, никогда в одиночку |
| mTLS в mesh (Istio/Linkerd) | идентичность SPIFFE, обеспечивается в sidecar/ambient-прокси | полностью автоматизирована | отладка через sidecar, задержка лишнего хопа | парки Kubernetes, где код приложения не должен знать о сертификатах |

### Идея SPIFFE / SPIRE
- SPIFFE стандартизирует идентичность рабочей нагрузки: URI SAN `spiffe://trust-domain/path` внутри короткоживущего X.509 (SVID), выпущенного локальным агентом, который удостоверяет рабочую нагрузку (какой под, какой service account). Ни один секрет никогда не копируется вручную; агент доставляет и ротирует сертификат через Unix-сокет.
- Сервис-меши реализуют это внутри; ты пишешь политику авторизации («`payments` может вызывать `ledger` на `POST /entries`»), а прокси обеспечивают её на каждом соединении и запросе.

### Как это сделать в Spring
- Сторона сервера: `server.ssl.client-auth=need` (или `want` для необязательного), `server.ssl.trust-store` с CA клиентов; вход через `x509()` в Spring Security извлекает subject и отображает его в `UserDetails`/authorities ([[spring-security-basics]]).
- Сторона клиента: хранилище ключей с клиентским сертификатом и ключом, передаваемое как SSL bundle Spring Boot 3.1+ в `RestClient`, `WebClient`, Kafka (`ssl.keystore.*`), Postgres (`sslcert`/`sslkey` или `sslmode=verify-full` только для сервера).
- Bundles поддерживают `reload-on-update`, поэтому cert-manager может ротировать файлы, а JVM подхватит их без перезапуска.

### Где завершать
- На обратном прокси или шлюзе ([[reverse-proxy-nginx-basics]], [[api-gateway-and-service-discovery]]): nginx с `ssl_verify_client on` проверяет, затем передаёт subject в заголовке (`X-SSL-Client-S-DN`); приложение должно доверять этому заголовку только от прокси.
- В приложении: нужно, когда прокси не доверенный или когда балансировщик, завершающий TLS, не умеет аутентифицировать клиентов (многие облачные L7-балансировщики не умеют; L4 passthrough умеет, см. [[load-balancers-l4-vs-l7]]).

## Пример
Сервис на Spring Boot 3, который и требует клиентские сертификаты от вызывающих, и предъявляет свой при обращении к eSIM-вендору; всё настроено через SSL bundles. SAN сертификата идентифицирует вызывающего, Spring Security отображает его в роль.
```yaml
spring:
  ssl:
    bundle:
      pem:
        server:                       # наша идентичность + каким CA клиентов доверяем
          reload-on-update: true
          keystore:
            certificate: "file:/etc/tls/tls.crt"
            private-key: "file:/etc/tls/tls.key"
          truststore:
            certificate: "file:/etc/tls/client-ca.crt"
        vendor-client:                # идентичность, которую предъявляем вендору SM-DP+
          keystore:
            certificate: "file:/etc/tls/vendor/client.crt"
            private-key: "file:/etc/tls/vendor/client.key"
          truststore:
            certificate: "file:/etc/tls/vendor/vendor-ca.crt"
server:
  port: 8443
  ssl:
    bundle: server
    client-auth: need                 # рукопожатие не проходит без доверенного клиентского сертификата
```
```java
@Configuration
public class MtlsSecurityConfig {

    @Bean
    SecurityFilterChain chain(HttpSecurity http) throws Exception {
        return http
            .x509(x -> x.subjectPrincipalRegex("CN=(.*?)(?:,|$)")   // CN клиентского сертификата -> principal
                        .userDetailsService(cn -> User.withUsername(cn)
                                .password("")                        // не используется: рукопожатие TLS уже аутентифицировало
                                .roles(cn.equals("billing-api") ? "BILLING" : "SERVICE")
                                .build()))
            .authorizeHttpRequests(a -> a
                .requestMatchers(HttpMethod.POST, "/api/v1/esims/*/activations").hasRole("BILLING")
                .anyRequest().hasRole("SERVICE"))
            .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .csrf(AbstractHttpConfigurer::disable)
            .build();
    }

    @Bean
    RestClient vendorClient(RestClient.Builder builder, RestClientSsl ssl) {
        return builder.baseUrl("https://smdp.vendor.example")
                .apply(ssl.fromBundle("vendor-client"))
                .build();
    }
}
```
Проверка из шелла — заодно так доказывают вендору, что проблема на его стороне:
```bash
$ curl -sS --cert client.crt --key client.key --cacert vendor-ca.crt https://smdp.vendor.example/es2plus/ping
{"status":"ok"}
$ curl -sS --cacert vendor-ca.crt https://smdp.vendor.example/es2plus/ping     # без клиентского сертификата
curl: (56) OpenSSL SSL_read: tlsv13 alert certificate required
```

## Подводные камни
- Доверие заголовку `X-SSL-Client-*` откуда угодно: если порт приложения доступен в обход прокси, любой может подделать идентичность. Привязывай приложение к частной сети и проверяй собственный сертификат или источник прокси.
- Долгоживущие клиентские сертификаты, скопированные в репозитории и образы; утёкший клиентский сертификат — это API-ключ с лишними шагами. Ротируй за дни, а не годы, и автоматизируй.
- Отзыв по-настоящему не работает (CRL устаревают, OCSP игнорируется); реальный механизм отзыва — короткий срок жизни.
- Корпоративные прокси с инспекцией TLS не умеют передавать клиентские сертификаты; mTLS к внешнему партнёру из офисной сети требует исключения на файрволе ([[tls-handshake-and-certificates]]).
- Смешение идентичности и авторизации: mTLS говорит, кто вызывает, а не что ему можно; правила авторизации всё равно нужно писать.
- Связанные заметки: [[authentication-vs-authorization]] — альтернативы на токенах, [[secrets-management]] — где должны жить закрытые ключи.
