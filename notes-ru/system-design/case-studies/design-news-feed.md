---
title: Проектирование ленты новостей
tags: [system-design, case-study, fan-out, caching, ranking]
learned: 2026-10-27
---
## Кратко
Лента новостей показывает каждому пользователю список свежих постов от тех, на кого он подписан, упорядоченных по времени или по оценке ранжирования, за несколько сотен миллисекунд. Главное решение — **рассылка при записи (fan-out on write)** (когда пост опубликован, его id кладётся в заранее вычисленную ленту каждого подписчика в кеше) против **рассылки при чтении (fan-out on read)** (когда пользователь открывает ленту, забираются свежие посты всех, на кого он подписан, и сливаются). Реальные системы делают и то и другое: push для обычных пользователей, pull для знаменитостей с миллионами подписчиков и слияние в момент чтения. Всё остальное — это [[caching]], [[cdn]] для медиа и асинхронный конвейер ([[message-queues-and-streams]]), который выполняет рассылку вне запроса.

## Основное

### Требования и оценки
- Функциональные: опубликовать пост (текст, медиа), подписаться/отписаться, читать ленту с бесконечной прокруткой, счётчики лайков и комментариев.
- Нефункциональные: загрузка ленты < 200 мс p99, посты появляются у большинства подписчиков за секунды, лента должна быть доступна даже при падении ранжирования (откат к хронологическому порядку).
- Числа: 10 M DAU, каждый открывает ленту 10 раз в день → 100 M чтений ленты в день ≈ 1 200/с, пик 5 000/с. 1 M постов в день ≈ 12/с; в среднем 200 подписчиков → 200 M вставок в ленты в день ≈ 2 300/с записей в кеш в среднем. Знаменитость с 5 M подписчиков обошлась бы в 5 M записей на один пост — поэтому и существует гибридная рассылка. Кеш лент: 10 M пользователей × 500 id постов × 16 B ≈ 80 GB в Redis, с шардированием.

### API
```
POST /v1/posts                        { text, mediaIds }        -> 201 { postId }
GET  /v1/feed?cursor=<opaque>&limit=20 -> { items: [ {postId, author, text, media, createdAt, score} ], nextCursor }
POST /v1/users/{id}/follow
GET  /v1/users/{id}/posts?cursor=     (profile timeline, separate from the home feed)
```
- Курсорная пагинация по `(score, postId)` или `(createdAt, postId)`; пагинация через offset ломается, когда приходят новые посты ([[api-design-for-system-design]]).

### Модель данных
- `post(id, author_id, text, media jsonb, created_at)` — Postgres, партиционирование по месяцам; индекс `(author_id, created_at desc)` для лент профилей.
- `follow(follower_id, followee_id)` — рёбра графа; нужны оба направления («на кого я подписан» для pull, «кто подписан на меня» для push). Храним дважды или в хранилище, удобном для графов ([[graphs-representation]]).
- `feed:{userId}` — sorted set в Redis из `postId` с оценкой по времени (или по ранжированию), обрезанный до последних 500–1 000 элементов ([[redis-data-structures]]).
- `post_cache:{postId}` — hash в Redis с отрендеренным постом, TTL — часы, чтобы чтение ленты для горячих постов вообще не обращалось к Postgres.
- Счётчики (лайки, комментарии) — в Redis с периодическим сбросом в Postgres.

- Разбор схемы базы (из упражнения курса про Twitter): `profile(id, name, followers_count, following_count)`, `follows(source_id, target_id)` с индексами в обе стороны, `tweet(id, profile_id, content, created_at)`, секционированная по времени, `likes(tweet_id, profile_id)`. Счётчики — это **хранимые колонки**, обновляемые асинхронно, никогда не `count(*)` по миллиардам строк подписок; домашняя лента вычисляется заранее, а не соединением подписок × твитов во время чтения.

### Общая архитектура
```
POST /posts ──▶ post service ──tx: post + outbox──▶ Postgres ──relay──▶ Kafka post.created
                                                                           │
                                               fan-out workers: followers = get(author)
                                                 if followers < 10k: ZADD feed:{f} for each (push)
                                                 else: mark author as "pull source"; skip push
GET /feed ──▶ feed service ──▶ ZREVRANGE feed:{me} (pushed ids)
                           ├─▶ for each celebrity I follow: recent post ids (cached per celebrity)
                           ├─▶ merge, rank, dedupe, cut to 20
                           └─▶ hydrate posts from post cache (MGET) → fallback Postgres → CDN URLs for media
```

### Подробно: стратегия рассылки
| | Рассылка при записи (push) | Рассылка при чтении (pull) | Гибрид |
|---|---|---|---|
| Стоимость публикации | O(подписчиков) записей в кеш | O(1) | O(подписчиков) только для обычных пользователей |
| Стоимость чтения | O(1): прочитать один sorted set | O(подписок) запросов + слияние | чтение одного set + k pull-ов для знаменитостей |
| Задержка появления | секунды (асинхронно) | мгновенно | смешанная |
| Лишняя работа | ленты строятся для тех, кто никогда не заходит | пересчёт при каждом открытии | ограничена |
| Проблема знаменитостей | 5 M записей на пост, минуты отставания | нет | решена |

- Push-конвейер: consumer `post.created` загружает id подписчиков страницами (`SELECT follower_id ... WHERE followee_id = ? AND follower_id > ? LIMIT 10000`), отправляет в Redis пачками (pipeline) `ZADD` + `ZREMRANGEBYRANK` (обрезка) и идемпотентен, потому что `ZADD` того же элемента ничего не меняет ([[idempotency]], [[async-processing-and-event-driven-scaling]]).
- Неактивные пользователи: не делаем push тем, кто не заходил 30 дней; их ленту пересобираем через pull при следующем входе. Это сокращает объём рассылки вдвое и больше.
- Отписка и удаление: лениво; фильтруем при чтении (`author not in blocked`, пост есть в кеше), а не сканируем миллионы set-ов.
- Ранжирование: хронологический порядок — это оценка по `createdAt`; ранжированная лента хранит оценку модели, вычисленную в момент рассылки, и при чтении переранжирует топ-100 кандидатов со свежими сигналами. Хронологический путь остаётся запасным, когда ранжировщик тормозит ([[circuit-breaker]]).
- Согласованность: собственная лента автора и его профиль читаются с primary, поэтому ситуации «я запостил, где пост?» не бывает; ленты всех остальных согласованы в конечном счёте (eventual consistency) ([[read-write-splitting-and-cqrs-lite]], [[consistency-models]]).

### Компромиссы
- Sorted set в Redis против заранее вычисленной таблицы `feed_item` в Postgres: Redis — для горячего окна, Postgres (или Cassandra) — когда ленты должны пережить потерю кеша без пересборки ([[storage-choices]]).
- Порог знаменитости (10 k, 100 k): чем ниже порог, тем больше pull-работы при чтении; чем выше, тем больше отставание рассылки; измерять нужно оба.
- Медиа — через [[cdn]] с подписанными URL; ответ ленты несёт URL, а не байты.

## Пример
Гибридный путь чтения: слить ленту из push с постами знаменитостей, полученными через pull, и заполнить данными из кеша постов.

```java
public FeedPage load(long userId, Cursor cursor) {
    // 1. id из push (большинство лент на этом заканчиваются)
    Set<ZSetOperations.TypedTuple<String>> pushed = redis.opsForZSet()
        .reverseRangeByScoreWithScores("feed:" + userId, 0, cursor.maxScore(), 0, 100);

    // 2. источники для pull: знаменитости, на которых подписан пользователь (короткий список, в кеше)
    List<Long> celebs = followCache.celebritiesFollowedBy(userId);
    Stream<Candidate> pulled = celebs.stream()
        .flatMap(c -> recentPostsCache.of(c, cursor.maxScore(), 20).stream());

    // 3. слить, убрать дубликаты, ранжировать, обрезать
    List<Candidate> top = Stream.concat(pushed.stream().map(Candidate::fromTuple), pulled)
        .distinct()
        .sorted(comparing(Candidate::score).reversed().thenComparing(Candidate::postId))
        .limit(20).toList();

    // 4. заполнить данными: один MGET, промахи идут в Postgres и дозаполняют кеш
    Map<Long, PostView> posts = postCache.getAll(top.stream().map(Candidate::postId).toList());
    return FeedPage.of(top, posts, Cursor.after(top.getLast()));
}
```

## Подводные камни
- На собеседовании спрашивают: «пользователь с 20 M подписчиков публикует пост — что происходит?» Pull для этого автора, push для остальных; «через сколько мои подписчики увидят пост?» — секунды, ограничено отставанием рассылки, с метрикой отставания и алертом.
- «Redis потерял шард» — ленты пересобираются по запросу через pull из графа подписок; эталонная копия поста лежит в Postgres; всплески стоимости пересборки нужно ограничивать ([[distributed-cache-coherence]]).
- Рассылка внутри POST-запроса вместо consumer-а: задержка публикации равна числу подписчиков.
- Пагинация через offset плюс живая лента: дубликаты и пропуски при прокрутке.
- Связанные заметки: [[design-chat-service]] — парный случай рассылки в маленькие группы, [[database-scaling-path]] — таблица подписок в масштабе, [[system-design-basics]].
