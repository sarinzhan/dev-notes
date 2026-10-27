---
title: Материалы по архитектуре и system design
tags: [resources, system-design, software-design, architecture]
learned: 2026-10-22
---
## Кратко
Подборка ссылок по system design (подготовка к собеседованиям, курсы, практика) и проектированию ПО (чистая архитектура, взаимодействие микросервисов, DDD), плюс чек-лист тем system design, которые нужно уметь объяснить. Сами знания живут в [[solid]], [[ddd-basics]] и заметках о паттернах; эта заметка — только список для чтения.

## Основное
### System design: основы и дорожные карты
- [System Design Primer](https://github.com/donnemartin/system-design-primer) — канонический бесплатный репозиторий на GitHub: масштабируемость, кэширование, базы данных, примеры задач с решениями.
- [roadmap.sh/system-design](https://roadmap.sh/system-design) — визуальная дорожная карта тем в порядке изучения.
- [Teach Yourself CS](https://teachyourselfcs.com) — список книг/курсов по основам CS (ОС, сети, распределённые системы).
- [CS Primer: Distributed Systems](https://csprimer.com/courses/distributed-systems/) — практический курс по консенсусу, репликации, партиционированию.
- [ByteByteGo: Software Architecture guides](https://bytebytego.com/guides/software-architecture/) — короткие иллюстрированные объяснения.
- [Яндекс: Архитектура распределённых систем (видео, RU)](https://www.youtube.com/live/VoDGD_-d-Sg) — лекция об архитектуре распределённых систем.

### System design: подготовка к собеседованиям и курсы
- [16 Best Resources to Crack the System Design Interview](https://medium.com/javarevisited/11-best-resources-to-crack-the-system-design-interview-2cbc77f6fc76) — обзор книг, курсов и репозиториев от Javarevisited.
- [4 Steps to Prepare for System Design Interviews](https://javarevisited.blogspot.com/2022/03/how-to-prepare-for-system-design.html) — план подготовки.
- [Top 10 Courses to Crack System Design Interview](https://javarevisited.blogspot.com/2022/07/best-system-design-and-object-oriented.html) — сравнение курсов.
- [codemia.io](https://codemia.io) — практические задачи по system design в стиле LeetCode.
- [Arslan Ahmad на Medium](https://arslan-ahmad.medium.com) — основатель DesignGurus; статьи о паттернах собеседований.
- [Educative: Scalability & System Design for Developers](https://www.educative.io/path/scalability-system-design) — учебный трек в текстовом формате.
- [Educative: Grokking Modern System Design Interview](https://www.educative.io/courses/grokking-the-system-design-interview) — классический текстовый курс для собеседований.
- [DesignGurus: Grokking the Coding Interview](https://www.designgurus.io/course/grokking-the-coding-interview) — паттерны алгоритмических собеседований (дополнение к курсу по system design).
- [Rate limiting (видео)](https://www.youtube.com/watch?v=8QyygfIloMc) — token bucket, leaky bucket, скользящее окно.

### Проектирование и архитектура ПО
- [The Clean Architecture (Uncle Bob)](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html) — оригинальный пост о правиле зависимостей и концентрических слоях.
- [Взаимодействие микросервисов для начинающих (Habr, RU)](https://habr.com/ru/companies/maxilect/articles/677128/) — синхронное и асинхронное взаимодействие между сервисами.
- [Я забил на споры о микросервисах с архитекторами (proglib, RU)](https://proglib.io/p/ya-zabil-na-spory-o-mikroservisah-s-arhitektorami-i-vot-pochemu-2025-04-02) — колонка мнений о прагматике монолита и микросервисов.
- [Статья Habr 873388 (RU)](https://habr.com/ru/articles/873388/) и [статья Habr 911018 (RU)](https://habr.com/ru/articles/911018/) — статьи об архитектуре, сохранённые из ленты.
- Архитектура проектов на Go: слоистая, гексагональная, чистая, DDD — заметки из сессии в чате; выжимка включена в [[ddd-basics]].

### Чек-лист system design (темы, которые нужно уметь объяснить)
- **Масштабирование и производительность**: горизонтальное и вертикальное масштабирование; балансировка нагрузки (round robin, least connections, консистентное хеширование); стратегии кэширования (cache-aside, write-through, write-back, см. [[spring-cache-abstraction]], [[redis-data-structures]]); CDN и статический контент; индексы и оптимизация запросов ([[database-indexes]], [[postgres-indexes]]).
- **Надёжность**: теорема CAP; репликация баз данных (primary-replica, multi-primary); circuit breaker; retry с экспоненциальным backoff; health checks и плавное завершение (graceful shutdown); [[idempotency]] в API.
- **Асинхронность и очереди**: очереди сообщений ([[kafka-architecture]], [[rabbitmq-basics]]); событийно-ориентированная архитектура; паттерн «Сага» для распределённых транзакций ([[outbox-pattern]]); dead-letter queues ([[kafka-consumer]]).
- **Данные**: SQL и NoSQL и когда что использовать; шардирование и партиционирование; ACID и BASE ([[transactions-acid-and-isolation]]); согласованность в конечном счёте.

## Подводные камни
- Читать о system design, ничего не рисуя: для каждого материала стоит отработать один дизайн от начала до конца (требования, оценки, API, модель данных, масштабирование, сценарии сбоев).
- Курсы для собеседований учат шаблону; реальные системы на работе (eSIM provisioning, анализ инцидентов) — лучший материал для практики, чем сокращатели URL.
- Связанные заметки: [[devops-resources]], [[spring-resources]].
