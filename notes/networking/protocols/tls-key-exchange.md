---
title: TLS key exchange, versions and certificate files
tags: [networking, tls, cryptography, openssl, java]
learned: 2026-10-29
---
## TL;DR
TLS uses asymmetric crypto only to agree on a secret and to prove identity, then switches to fast symmetric AEAD encryption for the data. The secret comes from an ephemeral (EC)Diffie-Hellman exchange, so recorded traffic stays safe even if the server's private key leaks later (forward secrecy); the certificate key only signs. TLS 1.3 sends the key share in the first message and finishes in 1 RTT instead of 2, with optional 0-RTT resumption that can be replayed. For local HTTPS you generate a key, a self-signed certificate or a CSR with `openssl`, and hand Java a PKCS12 file or PEM files through a Spring Boot SSL bundle.

## Key points
### Why both symmetric and asymmetric
| | Symmetric (AES-GCM, ChaCha20-Poly1305) | Asymmetric (RSA, ECDSA, X25519) |
|---|---|---|
| Keys | one shared secret | public + private pair |
| Speed | GB/s per core with AES-NI | thousands of operations per second |
| Problem | how do two strangers get the same key? | far too slow and size-limited for bulk data |
| Role in TLS | encrypts and authenticates every record | key agreement and signatures in the handshake |

- The handshake uses the slow, asymmetric part once, to produce a shared secret; HKDF expands it into separate keys per direction for the handshake and for application data. Details of the primitives: [[symmetric-and-asymmetric-crypto]], [[hashing-vs-encryption]].

### Key exchange: RSA vs (EC)DHE
- **RSA key exchange (TLS 1.2 and older)**: the client generates the pre-master secret and encrypts it with the server's RSA public key from the certificate. Anyone who records the traffic and later obtains that private key (a leak, a subpoena, a decommissioned disk) decrypts every past session. No forward secrecy.
- **(EC)DHE**: both sides generate a throwaway key pair per connection, exchange the public halves and compute the same secret; the private halves are deleted after the handshake. The certificate key only **signs** the exchange (`ServerKeyExchange` in 1.2, `CertificateVerify` in 1.3), so its leak lets an attacker impersonate the server in future, not read the past. This is **forward secrecy**.
- Groups: X25519 (default almost everywhere), P-256; browsers and Cloudflare already use the hybrid post-quantum `X25519MLKEM768`, which protects recorded traffic against a future quantum computer.

### TLS 1.2 vs TLS 1.3
| | TLS 1.2 | TLS 1.3 (RFC 8446, 2018) |
|---|---|---|
| Round trips before data | 2 | 1; 0 with resumption + early data |
| Key exchange | RSA, DH, ECDHE | ECDHE / DHE only, always forward secret |
| Ciphers | many, incl. CBC, RC4, 3DES | five AEAD suites |
| Cipher suite names | key exchange + auth + cipher + MAC (`TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256`) | cipher + hash only (`TLS_AES_128_GCM_SHA256`) |
| Encrypted handshake | no, certificate visible | everything after ServerHello |
| Removed | | renegotiation, compression, static RSA/DH, SHA-1, custom DH groups |

- 1.3 saves a round trip because the client guesses the group and sends its key share in ClientHello. If the guess is wrong, the server replies with `HelloRetryRequest` and the handshake costs 2 RTT. The message flow is animated in [[tls-handshake-and-certificates]]; the round-trip arithmetic with TCP is in [[connection-establishment-cost]].

### 0-RTT and replay
- After a full handshake the server sends a session ticket (a pre-shared key). Next time the client can put the request into the first flight as **early data**.
- Early data is encrypted but has no protection against replay: an attacker on the path copies the first flight and sends it again, maybe to another server of the cluster. Allow it only for idempotent requests; servers send `425 Too Early` to reject it, and proxies forward it with the `Early-Data: 1` header. The JDK's TLS stack supports resumption but neither sends nor accepts early data.

### Keys, CSRs and file formats
- **Private key**: PEM `BEGIN PRIVATE KEY` (PKCS#8) or the older `BEGIN RSA PRIVATE KEY` (PKCS#1). **CSR** (PKCS#10): public key + requested subject and SANs, signed by the private key; you send it to a CA and the key never leaves the host.
- **Self-signed certificate**: issuer = subject; fine for local development and tests, useless for public clients unless they explicitly trust that exact certificate.
- **PEM** is Base64 text, **DER** the same bytes in binary. **PKCS12** (`.p12`, `.pfx`) bundles key + chain under a password and is the default keystore type since Java 9; **JKS** is the legacy Java-only format.

## Example
A local CA, a server key with a CSR, the signed certificate, and a PKCS12 file for the JVM (OpenSSL 3.x).
```bash
# local CA: key + self-signed certificate
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
  -keyout ca.key -out ca.crt -days 3650 -subj "/CN=esim dev CA"

# server key and CSR with the SANs clients will check
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out server.key
openssl req -new -key server.key -out server.csr -subj "/CN=esim-api.local" \
  -addext "subjectAltName=DNS:esim-api.local,DNS:localhost,IP:127.0.0.1"

# sign the CSR with the CA, keeping the SAN extension from the CSR
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 90 -copy_extensions copy -out server.crt
openssl x509 -in server.crt -noout -subject -issuer -ext subjectAltName

# key + chain into PKCS12 for Java, then inspect it
openssl pkcs12 -export -inkey server.key -in server.crt -certfile ca.crt \
  -name esim-api -out server.p12 -passout pass:changeit
keytool -list -keystore server.p12 -storetype PKCS12 -storepass changeit

# test: trust only the dev CA
curl --cacert ca.crt https://localhost:8443/actuator/health
```
```yaml
# Spring Boot 3.1+: serve HTTPS from the PEM files (or point a jks bundle at server.p12)
server:
  port: 8443
  ssl:
    bundle: esim-server
spring:
  ssl:
    bundle:
      pem:
        esim-server:
          keystore:
            certificate: "file:/etc/esim/tls/server.crt"
            private-key: "file:/etc/esim/tls/server.key"
          reload-on-update: true
```

## Pitfalls
- A certificate without SANs: modern clients ignore CN and fail with a hostname mismatch. Always pass `subjectAltName`.
- `openssl x509 -req` before OpenSSL 3.0 drops the CSR's extensions; add them with `-extfile` or the certificate has no SAN.
- Enabling 0-RTT on a proxy in front of an API with non-idempotent POSTs: a replayed payment request runs twice ([[idempotency]]).
- Committing `server.key` or a `.p12` with the password in `application.yml`; keep them in a secret store ([[secrets-management]]).
- A server that still offers TLS 1.2 with `TLS_RSA_*` suites has no forward secrecy for those clients; restrict 1.2 to ECDHE suites.
- Related: [[tls-handshake-and-certificates]] for chains, trust stores and failure messages; [[mtls-and-service-to-service-auth]] for client certificates.
