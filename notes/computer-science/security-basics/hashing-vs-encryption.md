---
title: Hashing vs encryption
tags: [security, hashing, encryption, passwords, cryptography]
learned: 2026-10-27
---
## TL;DR
**Hashing** is one-way: a fixed-size digest from which the input cannot be recovered; use it for integrity checks, deduplication, content addressing and, with a slow salted algorithm, password storage. **Encryption** is two-way: ciphertext plus a key gives the plaintext back; use it when you must read the data again (card data at rest, PII in a database, TLS on the wire). **Encoding** (Base64, hex, URL encoding) is neither, it is just a representation and gives zero secrecy. Passwords are never encrypted and never hashed with plain SHA-256; they go through **bcrypt, scrypt or Argon2id** with a per-user salt and a cost factor that makes brute force expensive. Key types and signatures are in [[symmetric-and-asymmetric-crypto]].

## Key points
### The three operations
| | Hashing | Encryption | Encoding |
|---|---|---|---|
| Reversible | no | yes, with the key | yes, by anyone |
| Needs a key | no (HMAC: yes) | yes | no |
| Output size | fixed (SHA-256: 32 bytes) | ≈ input size + IV/tag | larger than input |
| Purpose | integrity, identity of content, password verification | confidentiality | transport of bytes as text |
| Examples | SHA-256, SHA-3, BLAKE3, bcrypt, Argon2 | AES-GCM, ChaCha20-Poly1305, RSA-OAEP | Base64, hex, URL encoding |

- A cryptographic hash must be *preimage-resistant* (cannot find input from hash), *second-preimage-resistant* and *collision-resistant* (cannot find two inputs with the same hash). MD5 and SHA-1 are broken for collisions; still fine for non-security checksums, wrong for signatures and certificates.
- `hashCode()` in Java is not a cryptographic hash: 32 bits, designed for bucket distribution, trivially collidable ([[hash-tables]], [[equals-and-hashcode]]).
- Hashing is deterministic: the same input always gives the same digest. That is what makes it useful for deduplication and idempotency keys ([[idempotency]]) and what makes it dangerous for passwords without a salt.

### Password storage
- Why not SHA-256: it is fast by design. A GPU computes billions of SHA-256 per second; a leaked table of unsalted hashes falls to rainbow tables and dictionary attacks within hours.
- **Salt**: a random 16+ byte value per user, stored next to the hash. It makes precomputed tables useless and forces an attacker to crack each user separately. Salt is not secret.
- **Pepper**: an additional secret applied to every password, kept outside the database (in the secret store, [[secrets-management]]); protects hashes if only the DB leaks.
- **Slow, memory-hard functions**: bcrypt (cost 10–12, ~100–250 ms), scrypt, **Argon2id** (the current recommendation: memory ≥ 64 MB, 2–3 iterations, parallelism 1–4). The cost is tuned so a login takes ~100 ms on your hardware and an attacker needs decades.
- Verification is `hash(candidate, stored_salt, stored_params) == stored_hash`, compared in constant time. Modern libraries encode algorithm, params and salt into one string (`$argon2id$v=19$m=65536,t=3,p=4$...`), so parameters can be raised later and old hashes upgraded on the next successful login.
- Spring Security: `PasswordEncoderFactories.createDelegatingPasswordEncoder()` stores `{bcrypt}`/`{argon2}` prefixed hashes and verifies whichever was used; `BCryptPasswordEncoder(12)` or `Argon2PasswordEncoder.defaultsForSpringSecurity_v5_8()` ([[spring-security-basics]]).
- Do not "encrypt" passwords: whoever holds the key (your service) can read every password, and a key leak exposes all of them in plaintext.

### Keyed hashes and integrity
- **HMAC** (hash-based message authentication code) = hash + secret key. It proves a message was produced by someone holding the key and was not altered: webhook signatures (`X-Signature: sha256=...`), JWT `HS256` ([[jwt-authentication]]), signed cookies, API request signing ([[api-security-checklist]]).
- Compare MACs with `MessageDigest.isEqual`, not `Arrays.equals` or `String.equals`, which return early on the first differing byte and leak timing.
- Checksums for integrity without an adversary: CRC32 in ZIP and Kafka record batches, SHA-256 for Docker image digests and Maven artifact verification. If an attacker can replace both the file and its hash, only a signature or HMAC helps.

### Encryption at rest, briefly
- Use an AEAD mode (AES-256-GCM, ChaCha20-Poly1305): confidentiality and integrity together, a random 12-byte nonce per message, never reused with the same key. The mechanics are in [[symmetric-and-asymmetric-crypto]].
- Field-level encryption in the DB (card PAN, document numbers) means you cannot index or search that column; store a separate HMAC of the value for equality lookups, or keep a last-4 column for display. PCI DSS asks exactly this for PAN ([[emv-card-processing]]).
- Encryption keys are secrets with a lifecycle (rotation, revocation); envelope encryption (a data key per record, wrapped by a master key in a KMS) is the standard pattern.

## Example
Registration and login with Argon2 via Spring Security, plus an HMAC for a webhook.
```java
@Service
public class CredentialService {
    private final PasswordEncoder encoder =
            Argon2PasswordEncoder.defaultsForSpringSecurity_v5_8(); // m=16MB,t=2,p=1; raise m in prod
    private final UserRepository users;
    private final byte[] webhookKey;   // from the secret store, not from application.yml

    CredentialService(UserRepository users, @Value("${webhook.key}") String keyB64) {
        this.users = users;
        this.webhookKey = Base64.getDecoder().decode(keyB64);
    }

    public void register(String login, char[] password) {
        String hash = encoder.encode(CharBuffer.wrap(password)); // salt generated and embedded
        users.save(new User(login, hash));
        Arrays.fill(password, '\0');
    }

    public boolean login(String login, char[] password) {
        return users.findByLogin(login)
                .map(u -> encoder.matches(CharBuffer.wrap(password), u.passwordHash()))
                .orElseGet(() -> { encoder.matches("dummy", DUMMY_HASH); return false; }); // same timing
    }

    public boolean webhookSignatureValid(byte[] body, String headerHex) throws GeneralSecurityException {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(webhookKey, "HmacSHA256"));
        byte[] expected = mac.doFinal(body);
        return MessageDigest.isEqual(expected, HexFormat.of().parseHex(headerHex));
    }
}
```

## Pitfalls
- Plain `SHA-256(password)` or `MD5(password + salt)`: salted or not, fast hashes are crackable at GPU speed. Only bcrypt/scrypt/Argon2 are acceptable.
- Treating Base64 as protection: a "token" that is Base64 of `userId:role` is readable and forgeable by anyone. Sign it (HMAC/JWT) or make it opaque and random.
- Hashing low-entropy data (phone numbers, national IDs) and calling it anonymisation: the input space is small, a dictionary of all numbers hashes in seconds. Use HMAC with a secret key or tokenisation.
- Logging the raw password or including it in an exception message on a failed login; Sentry and ELK then store it forever ([[owasp-top-10-for-backend]]).
- Rolling your own comparison, random salt from `java.util.Random`, or home-made "encryption" with XOR. `SecureRandom`, JCA and Spring Security exist for a reason.
- Related: [[symmetric-and-asymmetric-crypto]] for what AES, RSA and signatures actually do; [[jwt-authentication]] for HMAC vs RSA-signed tokens.
