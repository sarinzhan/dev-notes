---
title: Ресурсы по Spring: ссылки, инструменты и шаблоны
tags: [spring, resources, links, tools]
learned: 2026-10-22
---
## Кратко
Собранные ссылки и инструменты для раздела Spring: списки вопросов для собеседований, глубокие разборы внутреннего устройства контейнера, генераторы админ-панелей, UI-шаблоны и заготовки проектов. Заметки со знаниями не ссылаются за пределы хранилища; всё внешнее собрано здесь.

## Основное
### Подготовка к собеседованию
- [Вопросы по Spring на собеседовании (sibinfo.ru)](https://www.sibinfo.ru/about/articles/java-spring-sobesedovanie.html): русскоязычный список типичных вопросов по Spring с короткими ответами; хороший чек-лист перед собеседованием.
- [Вопросы и ответы по Spring для собеседования (DigitalOcean)](https://www.digitalocean.com/community/tutorials/spring-interview-questions-and-answers): англоязычный список, охватывающий core, MVC, AOP, транзакции; ответы соотносятся с [[spring-ioc-and-di]], [[spring-bean-lifecycle]], [[spring-aop]], [[spring-transactional]].
- [Habr: глубокий разбор вопросов по Spring (статья 490586)](https://habr.com/ru/articles/490586/): более длинный русскоязычный текст о внутреннем устройстве контейнера и каверзных вопросах.
- [JavaRevisited, метка SQL](https://javarevisited.blogspot.com/search/label/SQL?max-results=3): блог с темами собеседований по Java и SQL; качество неровное, но охват широкий.
- [JSE helper: вопросы и ответы для собеседования Java Junior](https://jsehelper.blogspot.com/search/label/Interview?updated-max=2016-04-08T19%3A01%3A00%2B03%3A00&max-results=5): старые (2016) русскоязычные вопросы и ответы уровня junior; полезны для основ, но версии нужно сверять с актуальными фактами.

### Внутреннее устройство контейнера
- [Medium: «Spring под капотом» (Кирилл Середа, на русском)](https://medium.com/@kirill.sereda/spring-под-капотом-9d92f2bf1a04): пошаговый разбор обновления контекста, `BeanFactoryPostProcessor`, `BeanPostProcessor` и создания прокси; основа для [[spring-bean-lifecycle]].
- DMDEV (YouTube/Udemy, на русском): курс по Spring с подробным модулем о жизненном цикле и AOP.
- Евгений Борисов, «Spring-потрошитель» / «Spring Ripper» (доклады на JPoint, на русском, есть на YouTube): классический глубокий разбор BPP/BFPP, прокси и «магии» `@Configuration`; части 1 и 2.
- [Плейлист по Spring на YouTube (видео mhofV15i4Wc)](https://www.youtube.com/watch?v=mhofV15i4Wc): русскоязычный плейлист по Spring, сохранённый из заметок.

### Официальная документация, которую стоит держать в закладках
- Справочник Spring Framework (docs.spring.io/spring-framework): Core (IoC, AOP), Web (MVC, WebFlux), Data Access (транзакции).
- Справочник Spring Boot: «Core Features» (внешняя конфигурация, профили), «Production-ready Features» (Actuator) и приложение со всеми ключами `application.properties` ([[spring-boot-properties-and-profiles]]).
- Справочник Spring Security: глава «Architecture» для сервлетов и руководство по миграции 5→6 ([[spring-security-basics]]).
- Документация springdoc-openapi по настройке Swagger UI ([[openapi-swagger]]).

### Админ-панели и генерация кода
- SnapAdmin: подключаемая зависимость, которая строит CRUD-админку по JPA-сущностям; ноль кода, ограниченная настройка.
- KraftAdmin: похожая админ-панель на основе зависимости поверх JPA-сущностей.
- Amplicode: плагин IntelliJ, который генерирует код Spring (сущности, репозитории, контроллеры, админ-интерфейс) и настраивается под проект.
- Spring Boot Admin: не CRUD-панель, а интерфейс мониторинга приложений поверх эндпоинтов Actuator (health, метрики, логгеры, env) для всех зарегистрированных экземпляров.
- JHipster: полноценный генератор приложений (бэкенд на Spring Boot + фронтенд на Angular/React/Vue, аутентификация, CI) для быстрого старта проекта.

### UI-шаблоны для фронтенда админок
- Velzon, Nazox, Adminto: коммерческие шаблоны админ-дашбордов на Bootstrap (Themesbrand), используемые как отправная точка для фронтенда внутренних инструментов.

### Шаблоны проектов
- [spring-boot-template-project (hieubz, GitHub)](https://github.com/hieubz/spring-boot-template-project): заготовка Spring Boot со своим взглядом на структуру — слоистая архитектура, обработка исключений и настройка Docker; прежде чем копировать, сравни со своими соглашениями.
- start.spring.io: официальный инициализатор; самый быстрый способ получить корректный файл сборки Boot 3.x с управляемыми версиями ([[spring-boot-starters-and-autoconfiguration]]).

### Инструменты тестирования, упомянутые в заметках
- WireMock, MockRestServiceServer, Testcontainers, Spring Cloud Contract, MockServer: см. сравнение в [[rest-clients]] и [[testing-types-and-dev-cycles]].

## Подводные камни
- Многие из этих ссылок русскоязычные, а некоторые старые (2016–2020): проверяй каждый факт, зависящий от версии (стиль конфигурации Spring Security 6, `jakarta.*`, `RestClient`), по актуальной документации, прежде чем повторять его на собеседовании.
- Сгенерированные админ-панели по умолчанию открывают доступ ко всем сущностям; закрывай их через [[spring-security-basics]] и никогда не размещай на публичном хосте.
- Связанные заметки: [[architecture-resources]], [[devops-resources]].
