---
title: Устройство LRU-кэша
tags: [data-structures, cache, lru, linked-hash-map, eviction]
learned: 2026-10-27
---
## Кратко
LRU-кэш (least recently used, «давно не использовавшийся») хранит не более N записей и вытесняет ту, к которой обращались раньше всех. Устройство с O(1) — это **хеш-map для поиска плюс двусвязный список для порядка давности**: каждое попадание отцепляет узел и переносит его в голову, каждый промах вставляет в голову и, когда кэш заполнен, выбрасывает хвост. В Java это есть в виде `LinkedHashMap` с `accessOrder = true` и переопределённым `removeEldestEntry`. Production-кэши (Caffeine, Redis) используют приближения LRU/LFU, потому что строгий LRU плохо ведёт себя при сканированиях и дорог в плане блокировок; выбор политики разобран в [[caching]].

## Основное

### Почему хеш-map + двусвязный список
| Потребность | Структура | Стоимость |
|---|---|---|
| найти запись по ключу | хеш-map `key → node` ([[hash-tables]]) | O(1) |
| перенести узел в «самые свежие» | двусвязный список: отцепить за O(1), потому что узел знает `prev` и `next` ([[linked-lists]]) | O(1) |
| найти жертву вытеснения | хвост списка | O(1) |
| вытеснить | удалить хвостовой узел, удалить его ключ из map | O(1) |

Односвязному списку нужно O(n), чтобы найти предшественника; деку на массиве нужно O(n), чтобы удалить из середины; `TreeMap` по временной метке дал бы O(log n). Только пара map + двусвязный список даёт O(1) везде.

### Операции
| Операция | Шаги | Сложность |
|---|---|---|
| `get(k)` | поиск в map; при попадании отцепить узел, прицепить в голову | O(1) |
| `put(k, v)` | если есть: обновить значение, перенести в голову; иначе создать узел в голове, положить в map; если `size > capacity`: отцепить хвост, удалить из map | O(1) |
| `remove(k)` | удалить из map, отцепить узел | O(1) |
| память | на запись: запись map + узел списка (или один совмещённый узел) + ключ + значение | O(capacity) |

Сигнальные узлы головы и хвоста убирают все проверки на null из кода прицепления/отцепления.

### Трюк с `LinkedHashMap`
- `new LinkedHashMap<>(capacity, 0.75f, true)`: третий аргумент переключает внутренний двусвязный список с порядка вставки на **порядок доступа**; `get` и `put` переносят запись в конец.
- Переопредели `protected boolean removeEldestEntry(Map.Entry<K,V> eldest)`, чтобы он возвращал `size() > capacity`; map вызывает его после каждой вставки и вытесняет первую (самую старую) запись.
- Это ровно устройство map + список, где список протянут через сами записи map ([[hashmap-internals]]). Не потокобезопасно: оберни в `Collections.synchronizedMap` или блокировку либо используй Caffeine.

### Варианты и политики
| Политика | Идея | Плюсы / минусы |
|---|---|---|
| **LRU** | вытеснять давно не использовавшееся | просто, хорошая временная локальность; однократное сканирование N+1 ключей вымывает весь кэш |
| **LFU** | вытеснять реже всего используемое | устойчива к сканированиям; новые записи умирают молодыми, нужно затухание; LFU за O(1) использует map корзин частот |
| **LRU-K / 2Q / SLRU** | защищать записи, к которым обращались ≥ 2 раз | устойчивы к сканированиям; менеджер буферов Postgres использует clock-sweep со счётчиками использования |
| **Clock / второй шанс (second chance)** | кольцо с битом обращения, без перемещений в списке | дешёвое приближение, используемое страничными кэшами ОС ([[virtual-memory-and-paging]]) |
| **TinyLFU / W-TinyLFU** | фильтр допуска (count-min sketch) + сегментированный LRU | политика Caffeine по умолчанию; лучшие доли попаданий на практике ([[bloom-filters-and-probabilistic]]) |
| **Выборочный LRU (sampled LRU)** | выбрать k случайных ключей, вытеснить самый старый | `allkeys-lru` в Redis: без списка, приближённо, O(1) накладных расходов памяти ([[redis-patterns-and-persistence]]) |
| **TTL** | вытеснять по истечению срока, а не по давности | ортогонально; сочетается с любой из перечисленных |

### Конкурентность
- Глобальная блокировка вокруг get/put сериализует каждое попадание в кэш; годится для локального кэша на запрос, но не для горячего общего.
- `ConcurrentHashMap` + lock-free-структура для порядка давности — это сложно (перемещение узлов списка под конкуренцией); Caffeine буферизует события доступа в кольцевых буферах и применяет их к политике асинхронно, поэтому чтения почти не блокируются ([[concurrent-collections]], [[lock-free-and-cas]]).
- `@Cacheable` в Spring подключается к Caffeine или Redis, а не к самописному LRU ([[spring-cache-abstraction]]); распределённая инвалидация — отдельная задача ([[distributed-cache-coherence]]).

### Чек-лист для собеседования
- Назвать две структуры и почему нужны обе; написать `get`/`put` с сигнальными узлами; упомянуть `LinkedHashMap` как короткий путь в JDK; упомянуть потокобезопасность и то, что строгий LRU уязвим к сканированиям; знать, что LFU за O(1) существует.

## Пример
Явная реализация map + двусвязный список плюс однострочник на `LinkedHashMap`. Здесь используется как кэш декодированных eSIM-профилей в пределах экземпляра с ключом ICCID.

```java
public final class LruCache<K, V> {
    private static final class Node<K, V> {
        K key; V value; Node<K, V> prev, next;
        Node(K key, V value) { this.key = key; this.value = value; }
    }

    private final int capacity;
    private final Map<K, Node<K, V>> index;
    private final Node<K, V> head = new Node<>(null, null);  // сигнальный: самый свежий — сразу после head
    private final Node<K, V> tail = new Node<>(null, null);  // сигнальный: самый давний — прямо перед tail

    public LruCache(int capacity) {
        this.capacity = capacity;
        this.index = HashMap.newHashMap(capacity);             // Java 19+: размер под capacity записей
        head.next = tail; tail.prev = head;
    }

    public V get(K key) {
        Node<K, V> n = index.get(key);
        if (n == null) return null;
        unlink(n); linkFirst(n);                               // обращение: перенести в самые свежие
        return n.value;
    }

    public void put(K key, V value) {
        Node<K, V> n = index.get(key);
        if (n != null) { n.value = value; unlink(n); linkFirst(n); return; }
        n = new Node<>(key, value);
        index.put(key, n); linkFirst(n);
        if (index.size() > capacity) {
            Node<K, V> victim = tail.prev;                     // давно не использовавшийся
            unlink(victim); index.remove(victim.key);
        }
    }

    private void linkFirst(Node<K, V> n) {
        n.next = head.next; n.prev = head;
        head.next.prev = n; head.next = n;
    }

    private void unlink(Node<K, V> n) {
        n.prev.next = n.next; n.next.prev = n.prev;
    }
}

// Короткий путь в JDK: LinkedHashMap с порядком доступа и хуком вытеснения
Map<String, Profile> cache = new LinkedHashMap<>(256, 0.75f, true) {
    @Override protected boolean removeEldestEntry(Map.Entry<String, Profile> eldest) {
        return size() > 256;
    }
};
```

## Подводные камни
- Забыть перенести узел при `get`: кэш деградирует до FIFO и вытесняет горячие записи.
- Забыть удалить вытесненный ключ из map (или узел из списка): две структуры расходятся, и память утекает.
- Использовать `LinkedHashMap` со значением по умолчанию `accessOrder = false`: он вытесняет в порядке вставки (FIFO), а не LRU.
- Выставлять несинхронизированный LRU-кэш как singleton-бин Spring: конкурентные вызовы `get` портят указатели списка. В реальных сервисах используй Caffeine (`Caffeine.newBuilder().maximumSize(n)`).
- Связанные заметки: [[caching]] — стратегии и инвалидация, [[redis-patterns-and-persistence]] — политики распределённого вытеснения, [[linked-lists]] и [[hash-tables]] — два строительных блока.
