---
title: Homomorphic encryption
tags: [security, cryptography, encryption, privacy, databases]
learned: 2026-10-30
---
## TL;DR
Normal encryption protects data at rest and in transit, but to compute on it (filter, sum, index, search) the server must **decrypt** it first, so the server, its admins and any attacker on it see plaintext. **Homomorphic encryption** lets the server compute directly on ciphertexts: the result, once the client decrypts it, equals the result of the same computation on plaintexts. The database never sees the data or the query values. The catch: it is extremely slow (thousands to millions of times slower), ciphertexts are large, and only some operations are practical. In 2026 it is used for niche private computation, not for general databases.

## Key points
### Why we cannot always encrypt
- **At rest** (disk, backups: TDE, LUKS) and **in transit** (TLS) are solved. **In use** is not: to evaluate `WHERE salary > 5000`, build a B-tree or run `SUM`, the database needs plaintext in memory.
- Encrypting columns in the application (client-side encryption) hides data from the DB but then the DB cannot index, sort, compare or aggregate those columns. Search becomes "fetch everything and decrypt in the app".
- Partial workarounds: deterministic encryption (equality search, but leaks equal values), order-preserving encryption (range queries, leaks order), blind indexes (HMAC of the value). Each leaks something.
- Basics of symmetric and asymmetric encryption: [[symmetric-and-asymmetric-crypto]], [[hashing-vs-encryption]].

### What "homomorphic" means
- An encryption `E` is homomorphic for an operation if `E(a) ⊕ E(b) = E(a + b)` for some operation `⊕` on ciphertexts.
- **Partially homomorphic**: one operation only. RSA (textbook) is multiplicative; Paillier is additive (`E(a)·E(b) = E(a+b)`), enough for encrypted sums and voting.
- **Somewhat / leveled**: both addition and multiplication, but each operation adds noise; after a fixed depth of multiplications the ciphertext becomes undecryptable.
- **Fully homomorphic (FHE)**: any computation, thanks to **bootstrapping** (Craig Gentry, 2009), which refreshes the noise by homomorphically evaluating the decryption. Modern schemes: BFV and BGV (exact integers), CKKS (approximate reals, ML), TFHE (boolean gates, fast bootstrapping).
- Libraries: Microsoft SEAL, OpenFHE, IBM HElib, Zama Concrete/TFHE-rs.

### How an encrypted database search works (course demo idea)
1. The client generates keys, encrypts each record field and uploads ciphertexts to the server.
2. To search, the client encrypts the query value and sends it.
3. The server evaluates an equality circuit between the encrypted query and every encrypted record (it cannot use an index: it does not know which record matches) and returns an encrypted result vector.
4. The client decrypts and sees which records matched. The server learned nothing, but it did work over **every** row.

### Is it ready?
- Cost: an addition is microseconds to milliseconds, a bootstrapped operation milliseconds or more; ciphertexts are 10–1000× bigger than plaintexts; keys can be megabytes to gigabytes.
- No indexes, no early exit: every query is a full scan over ciphertexts.
- Practical today: encrypted aggregation and statistics, private set intersection, private ML inference on small models, password breach checks, some advertising measurement. Hardware accelerators (DARPA DPRIVE program) and GPU implementations are narrowing the gap.
- Alternatives for "compute on secret data": **trusted execution environments** (Intel SGX/TDX, AMD SEV, AWS Nitro Enclaves: fast, but trust the hardware vendor), **secure multi-party computation**, and **searchable symmetric encryption**.

## Example
```python
# additive homomorphism with the Paillier scheme (python-paillier)
from phe import paillier

pub, priv = paillier.generate_paillier_keypair(n_length=2048)
salaries = [4200, 5100, 3900]
enc = [pub.encrypt(s) for s in salaries]   # the server stores only these

enc_total = sum(enc[1:], enc[0])            # server adds ciphertexts, never sees values
enc_raise = enc_total * 2                   # scalar multiplication also works
print(priv.decrypt(enc_total))              # 13200, only the key holder can read it
```

## Pitfalls
- Assuming "encrypted database" means the server cannot see data: TDE and disk encryption decrypt in memory for every query.
- Choosing order-preserving or deterministic encryption without accepting what they leak (frequency, order).
- Expecting FHE to run normal SQL workloads: no indexes, huge ciphertexts, orders of magnitude slower.
- Rolling your own crypto instead of a vetted library and parameter set (security level, noise budget).
- Related: [[database-security-and-permissions]], [[secrets-management]], [[owasp-top-10-for-backend]].
