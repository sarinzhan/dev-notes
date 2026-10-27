---
title: Класс Object, clone и рефлексия
tags: [object, clone, reflection, finalize, cleaner]
learned: 2026-10-22
---
## Кратко
`java.lang.Object` — корень каждого класса; он предоставляет идентичность (`equals`, `hashCode`), описание (`toString`, `getClass`), копирование (`clone`), методы монитора (`wait`, `notify`, `notifyAll`) и устаревший `finalize`. Рефлексия (`java.lang.Class` и `java.lang.reflect`) позволяет программе исследовать и изменять классы, поля, методы и конструкторы во время выполнения; именно благодаря ей работают Spring, Hibernate, Jackson и JUnit.

## Основное
### Методы Object
| Метод | Назначение | Примечания |
|---|---|---|
| `equals(Object)` | логическое равенство | по умолчанию `==`; см. [[equals-and-hashcode]] |
| `hashCode()` | хеш для хеш-коллекций | `native`, по умолчанию identity hash |
| `toString()` | человекочитаемое представление | по умолчанию `ClassName@hexIdentityHash`; переопределяй для логов |
| `getClass()` | `Class<?>` времени выполнения | `final`; точка входа в рефлексию |
| `clone()` | поверхностная копия | `protected native`; требует `Cloneable` |
| `wait()`, `notify()`, `notifyAll()` | координация через монитор | `final`; нужно владеть монитором, см. [[synchronization-and-monitors]] |
| `finalize()` | хук перед тем, как GC освободит объект | устарел с 9, помечен к удалению с 18; никогда на него не полагайся |

- `finalize` выполняется в отдельном потоке финализатора в неизвестный момент (или никогда), задерживает освобождение минимум на один цикл GC и может «воскрешать» объекты. Замены: `try-with-resources` с `AutoCloseable` для детерминированного освобождения, `java.lang.ref.Cleaner` (Java 9) как страховка ([[reference-types]]).

### clone и Cloneable
- `Object.clone()` — `protected native`: он выделяет новый объект того же класса и побитово копирует каждое поле — это **поверхностная копия** (shallow copy). Примитивы копируются по значению; ссылочные поля указывают на те же объекты, что и в оригинале.
- `Cloneable` — интерфейс-маркер без методов; если класс его не реализует, `Object.clone()` бросает `CloneNotSupportedException`. Чтобы клонирование было пригодным, переопредели `clone()` как `public`, вызови `super.clone()` и сам сделай глубокую копию изменяемых полей.
- Массивы реализуют `Cloneable`, и `clone()` у них публичный и быстрый; это единственное место, где `clone` идиоматичен.
- Во всех остальных случаях он считается ошибкой дизайна (обходит конструкторы, не позволяет переприсвоить `final`-поля, проверяемое исключение). Предпочитай копирующий конструктор, статическую фабрику `copyOf` или методы в стиле `with` у records ([[immutable-objects]]).

### Рефлексия
- Получение `Class`: `obj.getClass()`, `Type.class`, `Class.forName("fq.Name")` (ещё и инициализирует класс, см. [[class-initialization-order]]), `classLoader.loadClass`.
- Что можно делать: найти суперкласс и интерфейсы; перечислить модификаторы, поля, методы, конструкторы (`getDeclaredX` включает приватные, `getX` — только публичные, включая унаследованные); читать аннотации ([[annotations]]); создавать экземпляры (`getDeclaredConstructor(...).newInstance(...)`); читать и записывать значения полей; вызывать методы; создавать массивы (`Array.newInstance`); строить динамические прокси (`java.lang.reflect.Proxy`, основа AOP на JDK-прокси в [[spring-aop]]).
- `setAccessible(true)` обходит проверки доступа; начиная с Java 9 для внутренностей JDK это запрещено, если модуль не открыт (`--add-opens java.base/java.lang=ALL-UNNAMED`), а начиная с 16 строгая инкапсуляция включена по умолчанию. Твой собственный код в classpath по-прежнему полностью открыт сам для себя.
- Цена: поиск медленный (сопоставление строк, проверки безопасности); кешируй объекты `Method`/`Field`. Вызов через `Method.invoke` в несколько раз медленнее прямого вызова и непрозрачен для JIT; `MethodHandle` (Java 7) и `LambdaMetafactory` быстрее после связывания, поэтому фреймворки перешли на них.
- Рефлексия ломает статический анализ и GraalVM native-image, если не зарегистрировать элементы, к которым идёт обращение.

### Где она используется
- Внедрение зависимостей и создание бинов ([[spring-ioc-and-di]]), заполнение сущностей в ORM ([[jpa-persistence-context-and-entity-lifecycle]]), (де)сериализация JSON, тестовые фреймворки, находящие методы `@Test`, сериализация, отладчики и профилировщики.

## Пример
```java
// Минимальный «маппер», который копирует одноимённые поля между DTO и сущностью так,
// как это делают BeanUtils/ModelMapper, с кешированием объектов Field для каждой пары классов.
public final class FieldCopier {
    private static final Map<String, List<Field[]>> CACHE = new ConcurrentHashMap<>();

    public static <S, T> T copy(S source, T target) {
        String key = source.getClass().getName() + "->" + target.getClass().getName();
        List<Field[]> pairs = CACHE.computeIfAbsent(key, k -> pair(source.getClass(), target.getClass()));
        try {
            for (Field[] p : pairs) p[1].set(target, p[0].get(source));
        } catch (IllegalAccessException e) {
            throw new IllegalStateException(e);
        }
        return target;
    }

    private static List<Field[]> pair(Class<?> src, Class<?> dst) {
        List<Field[]> result = new ArrayList<>();
        for (Field sf : src.getDeclaredFields()) {
            if (Modifier.isStatic(sf.getModifiers())) continue;
            try {
                Field df = dst.getDeclaredField(sf.getName());
                if (df.getType() != sf.getType()) continue;
                sf.setAccessible(true); df.setAccessible(true);
                result.add(new Field[]{sf, df});
            } catch (NoSuchFieldException ignored) { }
        }
        return result;
    }
}
```

## Подводные камни
- `clone()` объекта с полем `List` даёт два объекта, разделяющих один список; классическая «копия», которая копией не является.
- Рефлексивный доступ к приватным полям привязывает твой код к чужим деталям реализации и ломается при обновлении; JDK теперь блокирует его для своих внутренностей.
- `Class.forName` в библиотеке, загруженной родительским загрузчиком, не видит классы в дочерних загрузчиках (Tomcat, fat jar Spring Boot); используй контекстный загрузчик классов потока (thread context class loader).
- `Method.invoke` оборачивает исключение целевого метода в `InvocationTargetException`; разворачивай `getCause()` перед логированием, иначе настоящая ошибка будет скрыта ([[exceptions]]).
- Связанные заметки: [[annotations]], [[equals-and-hashcode]], [[reference-types]].
