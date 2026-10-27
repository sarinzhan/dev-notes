---
title: Symmetric and asymmetric cryptography
tags: [security, cryptography, aes, rsa, signatures]
learned: 2026-10-27
---
## TL;DR
**Symmetric** encryption (AES, ChaCha20) uses one shared key for both directions: fast, suited to bulk data, but both sides must already hold the key. **Asymmetric** cryptography (RSA, elliptic curves) uses a key pair: anything encrypted with the public key only the private key can open, and anything **signed** with the private key anyone can verify with the public key. Real systems combine them: an asymmetric handshake (key exchange + signature to prove identity) agrees a fresh symmetric key, then symmetric AEAD protects the traffic; that is TLS ([[tls-handshake-and-certificates]]), SSH, and envelope encryption in a KMS. **HMAC** is the symmetric cousin of a signature: integrity and authenticity when both sides share a secret. One-way hashing versus encryption is in [[hashing-vs-encryption]].

## Key points
### Symmetric
- **AES** (128/256-bit keys) is a block cipher; a *mode* turns it into something usable. ECB leaks patterns (never use), CBC needs padding and a MAC, **GCM** is authenticated encryption (AEAD): it produces ciphertext plus a 16-byte tag, and decryption fails if a single bit was altered. ChaCha20-Poly1305 is the AEAD of choice where AES hardware acceleration is missing.
- Every encryption needs a unique **nonce/IV** (12 bytes for GCM). Reusing a nonce with the same key under GCM breaks both confidentiality and the integrity key. Generate with `SecureRandom` and store it next to the ciphertext, or use a counter that is persisted; rotate the key well before 2^32 messages.
- Performance: AES-GCM with AES-NI runs at gigabytes per second; this is why TLS and disk encryption are symmetric after the handshake.
- Key sizes: AES-128 is still fine, AES-256 is the compliance default. Keys are random bytes from `KeyGenerator`/`SecureRandom`, never derived from a password directly; if they must be, go through PBKDF2/Argon2 (a KDF) with a salt.

### Asymmetric
| | RSA | Elliptic curve (ECDSA / Ed25519, ECDH / X25519) |
|---|---|---|
| Key size for ~128-bit security | 3072 bits | 256 bits |
| Operations | encrypt (OAEP), sign (PSS) | sign, key agreement; no direct encryption |
| Speed | slow signing, fast verification | fast both; tiny keys and signatures |
| Where you meet it | older certificates, JWT `RS256`, legacy HSM/EMV issuer keys | TLS 1.3 key exchange, SSH keys, JWT `ES256`/`EdDSA`, modern certificates |

- **Encryption with a public key** is only used for small payloads (a symmetric key, a PIN block): RSA-OAEP can encrypt at most key size minus padding. Encrypting a file "with RSA" really means generating an AES key, encrypting the file with it, and encrypting the AES key with RSA: **hybrid / envelope encryption**. KMS services (AWS KMS, Vault transit) expose exactly this: a data key per object, wrapped by a master key that never leaves the HSM.
- **Key agreement** (ECDH / X25519): two parties each combine their private key with the other's public key and arrive at the same shared secret without sending it. With fresh ephemeral keys per session this gives *forward secrecy*: a stolen long-term key cannot decrypt past traffic. TLS 1.3 made this mandatory.
- **Digital signature**: hash the message, encrypt the hash with the private key (RSA) or run the EC signing algorithm; the verifier recomputes the hash and checks it with the public key. Gives integrity, authenticity and *non-repudiation*: only the private key holder could have signed. Certificates are signatures by a CA over a public key plus identity; EMV cards carry issuer-signed data the terminal verifies offline ([[emv-card-processing]]).
- The public key is public, but *binding* it to an identity is the hard problem: PKI/CA chains, SSH `known_hosts`, key pinning, or out-of-band fingerprints. Service-to-service identity built on this is in [[mtls-and-service-to-service-auth]].

### HMAC and when it is enough
- HMAC-SHA256 over a message with a shared secret gives integrity and authenticity but **not** non-repudiation: either party could have produced it. Fine inside one trust domain (two of your own services, a webhook from a provider who shares a secret with you, JWT `HS256` issued and verified by the same service). Use signatures when the verifier must not be able to forge (tokens verified by many services, documents, software releases) ([[jwt-authentication]], [[api-security-checklist]]).

### Choosing in practice
| Need | Use |
|---|---|
| Encrypt data at rest in your DB | AES-256-GCM, per-record data key, master key in KMS/Vault |
| Protect traffic between services | TLS 1.3 (mTLS if client identity matters) |
| Sign a token many services verify | ES256 / EdDSA / RS256, public key via JWKS |
| Authenticate a partner webhook | HMAC-SHA256 with a shared secret and a timestamp |
| Store passwords | not encryption: Argon2id / bcrypt |

## Example
Envelope encryption of a subscriber document number: a random AES data key per record, wrapped by a master key (here a local AES key standing in for a KMS call), with the record id as GCM associated data so a ciphertext cannot be moved between rows.
```java
public final class FieldCrypto {
    private static final SecureRandom RNG = new SecureRandom();
    private final SecretKey masterKey;  // in production: KMS/Vault wrap-unwrap, key never in the JVM

    public FieldCrypto(SecretKey masterKey) { this.masterKey = masterKey; }

    public record Sealed(byte[] wrappedDataKey, byte[] iv, byte[] ciphertext) {}

    public Sealed seal(String recordId, byte[] plaintext) throws GeneralSecurityException {
        SecretKey dataKey = KeyGenerator.getInstance("AES").generateKey();      // 256-bit by default on modern JDKs
        byte[] iv = new byte[12];
        RNG.nextBytes(iv);
        Cipher gcm = Cipher.getInstance("AES/GCM/NoPadding");
        gcm.init(Cipher.ENCRYPT_MODE, dataKey, new GCMParameterSpec(128, iv));
        gcm.updateAAD(recordId.getBytes(StandardCharsets.UTF_8));              // binds ciphertext to the row
        byte[] ct = gcm.doFinal(plaintext);

        Cipher wrap = Cipher.getInstance("AESWrap");                            // RFC 3394 key wrap
        wrap.init(Cipher.WRAP_MODE, masterKey);
        return new Sealed(wrap.wrap(dataKey), iv, ct);
    }

    public byte[] open(String recordId, Sealed s) throws GeneralSecurityException {
        Cipher unwrap = Cipher.getInstance("AESWrap");
        unwrap.init(Cipher.UNWRAP_MODE, masterKey);
        SecretKey dataKey = (SecretKey) unwrap.unwrap(s.wrappedDataKey(), "AES", Cipher.SECRET_KEY);
        Cipher gcm = Cipher.getInstance("AES/GCM/NoPadding");
        gcm.init(Cipher.DECRYPT_MODE, dataKey, new GCMParameterSpec(128, s.iv()));
        gcm.updateAAD(recordId.getBytes(StandardCharsets.UTF_8));
        return gcm.doFinal(s.ciphertext());                                     // AEADBadTagException if tampered
    }
}
```
Rotating the master key means re-wrapping the small data keys, not re-encrypting every record.

## Pitfalls
- `AES/ECB` (the JDK default when you write `Cipher.getInstance("AES")`) or CBC without a MAC: pattern leaks and padding-oracle attacks. Always name the full transformation and prefer GCM.
- Static or reused IVs "for determinism" with GCM: catastrophic. If you need deterministic encryption for equality lookups, use a separate HMAC column instead.
- RSA with PKCS#1 v1.5 padding for encryption, 1024-bit keys, SHA-1 signatures: all deprecated; use OAEP/PSS or move to EC.
- Keys in `application.yml`, in the image, or in git ([[secrets-management]]); a key next to the ciphertext is no encryption.
- Hand-written protocols: "we encrypt the JSON with AES and send it over HTTP" is weaker than plain HTTPS and far more work. Use TLS and sign at the application layer only when you need non-repudiation or end-to-end through intermediaries.
- Related: [[tls-handshake-and-certificates]] for how these primitives compose on the wire, [[hashing-vs-encryption]] for digests, salts and password hashing.
