---
title: Рукопожатие TLS и сертификаты
tags: [networking, tls, certificates, pki, java]
learned: 2026-10-27
---
## Кратко
TLS оборачивает TCP-поток шифрованием и аутентификацией. Рукопожатие TLS 1.3 занимает один round trip: клиент предлагает key shares и наборы шифров (cipher suites), сервер выбирает один, доказывает свою идентичность сертификатом, подписанным CA, которому доверяет клиент, и обе стороны выводят сессионные ключи. Доверие — это цепочка: конечный сертификат (leaf) → промежуточный CA → корневой CA в хранилище доверенных сертификатов (trust store) клиента. Когда в бэкенд-сервисе «падает TLS», это почти всегда одно из четырёх: цепочка сервера неполная, в trust store клиента нет нужного CA, имя хоста не совпадает с сертификатом (SNI/SAN) или корпоративный прокси вроде FortiGate переподписывает трафик своим CA, о котором JVM никогда не слышала.

## Основное
### Рукопожатие TLS 1.3 (один round trip)
1. **ClientHello**: поддерживаемые версии, наборы шифров, key share (публичное значение X25519 или P-256), **SNI** (имя хоста, открытым текстом), **ALPN** (`h2`, `http/1.1`) и session ticket для возобновления.
2. **ServerHello**: выбранный набор и key share; начиная отсюда всё шифруется ключами рукопожатия. Затем `EncryptedExtensions`, `Certificate` (цепочка), `CertificateVerify` (подпись над транскриптом закрытым ключом сертификата, доказывающая владение им), `Finished`.
3. Клиент проверяет цепочку, имя хоста и подпись, отправляет `Finished`; идут данные приложения. Итого: 1 RTT до первого байта HTTP (плюс 1 RTT у TCP). Возобновление с pre-shared key позволяет данные 0-RTT — их можно воспроизвести повторно, поэтому только для идемпотентных запросов.

```anim
tls13-handshake
```
- TLS 1.2 требовал 2 RTT и допускал обмен ключами через RSA без прямой секретности (forward secrecy). Зачем нужны оба вида криптографии, (EC)DHE, отличия 1.2 от 1.3, повтор 0-RTT и генерация ключей и сертификатов через `openssl`: [[tls-key-exchange]]. TLS 1.0/1.1 по умолчанию отключены в Java 11.0.11+/Java 17.
- Шифр выбирает сервер; сервер, предпочитающий слабые наборы, — это проблема его конфигурации, а не клиента.

### Сертификаты и цепочка
- Сертификат X.509 связывает публичный ключ с субъектом (`CN` и **Subject Alternative Names**, которые клиенты и проверяют на самом деле), с окном действия, подписью издателя, флагами использования ключа и расширениями.
- **Цепочка**: конечный сертификат (твой хост) подписан **промежуточным**, тот подписан **корневым**, который живёт в trust store (бандл ОС, `cacerts` в Java, бандл Mozilla в curl). Сервер должен отправлять конечный + промежуточные; отправка корневого безвредна, но бесполезна. Отсутствующий промежуточный сертификат — самый частый баг «работает в Chrome, падает в Java»: браузеры подгружают промежуточные через AIA, а JDK — нет.
- Проверка: цепочка подписей до доверенного корня, `notBefore`/`notAfter`, имя хоста против SAN (один CN современные клиенты игнорируют), отзыв (CRL/OCSP, на практике в основном пропускается; OCSP stapling позволяет серверу приложить свежее доказательство), ограничения имён и использование ключа.
- Let's Encrypt (ACME) выдаёт сертификаты на 90 дней; автоматизация (cert-manager в Kubernetes, certbot, Caddy) обязательна, а не роскошь. Публичные CA к 2029 году ограничивают максимальный срок жизни 47 днями, так что ручное продление мертво.
- **SNI** позволяет одному IP обслуживать много имён хостов: клиент называет хост в ClientHello, чтобы сервер мог выбрать нужный сертификат. Клиент, опускающий SNI (старая Java, голый `openssl s_client` без `-servername`), получает сертификат по умолчанию и несовпадение имени хоста.
- **Wildcard** `*.beeline.kg` покрывает только один уровень (`api.beeline.kg`, но не `a.b.beeline.kg`).

### Где ломается и какое сообщение ты увидишь
| Симптом | Причина |
|---|---|
| `PKIX path building failed: unable to find valid certification path` | CA нет в trust store JVM: частный CA, отсутствующий промежуточный сертификат или прокси с инспекцией TLS |
| `No subject alternative DNS name matching api.example.com found` | сертификат выпущен на другие имена; неверный SNI; подключение по IP |
| `certificate_expired` / `NotAfter` | истёк; также расхождение часов на клиенте |
| `handshake_failure` | нет общего шифра/версии (старый сервер на TLS 1.0 или сервер, требующий клиентский сертификат; см. [[mtls-and-service-to-service-auth]]) |
| `Received fatal alert: protocol_version` | собеседник говорит только на TLS 1.0/1.1 |
| работает с `curl -k`, без него падает | любое из перечисленного; `-k` — это диагностика, а не исправление |

- **Корпоративная инспекция TLS (FortiGate, Zscaler, Palo Alto)**: файрвол завершает TLS, инспектирует и шифрует заново сертификатом, выпущенным на лету и подписанным CA компании. Браузеры ему доверяют, потому что IT-отдел добавил этот CA в хранилище ОС; у JVM свой `cacerts`, и там его нет, поэтому каждый исходящий HTTPS из Java, Maven, сборок Docker и `pip` падает с `PKIX path building failed`. Инспектирующий прокси также ломает certificate pinning, mTLS и HTTP/3 (UDP обычно просто отбрасывается), а в его сертификате для редких хостов часто нет правильного SAN. Решение — импортировать корпоративный CA в каждый важный trust store (`keytool -importcert` в `cacerts` или в отдельное хранилище, `/usr/local/share/ca-certificates/` + `update-ca-certificates` для ОС, бандл CA в стадиях сборки Docker) или добиться исключения хостов назначения из инспекции. «Временное» отключение проверки — это путь, которым продакшен приходит к доверию всему подряд.

### Особенности Java
- Trust store: `$JAVA_HOME/lib/security/cacerts` (пароль `changeit`), переопределяется через `-Djavax.net.ssl.trustStore`. Key store (твой собственный сертификат + закрытый ключ) для серверов и mTLS: начиная с Java 9 по умолчанию PKCS12.
- **SSL bundles** в Spring Boot 3.1+: `spring.ssl.bundle.pem.*` или `.jks.*` с горячей перезагрузкой, используются сервером, `RestClient`, `WebClient`, data source и клиентами Kafka ([[spring-boot-properties-and-profiles]]).
- Отладка: `-Djavax.net.debug=ssl:handshake` печатает всё рукопожатие, включая то, какие доверенные якоря (trust anchors) были загружены.

## Пример
Диагностика падающего исходящего вызова из Java-сервиса снизу вверх. Сначала смотрим, что отправляет сервер, затем — чему доверяет JVM.
```bash
# 1. Показать цепочку, которую сервер реально отправляет (с SNI!), и результат проверки
$ openssl s_client -connect smdp.example.com:443 -servername smdp.example.com -showcerts </dev/null 2>/dev/null \
    | grep -E 'depth=|s:|i:|Verify return|Protocol|Cipher'
depth=0 CN = smdp.example.com
   i:C = US, O = Let's Encrypt, CN = R11          # издатель конечного сертификата
 0 s:CN = smdp.example.com
   i:C = US, O = Let's Encrypt, CN = R11
Verify return code: 21 (unable to verify the first certificate)   # промежуточного R11 НЕТ в цепочке
Protocol  : TLSv1.3
Cipher    : TLS_AES_256_GCM_SHA384

# 2. Та же проверка изнутри корпоративной сети: другой издатель означает прокси с инспекцией TLS
$ openssl s_client -connect smdp.example.com:443 -servername smdp.example.com </dev/null 2>/dev/null | grep 'i:'
   i:CN = FortiGate CA, O = Fortinet                 # цепочка переподписана файрволом

# 3. Доверяет ли JVM этому издателю?
$ keytool -list -cacerts -storepass changeit | grep -i -E 'fortinet|R11|isrg'
isrgrootx1 [jdk], ...                              # корень Let's Encrypt есть; CA FortiGate нет

# 4. Исправление для случая с прокси: импортировать корпоративный CA в отдельный trust store, а не в cacerts каждого образа JDK
$ keytool -importcert -alias corp-ca -file fortigate-ca.pem -keystore /etc/ssl/corp-truststore.p12 \
    -storetype PKCS12 -storepass changeit -noprompt
```
```yaml
# Spring Boot 3.x: дать исходящему клиенту bundle, включающий корпоративный CA
spring:
  ssl:
    bundle:
      pem:
        smdp-client:
          truststore:
            certificate: "file:/etc/ssl/corp-ca-chain.pem"
```
```java
RestClient smdp = RestClient.builder()
        .baseUrl("https://smdp.example.com")
        .apply(restClientSsl.fromBundle("smdp-client"))   // bean RestClientSsl из Spring Boot
        .build();
```

## Подводные камни
- `-k`, `verify=false`, собственный `TrustManager`, принимающий всё: они заглушают ошибку и отдают соединение любому атакующему на пути. Ревьюеры должны отклонять их с первого взгляда.
- Подключение по IP или через псевдоним из hosts-файла: SAN не совпадёт. Используй настоящее имя хоста или добавь псевдоним в сертификат.
- Пересборка Docker-образа с новым JDK молча теряет CA, который кто-то импортировал в `cacerts`; держи свои CA в смонтированном trust store или SSL bundle.
- Истёкшие промежуточные сертификаты и кросс-подписанные корни (истечение DST Root у Let's Encrypt в 2021) ломают старые trust store, даже когда конечный сертификат валиден; держи базовые образы обновлёнными.
- Расхождение часов в контейнере делает валидные сертификаты «ещё не действительными»; прежде всего проверь `date`.
- Связанные заметки: [[tls-key-exchange]] — криптография за рукопожатием и рецепты `openssl`/PKCS12, [[mtls-and-service-to-service-auth]] — сторона клиентского сертификата, [[network-debugging-toolkit]] — рецепты `openssl`/`curl` в одном месте.
