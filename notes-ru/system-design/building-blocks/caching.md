---
title: Стратегии кэширования — cache-aside, read/write-through, write-behind, TTL, инвалидация, stampede
tags: [system-design, caching, redis, performance]
learned: 2026-10-27
---
## Кратко
Кэш обменивает свежесть данных на задержку и нагрузку на базу: чтение из памяти стоит 0,1–1 мс вместо 1–10 мс и ничего не стоит базе. Стратегия отвечает на три вопроса: кто заполняет кэш (приложение в **cache-aside**, сам кэш в **read-through**), когда записи доходят до хранилища (**write-through** — синхронно, **write-behind** — позже) и как устаревшие данные покидают кэш (**TTL**, явная **инвалидация** или версионированные ключи). Типичные отказы — устаревшие чтения, **лавина запросов (stampede)** при истечении горячего ключа и горячие ключи, перегружающие один узел. Команды Redis описаны в [[redis-data-structures]] и [[redis-patterns-and-persistence]], аннотации Spring — в [[spring-cache-abstraction]]; эта заметка отвечает за стратегии.

## Основное

### Где находится кэш
| Уровень | Задержка | Общий для pod | Типичное содержимое |
|---|---|---|---|
| Клиент / браузер, CDN | 0 (локально) / RTT до edge | нет / да | статические ресурсы, публичные GET ([[http-caching]], [[cdn]]) |
| Внутри процесса (Caffeine) | ~100 нс | нет, у каждого pod своя копия | конфигурация, шаблоны, feature flags, горячие справочные данные |
| Распределённый (Redis) | 0,2–1 мс | да | сессии, пользовательские настройки, вычисленные представления, счётчики |
| Буферный кэш БД | ~10 мкс | н/п | страницы; уже есть, сначала настрой `shared_buffers` |

Два уровня (Caffeine перед Redis) дают скорость первого и согласованность второго; уровню внутри процесса нужен короткий TTL или канал инвалидации через pub/sub ([[distributed-cache-coherence]]).

### Стратегии заполнения и записи
| Стратегия | Путь чтения | Путь записи | Согласованность | Когда использовать |
|---|---|---|---|---|
| Cache-aside (ленивое) | приложение: промах кэша → БД → `SET` с TTL | приложение пишет в БД, затем `DEL` ключа | окно устаревания между записью и удалением; просто | по умолчанию для данных, которые в основном читаются |
| Read-through | кэш сам загружает из БД при промахе (функция-загрузчик) | как в aside | такая же | кэши под управлением библиотеки (Caffeine `LoadingCache`, Spring `@Cacheable`) |
| Write-through | попадание | приложение синхронно пишет в кэш и в БД | кэш всегда свежий; записи медленнее; кэшируются данные, которые никто не читает | чтение после записи должно быть точным |
| Write-behind (write-back) | попадание | приложение пишет в кэш, кэш асинхронно сбрасывает в БД | самые быстрые записи; **потеря данных, если кэш умрёт** до сброса | счётчики, статистика просмотров, где потеря допустима |
| Refresh-ahead | попадание; обновление до истечения TTL, если ключ недавно использовался | н/п | скрывает задержку промаха для горячих ключей | популярные ключи с дорогой загрузкой |

- В cache-aside **при записи удаляй, а не обновляй**: два одновременных писателя, обновляющих кэш, могут оставить последним более старое значение; удаление позволяет следующему чтению заполнить кэш заново. Удаляй *после* commit, иначе параллельный читатель может заново положить старое значение из ещё не закоммиченного снимка.
- Порядок «запись в БД, затем удаление из кэша» всё равно содержит гонку (промах при чтении → чтение старого из БД → commit записи → удаление → чтение кладёт старое значение). Окно маленькое, обычно его принимают; устранить можно коротким TTL или версией в ключе.

### Истечение и инвалидация
- **TTL всегда**, даже при явной инвалидации: он ограничивает, насколько неверным может стать значение при пропущенной инвалидации. Выбирай по допустимости: 30 с для балансов, 10 мин для шаблонов, 24 ч для неизменяемых блобов.
- **Добавляй джиттер к TTL** (`ttl + random(0..10 %)`), чтобы ключи, загруженные вместе, не истекали одновременно.
- **Версионированные ключи** (`prefs:v2:{userId}` или `product:{id}:{updatedAt}`) делают инвалидацию неявной: новая версия — это новый ключ, старые истекают. Хорошо для кэширования объектов целиком; версия должна быть дешёвой для получения.
- **Инвалидация по событиям**: событие `entity.updated` ([[outbox-pattern]], [[domain-events-and-integration-events]]) запускает `DEL` в каждом потребителе, который кэширует эту сущность; единственный способ держать согласованными кэши многих сервисов.
- Отрицательное кэширование (negative caching): кратко (10–60 с) кэшировать «не найдено», чтобы поток запросов по отсутствующим id не доходил до БД; в значении кэша различай `null` и «отсутствует».

### Stampede, горячие ключи, вытеснение
- **Stampede / dog-pile**: горячий ключ истекает, и 1 000 одновременных запросов все промахиваются и все идут в БД. Решения от дешёвых к сильным: схлопывание запросов внутри процесса (один загрузчик на ключ, `ConcurrentHashMap.computeIfAbsent` или Caffeine), короткоживущая распределённая блокировка в Redis (`SET lock:key NX PX 2000`), чтобы загружал один pod, а остальные ждали или отдавали устаревшее значение, refresh-ahead или отдача устаревшего значения во время перепроверки (stale-while-revalidate) ([[distributed-locks-and-fencing]]).
- **Горячий ключ (hot key)**: один ключ (знаменитость, глобальная конфигурация) получает 100 K rps и попадает на один узел Redis. Реплицируй его в уровень внутри процесса или шардируй (`key:{0..15}`: при чтении выбирается случайный, при обновлении пишутся все).
- **Вытеснение (eviction)**: когда память заполнена, Redis вытесняет ключи по политике (`allkeys-lru`, `allkeys-lfu`, `volatile-ttl`); рассчитывай объём памяти по горячему множеству ([[back-of-envelope-estimation]]) и следи за hit ratio, а не только за памятью. Сам алгоритм LRU — в [[lru-cache-design]].
- **Пробивание (penetration)**: запросы к ключам, которых никогда не существует (случайные id от атакующего), всегда промахиваются; помогает отрицательное кэширование или [[bloom-filters-and-probabilistic|фильтр Блума]] перед кэшем.

### Что измерять
Hit ratio по каждому кэшу, p99 промаха и попадания, QPS базы до и после, вытеснения в секунду. Кэш с hit ratio 60 % на дешёвых запросах может стоить больше, чем экономит; hit ratio 99 % на дорогих запросах держит на себе всю систему ([[grafana-dashboards-and-red-use]]).

## Пример
Cache-aside для настроек абонента с защитой от stampede: схлопывание внутри процесса по ключу, блокировка в Redis, чтобы отсутствующий ключ загружал только один pod, и запасной вариант на время чужой блокировки. Spring `@Cacheable` намеренно не используется, чтобы показать механику.

```java
@Service
public class SubscriberPrefsCache {
    private static final Duration TTL = Duration.ofMinutes(10);
    private static final Duration LOCK_TTL = Duration.ofSeconds(3);

    private final StringRedisTemplate redis;
    private final SubscriberPrefsRepository repo;
    private final ObjectMapper json;
    private final ConcurrentHashMap<String, CompletableFuture<Prefs>> inFlight = new ConcurrentHashMap<>();

    public Prefs get(long subscriberId) {
        String key = "prefs:v1:" + subscriberId;
        String cached = redis.opsForValue().get(key);
        if (cached != null) return decode(cached);

        // 1. Схлопываем одновременные промахи внутри этого pod: один загрузчик на ключ.
        CompletableFuture<Prefs> f = inFlight.computeIfAbsent(key, k -> CompletableFuture.supplyAsync(() -> loadWithLock(k, subscriberId)));
        try {
            return f.join();
        } finally {
            inFlight.remove(key, f);
        }
    }

    private Prefs loadWithLock(String key, long subscriberId) {
        // 2. Один загрузчик на все pod: SET NX с коротким TTL. Fencing token не нужен, блокировка лишь
        //    снижает нагрузку на БД; корректность от неё не зависит.
        Boolean locked = redis.opsForValue().setIfAbsent(key + ":lock", "1", LOCK_TTL);
        if (Boolean.TRUE.equals(locked)) {
            try {
                Prefs prefs = repo.findById(subscriberId).orElse(Prefs.defaults());
                long jitter = ThreadLocalRandom.current().nextLong(0, TTL.toSeconds() / 10);   // 0..60 с
                redis.opsForValue().set(key, encode(prefs), TTL.plusSeconds(jitter));
                return prefs;
            } finally {
                redis.delete(key + ":lock");
            }
        }
        // 3. Загружает кто-то другой: недолго ждём значение, затем один раз идём в БД.
        for (int i = 0; i < 20; i++) {
            LockSupport.parkNanos(Duration.ofMillis(50).toNanos());
            String v = redis.opsForValue().get(key);
            if (v != null) return decode(v);
        }
        return repo.findById(subscriberId).orElse(Prefs.defaults());
    }

    @Transactional
    public void update(long subscriberId, Prefs prefs) {
        repo.save(subscriberId, prefs);
        // 4. Удаляем, а не обновляем, и только после commit, чтобы читатель не закэшировал снимок до commit.
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override public void afterCommit() { redis.delete("prefs:v1:" + subscriberId); }
        });
    }

    private Prefs decode(String s) { try { return json.readValue(s, Prefs.class); } catch (IOException e) { throw new UncheckedIOException(e); } }
    private String encode(Prefs p) { try { return json.writeValueAsString(p); } catch (IOException e) { throw new UncheckedIOException(e); } }
}
```

При лавине из 1 000 запросов на один ключ этот код делает не больше одного запроса в БД на pod, обычно один на весь кластер, вместо 1 000.

## Подводные камни
- Нет TTL, «потому что мы инвалидируем»: одно пропущенное событие (потребитель лежит, баг) — и ключ неверен навсегда.
- Кэширование до измерений: Redis перед запросом, который выполняется 1 мс и 10 раз в секунду, добавляет эксплуатационную зависимость впустую (сначала [[postgres-performance-checklist]]).
- Обновление кэша внутри транзакции: если транзакция откатится, в кэше останется значение, которого никогда не было; к тому же параллельный читатель может сохранить значение до commit.
- Сериализованные объекты с изменившимся классом: deploy, меняющий DTO, делает все закэшированные значения недесериализуемыми; версионируй ключ или полезную нагрузку.
- Redis как источник истины с write-behind и без персистентности: перезапуск теряет счётчики ([[redis-patterns-and-persistence]]).
- Связанные заметки: [[distributed-cache-coherence]] — инвалидация между сервисами; [[hibernate-caching]] — кэш второго уровня ORM и почему он редко нужен.
