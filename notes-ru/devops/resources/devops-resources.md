---
title: Ресурсы по DevOps, ОС и общему обучению
tags: [resources, linux, docker, roadmap]
learned: 2026-10-22
---
## Кратко
Список чтения для DevOps-части хранилища: Linux/ОС, Docker, nginx, плюс общие ссылки «roadmap для senior Java» и бэклог того, что предстоит изучить (ClickHouse). Знания лежат в заметках [[linux-basics|linux]], [[docker-fundamentals|docker]], [[git-commands|git]], [[maven]] и [[logging-stacks|observability]]; эта страница только отслеживает, где почитать подробнее.

## Основное
### Операционные системы и Linux
- [How Linux works (плейлист на YouTube, RU)](https://www.youtube.com/playlist?list=PLHHm04DXWzeKZycf_ZuBgxWdVBnrjE_mj) — серия видео о внутреннем устройстве Linux: ядро, процессы, файловые системы; дополняет [[linux-architecture-and-kernel]] и [[linux-processes]].
- План изучения, перенесённый из исходных заметок (темы по порядку): основы (FHS, базовые команды, пользователи/права, менеджеры пакетов) -> архитектура (пространство ядра и пространство пользователя, системные вызовы, `strace`, системы инициализации, оболочки, инструменты GNU) -> процессы (`fork`/`execve`, сигналы, `ps`/`htop`/`nice`, юниты systemd) -> память/файловые системы/IO (виртуальная память, страничная организация, swap, ext4/XFS/btrfs, `/proc` и `/sys`, планировщики IO) -> сеть (стек, ARP/IP/ICMP/TCP/UDP, iproute2, nftables, DNS, DHCP) -> внутреннее устройство ядра (модули, sysfs/procfs, планировщик, пространства имён, cgroups) -> скрипты на shell (bash, awk/sed, cron, таймеры systemd). Теперь у каждого этапа есть заметка: [[linux-basics]], [[linux-architecture-and-kernel]], [[linux-processes]], [[linux-memory-filesystems-io]], [[linux-networking]], [[linux-commands]].

### Docker и контейнеры
- [Документация Docker](https://docs.docker.com/) — справочник по CLI, Dockerfile, Compose, Swarm, BuildKit; источник истины, когда флаг ведёт себя неожиданно.
- *Docker Deep Dive*, Nigel Poulton — книга о внутреннем устройстве движка, образах, сети, volumes, Swarm и безопасности; лучшее, что можно прочитать после документации.
- План изучения Docker из исходных заметок: зачем существует Docker и что он заменил; контейнеры и VM; образы и контейнеры; CLI и Compose; сеть и volumes; лучшие практики Dockerfile; управление контейнерами и логами. Покрыто заметками [[docker-fundamentals]], [[dockerfile-and-images]], [[docker-containers-and-resources]], [[docker-volumes-and-registry]], [[docker-swarm]], [[docker-socket-dind-dood]].

### Веб-серверы
- [The NGINX Handbook (freeCodeCamp)](https://www.freecodecamp.org/news/the-nginx-handbook/) — объёмный учебник: раздача статики, reverse proxy, балансировка нагрузки, кеширование, TLS; reverse proxy перед сервисами [[docker-swarm]].

### Карьерные roadmap для Java
- [Roadmap: Java Senior Developer (LinkedIn)](https://www.linkedin.com/pulse/roadmap-java-senior-developer-dineshchand-g-r/) — чек-лист навыков от core Java до архитектуры и DevOps.
- [Senior Java Developer 2024 (dev.to)](https://dev.to/antofik/senior-java-developer-2024-2cof) — ещё один roadmap со списками инструментов и тем; полезен для сверки пробелов в этом хранилище.

### Изучить (бэклог)
- [Введение в ClickHouse (Habr/OTUS, RU)](https://habr.com/ru/companies/otus/articles/773174/) — колоночная OLAP-база данных; актуальна для аналитики по событиям инцидентов и provisioning рядом с Postgres ([[database-fundamentals]]).
- [Статья Habr 910194 (RU)](https://habr.com/ru/articles/910194/) — сохранена из ленты, не классифицирована; прочитать и отнести к нужной заметке.
- Spring Boot Admin — UI для мониторинга приложений через actuators Boot (отмечено в ссылках по Spring; место рядом с [[logging-stacks]] и заметкой о метриках [[otel-metric-types]]).

## Подводные камни
- Список ссылок — это не знание: после прочтения ресурса перенеси важное в тематическую заметку и оставь здесь только однострочный указатель.
- Плейлисты на русском хороши для понимания, но практикуй английские термины (namespaces, cgroups, page cache), потому что их используют на собеседованиях и в документации.
- Связанные заметки: [[architecture-resources]], [[spring-resources]].
