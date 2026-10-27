---
title: Ограничение частоты запросов — token bucket, leaky bucket, скользящее окно, распределённые лимиты в Redis
tags: [system-design, rate-limiting, redis, resilience]
learned: 2026-10-27
---
## Кратко
Ограничитель частоты запросов (rate limiter) защищает ресурс от большего числа запросов, чем он может или должен обработать: твой API — от злоупотребляющего клиента, внешнего провайдера — от твоих же retry, тенанта — от соседей. Четыре классических алгоритма различаются отношением к всплескам: **token bucket** допускает всплеск до своей ёмкости, а затем равномерную скорость, **leaky bucket** сглаживает выход до постоянной скорости, счётчик **фиксированного окна (fixed window)** дешёвый, но на границе окон пропускает вдвое больше, а **скользящее окно (sliding window)** (лог или взвешенный счётчик) точно и без проблемы границы. При нескольких pod API счётчики живут в Redis и обновляются атомарно Lua-скриптом; клиент получает `429` и `Retry-After`. Полный дизайн скользящего окна на Redis — в кейсе [[design-rate-limiter]].

## Основное

### Где живут лимиты
| Место | Что ограничивает | Типичный ключ |
|---|---|---|
| Edge / API gateway | злоупотребления, DDoS, квоты на API-ключ | IP клиента, API-ключ, id пользователя ([[api-gateway-and-service-discovery]], [[cdn]]) |
| Сервис | дорогие эндпоинты, справедливость между тенантами | id тенанта + эндпоинт ([[multi-tenancy-strategies]]) |
| Исходящий клиент | вызовы провайдера с лимитом по договору (SMS-шлюз 100 сообщений/с) | имя провайдера |
| Потребитель / воркер | скорость разбора очереди, чтобы не завалить БД | группа воркеров ([[backpressure-and-flow-control]]) |

Ограничитель — это **политика**: он отклоняет рано и дёшево. Это не [[circuit-breaker]] (который реагирует на сбои) и не сброс нагрузки (load shedding) (который отклоняет по текущей ёмкости), хотя все три встречаются в одном слое отказоустойчивости.

### Алгоритмы
| Алгоритм | Состояние на ключ | Всплеск | Точность | Заметки |
|---|---|---|---|---|
| Token bucket | токены, время последнего пополнения (2 числа) | до ёмкости, затем `rate` | точный | стандарт индустрии (AWS, Envoy, Guava `RateLimiter`, Bucket4j); пополнение лениво при каждом запросе |
| Leaky bucket (как счётчик) | как у token bucket | нет: постоянный выход | точный | та же математика, увиденная с другой стороны; вариант «как очередь» задерживает запросы вместо отклонения |
| Фиксированное окно | один счётчик на окно | 2× на границе | низкая | `INCR key:minute` + `EXPIRE`; самый дешёвый |
| Лог скользящего окна | sorted set меток времени | нет | точный | память ∝ лимиту; `ZADD`/`ZREMRANGEBYSCORE`/`ZCARD` |
| Счётчик скользящего окна | два счётчика (текущее, предыдущее окно) | небольшой | ошибка ~1 % | `prev × (1 − elapsed/window) + current`; практичный компромисс |

- Математика token bucket: `tokens = min(capacity, tokens + (now − last) × rate)`; пропустить, если `tokens ≥ cost`, затем `tokens −= cost`. Ёмкость — это всплеск, rate — устойчивый лимит; `cost` позволяет дорогим вызовам расходовать больше.
- Лимит — это пара «N за окно» плюс решение, что делать с избытком: отклонить (`429`), задержать (очередь) или деградировать (отдать закэшированный ответ).

### Распределённые лимиты
- Лимиты в памяти каждого pod умножаются на число pod и сбрасываются при deploy; годятся как грубая страховка, но не для лимита по договору.
- Общее состояние в Redis: одно обращение на решение (~0,5 мс), атомарность через Lua-скрипт или `MULTI`, ключи с TTL, чтобы неактивные клиенты ничего не стоили ([[redis-data-structures]]). Redis Cluster шардирует ключи, поэтому один горячий ключ всё равно попадает на один узел; при 100 K решений/с на ключ переходи на локальные token bucket, пополняемые из центральной квоты («аренда токенов»: каждый pod берёт в аренду 100 токенов/с).
- Часы: используй Redis `TIME` внутри скрипта, а не часы каждого pod, чтобы pod с расходящимися часами были согласны ([[time-and-clocks]]).
- Fail-open или fail-closed, когда Redis недоступен: на edge публичного API обычно fail-open с локальным запасным лимитом; квота провайдера — fail-closed (лучше задержать SMS, чем получить блокировку аккаунта).

### Ответы и заголовки
- `429 Too Many Requests` с `Retry-After: <seconds>` и информативными заголовками (`RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` — имена из черновика IETF), чтобы клиенты могли разумно отступать.
- Клиенты: экспоненциальный backoff с джиттером, соблюдение `Retry-After`, никогда не повторять `429` сразу ([[failure-modes-and-timeouts]]).
- Логируй и считай отказы по ключу: всплеск `429` у одного тенанта — это либо злоупотребление, либо сломанный клиент, и то и другое стоит алерта ([[slo-sli-and-alerting]]).

### Выбор
- Публичный API на клиента: token bucket (дружелюбен к всплескам) в Redis, лимиты по тарифному плану.
- Защита на пользователя («максимум 5 SMS в час», перебор OTP): счётчик скользящего окна, строго.
- Лимит исходящих вызовов провайдера: token bucket на провайдера, общий для всех воркеров, fail-closed, а то, что bucket отклоняет, поглощает очередь ([[message-queues-and-streams]]).
- Справедливость между тенантами: взвешенная справедливая очередь (weighted fair queueing) или bucket на тенанта плюс глобальный.

## Пример
Token bucket, общий для всех pod, хранится в Redis hash и атомарно обновляется Lua-скриптом; используется и в фильтре публичного API, и как защита перед вызовом SMS-провайдера.

```java
@Component
public class RedisTokenBucket {
    // KEYS[1] = ключ bucket; ARGV = capacity, refillPerSec, cost, ttlSec. Используется Redis TIME, а не часы pod.
    private static final String LUA = """
        local capacity = tonumber(ARGV[1]); local rate = tonumber(ARGV[2])
        local cost = tonumber(ARGV[3]);     local ttl  = tonumber(ARGV[4])
        local t = redis.call('TIME'); local now = t[1] + t[2] / 1000000
        local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
        local tokens = tonumber(b[1]); local ts = tonumber(b[2])
        if tokens == nil then tokens = capacity; ts = now end
        tokens = math.min(capacity, tokens + (now - ts) * rate)
        local allowed = 0; local retryAfter = 0
        if tokens >= cost then tokens = tokens - cost; allowed = 1
        else retryAfter = math.ceil((cost - tokens) / rate) end
        redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
        redis.call('EXPIRE', KEYS[1], ttl)
        return { allowed, retryAfter, math.floor(tokens) }
        """;
    private final RedisScript<List> script = RedisScript.of(LUA, List.class);
    private final StringRedisTemplate redis;

    public RedisTokenBucket(StringRedisTemplate redis) { this.redis = redis; }

    public record Decision(boolean allowed, long retryAfterSec, long remaining) {}

    @SuppressWarnings("unchecked")
    public Decision tryConsume(String key, long capacity, double refillPerSec, long cost) {
        long ttl = (long) Math.ceil(capacity / refillPerSec) + 1;            // неактивный bucket снова полон через ttl
        List<Long> r = redis.execute(script, List.of(key),
                String.valueOf(capacity), String.valueOf(refillPerSec), String.valueOf(cost), String.valueOf(ttl));
        return new Decision(r.get(0) == 1, r.get(1), r.get(2));
    }
}

@Component
@Order(1)
public class ApiRateLimitFilter extends OncePerRequestFilter {
    private final RedisTokenBucket bucket;
    public ApiRateLimitFilter(RedisTokenBucket bucket) { this.bucket = bucket; }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        String client = Optional.ofNullable(req.getHeader("X-Api-Key")).orElse(req.getRemoteAddr());
        RedisTokenBucket.Decision d;
        try {
            d = bucket.tryConsume("rl:api:" + client, 100, 50.0, 1);          // всплеск 100, устойчиво 50 rps
        } catch (DataAccessException redisDown) {
            d = new RedisTokenBucket.Decision(true, 0, -1);                     // fail-open на публичном edge
        }
        res.setHeader("RateLimit-Limit", "50");
        res.setHeader("RateLimit-Remaining", String.valueOf(Math.max(d.remaining(), 0)));
        if (!d.allowed()) {
            res.setHeader("Retry-After", String.valueOf(d.retryAfterSec()));
            res.sendError(HttpStatus.TOO_MANY_REQUESTS.value());
            return;
        }
        chain.doFilter(req, res);
    }
}

// защита исходящих вызовов в SMS-воркере: fail-closed, Kafka consumer ставит паузу вместо вызова провайдера
// var d = bucket.tryConsume("rl:provider:sms-gw", 100, 100.0, 1); if (!d.allowed()) pauseFor(d.retryAfterSec());
```

Одно обращение к Redis на запрос, два числа состояния на клиента, и все pod согласны в подсчёте.

## Подводные камни
- Фиксированное окно на границе минуты: 100 запросов в 12:00:59 и 100 в 12:01:00 — «в пределах лимита», но на бэкенд за одну секунду приходит 200.
- Ограничение только по IP клиента: NAT мобильного оператора прячет 10 000 пользователей за одним IP; после аутентификации предпочитай API-ключ или id пользователя, IP — только для анонимного трафика.
- Неатомарные `GET`, затем `SET` в Redis: два pod читают 99, оба пропускают, счётчик заканчивается на 100, а прошёл 101 запрос. Используй Lua или `INCR`.
- Немедленный retry `429` с клиента или с прокси: ограничитель теперь тратит свою ёмкость на отказы. Соблюдай `Retry-After` с джиттером.
- Ограничитель как единственная защита квоты провайдера: ограничитель отклоняет, воркер повторяет, очередь растёт без предела. Дополняй алертами на глубину очереди и сбросом по приоритету ([[back-of-envelope-estimation]] помогает оценить объём отставания).
- Связанные заметки: [[design-rate-limiter]] — полный дизайн скользящего окна на Redis; [[circuit-breaker]] — родственный механизм, реагирующий на сбои.
