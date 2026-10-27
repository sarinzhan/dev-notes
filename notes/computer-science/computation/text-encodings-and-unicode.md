---
title: Text encodings and Unicode
tags: [unicode, utf-8, encoding, strings, fundamentals]
learned: 2026-10-27
---
## TL;DR
**Unicode** assigns every character a number, a **code point** (U+0000 … U+10FFFF, ~1.1 million slots). An **encoding** turns code points into bytes: **UTF-8** uses 1–4 bytes per code point, is ASCII-compatible and is the default everywhere on the wire and on disk; **UTF-16** uses 2 or 4 bytes and is what Java's `char` and `String` API expose. A Java `char` is a UTF-16 *code unit*, not a character: an emoji or a rare CJK ideograph is two chars (a surrogate pair), and a user-visible character such as "é" may be one code point or two (e + combining accent) depending on **normalization**. Most encoding bugs come from (1) assuming the platform default charset, (2) counting `length()` as characters, (3) comparing strings without normalizing. Java `String` internals are in [[strings-and-string-pool]].

## Key points
### Code points, code units, graphemes
| Term | Meaning | Example: e + U+0301 + U+1F600 (emoji) |
|---|---|---|
| Grapheme cluster | what a user sees as one character | 2 |
| Code point | Unicode scalar value | 3: U+0065, U+0301, U+1F600 |
| UTF-16 code unit (`char`) | 16-bit unit; code points above U+FFFF use a surrogate pair | 4 |
| UTF-8 byte | 1–4 bytes per code point | 1 + 2 + 4 = 7 |

- `String.length()` counts chars (UTF-16 units). `codePointCount(0, length())` counts code points. Grapheme counting needs `BreakIterator.getCharacterInstance()` (or ICU4J for full correctness).
- `charAt(i)` on a surrogate half returns garbage; use `codePoints()` streams or `Character.isSurrogate`. `substring` in the middle of a pair produces an unpaired surrogate that becomes `?` or U+FFFD on encoding.
- Since Java 9 (compact strings) a `String` stores Latin-1 as 1 byte per char internally and UTF-16 only when needed; this is invisible to the API.

### Encodings
- **ASCII**: 7-bit, 128 characters, a subset of every encoding below.
- **Latin-1 / Windows-1251 / KOI8-R**: single-byte legacy encodings; Cyrillic text in the wrong one becomes mojibake. Still met in old telecom and banking interfaces (SMS in GSM 7-bit or UCS-2, EMV tags).
- **UTF-8**: ASCII bytes are unchanged (one byte, top bit 0); multi-byte sequences have distinctive lead and continuation bytes (`110xxxxx 10xxxxxx` …), so the decoder can resynchronise and byte-searching for ASCII delimiters is safe. No byte-order problem. Default for HTTP bodies, JSON (RFC 8259 mandates it), Kafka string serialisers, Postgres databases (`UTF8` encoding) and, since Java 18 (JEP 400), `Charset.defaultCharset()`.
- **UTF-16**: 2 bytes for the BMP, 4 for the rest; has byte order (BE/LE) and an optional BOM. Used internally by Java, JavaScript and Windows APIs; rarely on the wire.
- **UTF-32**: fixed 4 bytes per code point; simple indexing, wasteful; basically unused for storage.
- Always say which charset you mean: `new String(bytes, StandardCharsets.UTF_8)`, `str.getBytes(UTF_8)`, `Files.readString(path, UTF_8)`, `Content-Type: application/json; charset=utf-8`. Before Java 18 the no-argument overloads used the OS locale, which differed between a dev laptop and a Docker image (`POSIX` → ASCII).

### Normalization and comparison
- The same text can have several code point sequences: "é" as U+00E9 (precomposed, NFC) or `e` + U+0301 (decomposed, NFD). `equals` says they differ; a unique index on `email` or `username` lets both in.
- `java.text.Normalizer.normalize(s, Normalizer.Form.NFC)` before storing and comparing. NFKC additionally folds compatibility variants (full-width digits, ligatures); use it for identifiers and search keys, not for displayed text.
- Case: `toLowerCase(Locale.ROOT)` for technical comparisons. The Turkish dotless i and the German ß (`"ß".toUpperCase()` is `"SS"`) show why locale and length can change. Collation (sort order) is a database setting; Postgres `ORDER BY name COLLATE "und-x-icu"` versus `"C"` gives different results and affects index usability ([[postgres-indexes]]).
- Security: homoglyphs (Cyrillic "а" vs Latin "a") and invisible characters (zero-width space, bidi overrides) pass validation and show up in phishing-style usernames; validate against an explicit allowed set, not "any letter".

### Where it goes wrong in a backend
- Length limits: `varchar(50)` in Postgres counts characters, but an SMS segment counts GSM-7 septets or UCS-2 units, and a Kafka message or an `S3` key counts bytes. Validate in the unit the limit is defined in.
- `Content-Length` is bytes of the encoded body, never `string.length()`.
- URL encoding, Base64 and JSON escaping are transformations on *bytes* or *code points*, not on chars; `URLEncoder.encode(s, UTF_8)` requires the charset for a reason.
- Logs and Sentry events with mojibake usually mean one hop decoded with the wrong charset; fix the source, do not "re-encode" downstream.

## Example
What the counts look like, and the normalization trap on a user identifier.
```java
import java.nio.charset.StandardCharsets;
import java.text.Normalizer;

public class UnicodeDemo {
    public static void main(String[] args) {
        String s = "e\u0301\uD83D\uDE00";            // "é" (decomposed) + a smiley emoji
        System.out.println(s.length());             // 4  UTF-16 code units
        System.out.println(s.codePointCount(0, s.length())); // 3 code points
        System.out.println(s.getBytes(StandardCharsets.UTF_8).length);  // 7 bytes
        System.out.println(s.getBytes(StandardCharsets.UTF_16).length); // 10 (BOM + 8)

        String nfc = "\u00E9";                      // precomposed é
        String nfd = "e\u0301";                     // e + combining acute
        System.out.println(nfc.equals(nfd));        // false
        System.out.println(Normalizer.normalize(nfc, Normalizer.Form.NFC)
                .equals(Normalizer.normalize(nfd, Normalizer.Form.NFC))); // true

        // Safe iteration over real characters
        s.codePoints().forEach(cp ->
                System.out.printf("U+%04X %s%n", cp, Character.getName(cp)));

        // Legacy interface: a Cyrillic city name in Windows-1251 decoded as UTF-8 -> mojibake
        byte[] cp1251 = "Бишкек".getBytes(java.nio.charset.Charset.forName("windows-1251"));
        System.out.println(new String(cp1251, StandardCharsets.UTF_8)); // ������
    }
}
```
For a username column: `Normalizer.normalize(input, NFC).toLowerCase(Locale.ROOT)` before validation, uniqueness check and storage.

## Pitfalls
- `new String(bytes)` / `getBytes()` without a charset in code that must run on Java 17 or older: works on the laptop, corrupts text in a container with `LANG=C`. Always pass `UTF_8`.
- Truncating a string to fit a byte limit by `substring(0, n)`: n chars can be up to 4n bytes, and the cut may split a surrogate pair. Encode first, then cut at a code point boundary.
- Regex `.` and `\w` operate on code units by default; `[a-zA-Z]` rejects every non-English name. Use `\p{L}` and `Pattern.UNICODE_CHARACTER_CLASS` when the intent is "letters".
- Storing a database or Kafka topic in a non-UTF-8 encoding "because legacy": every consumer must know, and JSON libraries assume UTF-8. Convert at the boundary adapter once.
- Related: [[numbers-bits-and-floating-point]] for the other representation trap (bytes vs values), [[strings-and-string-pool]] for `String` immutability, interning and compact strings in the JVM.
