---
title: Numbers, bits and floating point
tags: [numbers, floating-point, ieee-754, bigdecimal, fundamentals]
learned: 2026-10-27
---
## TL;DR
Integers are stored in **two's complement**, which is why `Integer.MAX_VALUE + 1` wraps to a negative number without any error. Fractions are stored as IEEE 754 **binary floating point**: a sign, an exponent and a 52-bit (double) or 23-bit (float) mantissa. Most decimal fractions (0.1, 0.2, 0.3) have no exact binary representation, so `0.1 + 0.2 == 0.30000000000000004`. For money, quantities and anything a human will reconcile, use `BigDecimal` in Java and `numeric` in Postgres; `double` is for measurements, geometry and statistics where a relative error of 1e-16 is fine. The Java type sizes and boxing rules are in [[primitives-and-wrappers]].

## Key points
### Integers
- An *n*-bit two's complement integer covers −2^(n−1) … 2^(n−1)−1. Negating is "invert the bits and add one"; the top bit is the sign. `int` is 32-bit: −2 147 483 648 … 2 147 483 647; `long` is 64-bit.
- Overflow is silent and wraps: `Integer.MAX_VALUE + 1 == Integer.MIN_VALUE`, and `Math.abs(Integer.MIN_VALUE)` is still negative. `Math.addExact`/`multiplyExact` throw `ArithmeticException` instead; use them for counters that must not wrap (balances, quotas).
- Division truncates toward zero; `%` keeps the sign of the dividend, so `-7 % 3 == -1`. `Math.floorMod` gives the non-negative result you usually want for ring-buffer and hash-bucket indices ([[hash-tables]]).
- Classic overflow bug: `(low + high) / 2` in [[binary-search-and-variants|binary search]] overflows for large arrays; write `low + (high - low) / 2` or `(low + high) >>> 1`.
- Java has no unsigned primitives, but `Integer.toUnsignedString`, `Integer.compareUnsigned`, `Long.divideUnsigned` and `>>>` cover the cases (parsing binary protocols, EMV byte fields, hashes). Bit tricks are in [[bit-manipulation]].
- Byte order matters when integers cross a wire: network order is big-endian, x86 is little-endian; `ByteBuffer.order(...)` and `DataInputStream` (always big-endian) handle it ([[java-nio]]).

### IEEE 754 floating point
| Type | Bits (sign/exp/mantissa) | Decimal digits | Range |
|---|---|---|---|
| `float` | 1 / 8 / 23 | ~7 | ±3.4e38 |
| `double` | 1 / 11 / 52 | ~15–16 | ±1.8e308 |

- A value is ±1.mantissa × 2^(exponent−bias). Only fractions whose denominator is a power of two (0.5, 0.25, 0.375) are exact; 0.1 is a repeating binary fraction that gets rounded at the 53rd bit. Adding two rounded numbers accumulates the error, hence `0.1 + 0.2 != 0.3`.
- Special values: `+0.0` and `-0.0` (equal under `==`, different under `Double.compare`), `Infinity` from overflow or `1.0 / 0`, `NaN` from `0.0 / 0` or `sqrt(-1)`. `NaN != NaN`, so `Double.isNaN(x)` is the only test; `Double.equals` and `compareTo` treat NaN as equal to itself for sorting.
- Precision is relative: doubles near 1 are spaced ~2e-16 apart, near 1e16 they are spaced 2 apart, so `1e16 + 1 == 1e16`. Summing a million small values into one big accumulator loses them (use Kahan summation or sort ascending).
- Comparing: never `a == b` after arithmetic; compare `Math.abs(a - b) <= eps * Math.max(Math.abs(a), Math.abs(b))`.
- `float` is 4 bytes and rarely worth it in backend code except for large arrays (ML embeddings, graphics); `double` is the default and is as fast on modern CPUs.

### Decimal types for money
- `BigDecimal` stores an unscaled `BigInteger` plus a scale: `new BigDecimal("19.99")` is exactly 1999 × 10^-2. Construct from a `String` or `BigDecimal.valueOf(double)`; `new BigDecimal(0.1)` captures the binary garbage (`0.1000000000000000055511151231257827...`).
- `equals` compares scale too: `new BigDecimal("2.0").equals(new BigDecimal("2.00"))` is `false`; use `compareTo` for value equality and strip or fix the scale before storing.
- Division needs an explicit `MathContext` or scale and `RoundingMode`: `price.divide(BigDecimal.valueOf(3), 2, RoundingMode.HALF_EVEN)`; without it a non-terminating result throws `ArithmeticException`. Banker's rounding (`HALF_EVEN`) is the usual accounting default; VAT rules may prescribe `HALF_UP`.
- Alternative: store minor units as `long` (tiyin, cents) and convert at the edges. Fast and simple when there is one currency and no fractional cents; breaks for FX rates and percentage fees. Both approaches, with the DB side, are in [[time-money-and-ids]].
- Postgres: `numeric(19,4)` is exact and arbitrary precision; `real`/`double precision` are IEEE 754; `money` is locale-dependent and best avoided.

## Example
The same tariff calculation with `double` and with `BigDecimal`.
```java
public class TariffDemo {
    public static void main(String[] args) {
        // Three top-ups of 0.10 KGS plus a 0.20 bonus: should be exactly 0.50
        double d = 0.1 + 0.1 + 0.1 + 0.2;
        System.out.println(d);                 // 0.5000000000000001
        System.out.println(d == 0.5);          // false

        BigDecimal b = new BigDecimal("0.10")
                .add(new BigDecimal("0.10"))
                .add(new BigDecimal("0.10"))
                .add(new BigDecimal("0.20"));
        System.out.println(b);                 // 0.50
        System.out.println(b.compareTo(new BigDecimal("0.5")) == 0); // true

        // 12% VAT on 199.99, rounded to the nearest tiyin
        BigDecimal vat = new BigDecimal("199.99")
                .multiply(new BigDecimal("0.12"))
                .setScale(2, RoundingMode.HALF_EVEN);
        System.out.println(vat);               // 24.00  (23.9988 rounded)

        // Silent integer overflow vs exact arithmetic
        int subscribers = Integer.MAX_VALUE;
        System.out.println(subscribers + 1);               // -2147483648
        System.out.println(Math.addExact(subscribers, 1)); // ArithmeticException
    }
}
```

## Pitfalls
- `double` columns or fields for prices, balances and quantities: totals drift by a cent, reconciliation fails, and the bug surfaces months later in finance. Use `BigDecimal` ↔ `numeric` end to end, including JSON (serialise as a string or a decimal, never as a JS `number` beyond 2^53).
- `BigDecimal` in `HashMap` keys or `equals` checks with mismatched scale; normalise with `setScale` or compare with `compareTo`.
- `int` counters for message offsets, byte counts or epoch millis: `System.currentTimeMillis()` already needs a `long`; Kafka offsets and file sizes do too.
- Rounding at every intermediate step instead of once at the end, or rounding with `HALF_UP` in one service and `HALF_EVEN` in another; agree on a single rounding policy per calculation.
- `float` for anything parsed from user input: 7 significant digits are gone after a few operations.
- Related: [[primitives-and-wrappers]] for widening, boxing and `Integer` cache traps; [[text-encodings-and-unicode]] for the other place where "a number of bytes" and "a number of characters" differ.
