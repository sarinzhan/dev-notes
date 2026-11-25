---
title: Загрузка классов, загрузчики и делегирование родителю
tags: [jvm, class-loading, classloader, parent-delegation, static-init]
learned: 2026-11-25
---
## Кратко
Класс проходит три стадии: **загрузку** (ClassLoader находит байты, JVM строит метаданные в Metaspace и объект `Class` в куче), **связывание** (linking: проверка → подготовка → разрешение) и **инициализацию** (выполняется `<clinit>`). HotSpot делает всё это лениво, и загрузка и инициализация — разные события: `Foo.class` или `loadClass("Foo")` загружают класс, не выполняя его блок `static {}`. Загрузчики образуют цепочку (Bootstrap → Platform → Application → пользовательский), и каждый сначала спрашивает родителя, поэтому основные классы нельзя подменить, а у каждого класса ровно один определяющий загрузчик.

## Основное
### Три стадии
```
Loading  →  Linking (Verify → Prepare → Resolve)  →  Initialization
```
1. **Загрузка (loading).** ClassLoader находит байты `.class` по двоичному имени (`com.acme.Foo`): из jar, из папки, по сети или сгенерированные во время работы (прокси, CGLIB). JVM строит метаданные класса в **Metaspace** и создаёт объект `java.lang.Class` в **куче** ([[jvm-memory-areas]]).
2. **Связывание (linking).**
   - **Проверка (verification):** байткод типобезопасен, стек операндов не переполняется, ни один переход не выходит за пределы метода. Ошибка → `VerifyError`.
   - **Подготовка (preparation):** под статические поля выделяется память, и они получают значения **по умолчанию** (`0`, `null`, `false`), а не значения из твоего кода. Константы времени компиляции (`static final int X = 5`) получают своё значение уже здесь.
   - **Разрешение (resolution):** символьные ссылки в пуле констант (`"java/util/List"`) становятся прямыми ссылками. HotSpot делает это лениво, при первом использовании.
3. **Инициализация.** JVM выполняет `<clinit>`: присваивания статических полей и блоки `static {}` в порядке исходного кода, под блокировкой на класс, ровно один раз. На этой блокировке держится синглтон по идиоме holder ([[creational-patterns]]). Подробный порядок статической инициализации и инициализации экземпляра — в [[class-initialization-order]].

### Metaspace и объект `Class`
Загрузка создаёт две половины одного класса, связанные друг с другом:
- **Metaspace** (нативная память) — **чертёж класса для JVM**: раскладка полей и размер объекта, методы и байткод, пул констант, vtable, данные профилирования для JIT. JVM читает его при `new Foo()` (размер, раскладка), при `foo.bar()` (какой байткод, какая реализация), при первом разрешении (имена → адреса) и когда JIT-компилирует горячий код.
- **Объект `java.lang.Class`** (куча) — **ручка для Java-кода**, потому что Java может ссылаться только на Java-объекты: `Foo.class`, `getClass()` и рефлексия идут через него, **статические поля живут в нём**, `static synchronized` блокируется на нём, и пока он достижим, класс и его загрузчик остаются живы.

### Когда класс загружается
- **Запуск:** Bootstrap загружает основные классы (`Object`, `String`, `Thread`…), затем загрузчик Application загружает главный класс.
- **Разрешение ссылки:** в первый раз, когда выполняемый код обращается к `Foo` (`new Foo()`, `Foo.bar()`, `new Foo[3]`).
- **Проверка другого класса:** верификатор может загрузить `Foo` только для того, чтобы проверить, что он подтип `Bar`, поэтому класс может быть загружен раньше, чем выполнится хоть строчка его кода.
- **Явные вызовы:** `Class.forName`, `loader.loadClass`, литерал `Foo.class`, рефлексия и фреймворки с прокси (Spring загружает тысячи классов при сканировании пакетов).
- Посмотреть: `java -Xlog:class+load -jar app.jar`. Приложение на Spring Boot загружает 10–20 тысяч классов; это заметная часть времени его запуска и расхода Metaspace.

### Когда класс инициализируется
- **Запускают (первое активное использование):** `new`, вызов статического метода, чтение или запись неконстантного статического поля, `Class.forName(name)`, рефлексия над членом класса, инициализация подкласса (суперкласс идёт первым), роль главного класса.
- **Не запускают:** `loader.loadClass(name)`, `Class.forName(name, false, loader)`, литерал `Foo.class`, `new Foo[10]`, чтение константы времени компиляции (javac встраивает её).
- Поэтому `Class.forName("com.mysql.cj.jdbc.Driver")` регистрирует JDBC-драйвер, а `loadClass` — нет: регистрация находится в блоке `static {}` драйвера.

### Загрузчики классов (Java 9+)
| Загрузчик | Что загружает | Заметки |
|---|---|---|
| Bootstrap | `java.base` и другие основные модули | Нативный код; `String.class.getClassLoader()` возвращает `null` |
| Platform | Остальные модули JDK (`java.sql`, …) | До Java 9 назывался Extension |
| Application (System) | Classpath / module path | Загрузчик по умолчанию для твоего кода |
| Пользовательский | Что угодно | Tomcat, загрузчик fat-jar в Spring Boot, плагинные системы |

### Делегирование родителю (parent delegation)
`loadClass(name)` работает в три шага:
1. `findLoadedClass` — вернуть класс, если этот загрузчик уже его загрузил.
2. Спросить родителя, рекурсивно вплоть до Bootstrap.
3. Только если родитель не нашёл, `findClass` — загрузчик сам ищет байты.

Зачем:
- **Безопасность:** никто не подсунет свой `java.lang.String`, потому что Bootstrap всегда побеждает.
- **Одна копия каждого класса:** идентичность класса — это (определяющий загрузчик, полное имя). Одни и те же байты, загруженные двумя загрузчиками, — два разных типа: `instanceof` не проходит, и получаешь `ClassCastException: Foo cannot be cast to Foo`.

Где правило нарушают намеренно:
- **Tomcat:** загрузчик веб-приложения сначала смотрит в `WEB-INF` (child-first), так что каждое приложение может использовать свои версии библиотек.
- **JDBC / `ServiceLoader` (SPI):** `DriverManager` загружен загрузчиком JDK, который не видит jar твоего драйвера, поэтому он использует **контекстный загрузчик потока (thread context ClassLoader)**, чтобы дотянуться вниз до загрузчика Application.
- **Spring Boot DevTools:** отдельный restart-загрузчик для твоего кода; горячая перезагрузка выбрасывает его и создаёт новый.

### Выгрузка
Класс выгружается, только когда его загрузчик становится недостижимым и собирается сборщиком мусора. Классы загрузчиков Bootstrap, Platform и Application остаются в Metaspace до завершения JVM.

## Пример
Загружен или инициализирован — по шагам. Запуск: `java -Xlog:class+load ClassLoadingDemo` (без пакета).
```java
public class ClassLoadingDemo {

    static class Lazy {
        static { System.out.println("   -> Lazy: <clinit> ran"); }
        static final int CONSTANT = 42;   // константа времени компиляции
        static void hello() { System.out.println("   Lazy.hello()"); }
    }

    static class Parent { static { System.out.println("   -> Parent: <clinit> ran"); } }
    static class Child extends Parent { static { System.out.println("   -> Child: <clinit> ran"); } }

    public static void main(String[] args) throws Exception {
        ClassLoader loader = ClassLoadingDemo.class.getClassLoader();
        String name = "ClassLoadingDemo$Lazy";

        System.out.println("1. Lazy.CONSTANT = " + Lazy.CONSTANT); // встроено javac
        System.out.println("2. new Lazy[3]: " + new Lazy[3].length); // Lazy загружен, без инициализации
        System.out.println("3. Lazy.class: " + Lazy.class.getSimpleName()); // без инициализации
        loader.loadClass(name);
        System.out.println("4. loader.loadClass(...) done"); // без инициализации
        Class.forName(name, false, loader);
        System.out.println("5. Class.forName(name, false, loader) done"); // без инициализации
        System.out.println("6. Lazy.hello():");
        Lazy.hello(); // первое активное использование -> <clinit>
        System.out.println("7. new Child():");
        new Child(); // суперкласс инициализируется первым
    }
}
```
Реальный вывод (строки JDK убраны):
```
[class,load] ClassLoadingDemo source: file:/.../
1. Lazy.CONSTANT = 42
[class,load] ClassLoadingDemo$Lazy source: file:/.../
2. new Lazy[3]: 3
3. Lazy.class: Lazy
4. loader.loadClass(...) done
5. Class.forName(name, false, loader) done
6. Lazy.hello():
   -> Lazy: <clinit> ran
   Lazy.hello()
7. new Child():
[class,load] ClassLoadingDemo$Parent source: file:/.../
[class,load] ClassLoadingDemo$Child source: file:/.../
   -> Parent: <clinit> ran
   -> Child: <clinit> ran
```
Что происходит при запуске:
1. **Старт JVM:** Bootstrap загружает основные классы; создаются загрузчики Platform и Application.
2. **Главный класс:** Application спрашивает Platform, тот спрашивает Bootstrap; ни у кого его нет, поэтому Application загружает `ClassLoadingDemo` из classpath. Статического состояния у него нет, значит, нет и `<clinit>`; запускается `main`.
3. **Шаг 1:** `Lazy.CONSTANT` заменён на `42` при компиляции — `Lazy` даже не загружается.
4. **Шаг 2:** для создания `Lazy[]` нужно разрешить `Lazy`, поэтому он **загружается**, но создание массива — не активное использование, так что он **не инициализируется**.
5. **Шаги 3–5:** `Lazy` берётся из кэша загрузчика; `forName(name, false, …)` явно пропускает инициализацию.
6. **Шаг 6:** вызов статического метода — первое активное использование: `<clinit>` выполняется один раз, затем `hello()`.
7. **Шаг 7:** загрузка `Child` сначала загружает его суперкласс `Parent`; `Parent` инициализируется раньше `Child`.

Замени шаг 5 на `Class.forName(name)` — и `<clinit>` переедет на шаг 5.

## Подводные камни
- Блок `static {}`, бросивший исключение, один раз даёт `ExceptionInInitializerError`; каждое следующее использование класса бросает `NoClassDefFoundError`, а исходная причина к тому времени давно ушла из логов. Статические блоки должны быть тривиальными.
- `ClassCastException: Foo cannot be cast to Foo` значит, что `Foo` загрузили два загрузчика (серверы приложений, плагины, restart-загрузчик DevTools и библиотека вне его).
- Утёкший загрузчик классов (поток или `ThreadLocal`, который всё ещё держит один из его классов) удерживает в Metaspace все загруженные им классы; повторные redeploy заканчиваются `OutOfMemoryError: Metaspace`.
- `ClassNotFoundException` (проверяемое, явный `forName`/`loadClass` не нашёл класс) — это не `NoClassDefFoundError` (класс был при компиляции, но сейчас его нельзя загрузить или инициализировать).
- Связанные заметки: [[class-initialization-order]], [[jvm-memory-areas]], [[compilation-interpretation-jit]], [[object-class-and-reflection]].
