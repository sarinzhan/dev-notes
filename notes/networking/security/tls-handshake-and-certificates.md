---
title: TLS handshake and certificates
tags: [networking, tls, certificates, pki, java]
learned: 2026-10-27
---
## TL;DR
TLS wraps a TCP stream in encryption and authentication. The TLS 1.3 handshake takes one round trip: the client proposes key shares and cipher suites, the server picks one, proves its identity with a certificate signed by a CA the client trusts, and both derive session keys. Trust is a chain: leaf certificate → intermediate CA → root CA in the client's trust store. When "TLS fails" in a backend service it is almost always one of four things: the server's chain is incomplete, the client's trust store lacks the CA, the hostname does not match the certificate (SNI/SAN), or a corporate proxy such as FortiGate is re-signing traffic with its own CA that the JVM has never heard of.

## Key points
### TLS 1.3 handshake (one round trip)
1. **ClientHello**: supported versions, cipher suites, key share (X25519 or P-256 public value), **SNI** (the hostname, in clear text), **ALPN** (`h2`, `http/1.1`), and session ticket for resumption.
2. **ServerHello**: chosen suite and key share; from here on everything is encrypted with handshake keys. Then `EncryptedExtensions`, `Certificate` (the chain), `CertificateVerify` (signature over the transcript with the certificate's private key, proving possession), `Finished`.
3. Client verifies the chain, hostname and signature, sends `Finished`; application data flows. Total: 1 RTT before the first HTTP byte (plus TCP's 1 RTT). Resumption with a pre-shared key allows 0-RTT data, replayable, only for idempotent requests.
- TLS 1.2 needed 2 RTT and allowed RSA key exchange without forward secrecy; 1.3 removed that, removed renegotiation and compression, and left only AEAD ciphers (`TLS_AES_128_GCM_SHA256`, `TLS_AES_256_GCM_SHA384`, `TLS_CHACHA20_POLY1305_SHA256`). TLS 1.0/1.1 are disabled in Java 11.0.11+/Java 17 by default. Key exchange math is in [[symmetric-and-asymmetric-crypto]].
- Server picks the cipher; a server preferring weak suites is a configuration problem, not a client one.

### Certificates and the chain
- An X.509 certificate binds a public key to a subject (`CN`, and the **Subject Alternative Names** that clients actually check), with a validity window, a signature from the issuer, key usage flags and extensions.
- **Chain**: leaf (your host) signed by an **intermediate**, signed by a **root** that lives in the trust store (OS bundle, Java `cacerts`, Mozilla bundle in curl). The server must send leaf + intermediates; sending the root is harmless but useless. A missing intermediate is the most common "works in Chrome, fails in Java" bug: browsers fetch intermediates via AIA, the JDK does not.
- Validation: signature chain to a trusted root, `notBefore`/`notAfter`, hostname against SAN (CN alone is ignored by modern clients), revocation (CRL/OCSP, in practice mostly skipped; OCSP stapling lets the server attach a fresh proof), name constraints and key usage.
- Let's Encrypt (ACME) issues 90-day certificates; automation (cert-manager in Kubernetes, certbot, Caddy) is mandatory, not a luxury. Public CAs are capping maximum lifetime to 47 days by 2029, so manual renewal is dead.
- **SNI** lets one IP serve many hostnames: the client names the host in ClientHello so the server can choose the right certificate. A client that omits SNI (old Java, raw `openssl s_client` without `-servername`) gets the default certificate and a hostname mismatch.
- **Wildcard** `*.beeline.kg` covers one level only (`api.beeline.kg`, not `a.b.beeline.kg`).

### Where it fails, and the message you will see
| Symptom | Cause |
|---|---|
| `PKIX path building failed: unable to find valid certification path` | CA not in the JVM trust store: private CA, missing intermediate, or a TLS-inspecting proxy |
| `No subject alternative DNS name matching api.example.com found` | certificate issued for other names; wrong SNI; connecting by IP |
| `certificate_expired` / `NotAfter` | expired; also clock skew on the client |
| `handshake_failure` | no common cipher/version (old TLS 1.0 server, or a server demanding a client cert; see [[mtls-and-service-to-service-auth]]) |
| `Received fatal alert: protocol_version` | peer only speaks TLS 1.0/1.1 |
| works with `curl -k`, fails without | any of the above; `-k` is diagnosis, never a fix |

- **Corporate TLS inspection (FortiGate, Zscaler, Palo Alto)**: the firewall terminates TLS, inspects, and re-encrypts with a certificate it minted on the fly, signed by the company CA. Browsers trust it because IT pushed the CA into the OS store; the JVM has its own `cacerts` and does not, so every outbound HTTPS from Java, Maven, Docker builds and `pip` fails with `PKIX path building failed`. The inspecting proxy also breaks certificate pinning, mTLS, and HTTP/3 (UDP is usually just dropped), and its certificate often lacks the right SAN for less common hosts. The fix is to import the corporate CA into every trust store that matters (`keytool -importcert` into `cacerts` or a dedicated store, `/usr/local/share/ca-certificates/` + `update-ca-certificates` for the OS, the CA bundle in Docker build stages), or to get the destination hosts exempted from inspection. Disabling verification "temporarily" is how production ends up trusting everything.

### Java specifics
- Trust store: `$JAVA_HOME/lib/security/cacerts` (password `changeit`), overridable with `-Djavax.net.ssl.trustStore`. Key store (your own certificate + private key) for servers and mTLS: PKCS12 is the default since Java 9.
- Spring Boot 3.1+ **SSL bundles**: `spring.ssl.bundle.pem.*` or `.jks.*` with hot reload, usable by the server, `RestClient`, `WebClient`, data-source and Kafka clients ([[spring-boot-properties-and-profiles]]).
- Debug: `-Djavax.net.debug=ssl:handshake` prints the whole handshake, including which trust anchors were loaded.

## Example
Diagnosing a failing outbound call from a Java service, bottom-up. First see what the server sends, then what the JVM trusts.
```bash
# 1. Show the chain the server actually sends (with SNI!) and the verify result
$ openssl s_client -connect smdp.example.com:443 -servername smdp.example.com -showcerts </dev/null 2>/dev/null \
    | grep -E 'depth=|s:|i:|Verify return|Protocol|Cipher'
depth=0 CN = smdp.example.com
   i:C = US, O = Let's Encrypt, CN = R11          # issuer of the leaf
 0 s:CN = smdp.example.com
   i:C = US, O = Let's Encrypt, CN = R11
Verify return code: 21 (unable to verify the first certificate)   # intermediate R11 is MISSING from the chain
Protocol  : TLSv1.3
Cipher    : TLS_AES_256_GCM_SHA384

# 2. Same check from inside the corporate network: a different issuer means a TLS-inspecting proxy
$ openssl s_client -connect smdp.example.com:443 -servername smdp.example.com </dev/null 2>/dev/null | grep 'i:'
   i:CN = FortiGate CA, O = Fortinet                 # the chain was re-signed by the firewall

# 3. Does the JVM trust that issuer?
$ keytool -list -cacerts -storepass changeit | grep -i -E 'fortinet|R11|isrg'
isrgrootx1 [jdk], ...                              # Let's Encrypt root is there; the FortiGate CA is not

# 4. Fix for the proxy case: import the corporate CA into a dedicated trust store, not into cacerts of every JDK image
$ keytool -importcert -alias corp-ca -file fortigate-ca.pem -keystore /etc/ssl/corp-truststore.p12 \
    -storetype PKCS12 -storepass changeit -noprompt
```
```yaml
# Spring Boot 3.x: give the outbound client a bundle that includes the corporate CA
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
        .apply(restClientSsl.fromBundle("smdp-client"))   // RestClientSsl bean from Spring Boot
        .build();
```

## Pitfalls
- `-k`, `verify=false`, custom `TrustManager` that accepts everything: they silence the error and hand the connection to any attacker on the path. Reviewers should reject them on sight.
- Connecting by IP or through a hosts-file alias: the SAN will not match. Use the real hostname or add the alias to the certificate.
- Rebuilding the Docker image with a new JDK silently drops a CA someone imported into `cacerts`; keep custom CAs in a mounted trust store or an SSL bundle.
- Expired intermediates and cross-signed roots (the Let's Encrypt DST Root expiry in 2021) break old trust stores even when the leaf is valid; keep base images updated.
- Clock skew in a container makes valid certificates "not yet valid"; check `date` before anything else.
- Related: [[mtls-and-service-to-service-auth]] for the client-certificate side, [[network-debugging-toolkit]] for the `openssl`/`curl` recipes in one place.
