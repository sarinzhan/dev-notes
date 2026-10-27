---
title: Dockerfile, multi-stage сборки, слои и кеш
tags: [docker, dockerfile, build-cache, multi-stage]
learned: 2026-10-22
---
## Кратко
Dockerfile — это сценарий из инструкций, который `docker build` выполняет шаг за шагом, превращая каждую в слой образа. Кеш сборки переиспользует слой, если инструкция и её входные данные не изменились, поэтому **порядок инструкций определяет скорость сборки**: редко меняющееся (базовый образ, зависимости) ставь в начало, а jar — в конец. **Многоэтапные сборки (multi-stage builds)** компилируют в одном этапе и копируют только артефакт в маленький образ для запуска.

## Основное
### Инструкции
| Инструкция | Что делает |
|---|---|
| `FROM image[:tag] [AS name]` | Базовый образ; первая инструкция (после необязательного `ARG`). `FROM scratch` — пустая база |
| `RUN cmd` | Выполняет команду во время сборки и сохраняет результат как новый слой (`apt-get install`, `mvn package`) |
| `COPY src dst` / `ADD` | Копирует файлы из **контекста сборки (build context)** — каталога, переданного в `docker build`, обычно `.` — в образ. `ADD` также распаковывает локальные tar-архивы и скачивает URL; предпочитай `COPY`, если это не нужно |
| `WORKDIR /app` | Задаёт рабочий каталог для последующих `RUN`, `CMD`, `ENTRYPOINT`, `COPY`; создаёт его, если нет |
| `ENV KEY=value` | Переменная окружения во время сборки *и* в запущенном контейнере |
| `ARG NAME=default` | Переменная только времени сборки (`--build-arg`); во время выполнения отсутствует |
| `EXPOSE 8080` | Документирует, что контейнер слушает порт. **Не публикует его**; это делает `-p 8080:8080` или Compose/Swarm. Другие инструменты читают это как метаданные |
| `CMD ["java","-jar","app.jar"]` | Команда по умолчанию, полностью заменяется через `docker run image <other cmd>` |
| `ENTRYPOINT ["java","-jar","app.jar"]` | Исполняемый файл; `CMD` или аргументы запуска добавляются к нему как аргументы. Используй exec-форму (JSON-массив), чтобы процесс был PID 1 и получал сигналы ([[linux-processes]]) |
| `VOLUME /data` | Объявляет точку монтирования; если туда ничего не смонтировано, создаётся анонимный volume ([[docker-volumes-and-registry]]) |
| `USER app` | Выполнять последующие инструкции и сам контейнер от этого пользователя, а не от root |
| `HEALTHCHECK CMD ...` | Команда, которую Docker периодически запускает, чтобы сообщать `healthy`/`unhealthy` ([[docker-containers-and-resources]]) |
| `LABEL`, `SHELL`, `STOPSIGNAL`, `ONBUILD` | Метаданные, оболочка для `RUN`, сигнал для `docker stop`, триггеры для дочерних образов |

### Слои и кеширование
- Каждая инструкция создаёт слой (`RUN`, `COPY`, `ADD` добавляют содержимое файловой системы; `ENV`, `WORKDIR`, `CMD` добавляют только метаданные). Docker хранит слои в локальном кеше.
- При пересборке Docker проходит инструкции по порядку и проверяет, изменились ли инструкция *и* её входные данные: для `RUN` — строка команды; для `COPY`/`ADD` — контрольная сумма копируемых файлов. Если ничего не изменилось, переиспользуется закешированный слой. **Первый изменившийся слой инвалидирует все слои после него**, потому что каждый слой строится поверх предыдущего.
- Поэтому упорядочивай инструкции от наименее к наиболее изменчивым: базовый образ, системные пакеты, манифест зависимостей (`pom.xml`) и скачивание зависимостей, затем исходный код, затем сборка.
- `RUN apt-get update && apt-get install -y x && rm -rf /var/lib/apt/lists/*` должно быть одной инструкцией: отдельный `rm` в более позднем слое не уменьшает предыдущий слой, а закешированный отдельный `apt-get update` отдаёт устаревшие списки пакетов.
- `.dockerignore` не пускает в контекст `target/`, `.git`, файлы IDE: меньше загружаемых данных и меньше ложных промахов кеша.
- BuildKit (по умолчанию с Docker 23) добавляет `RUN --mount=type=cache,target=/root/.m2` для постоянного кеша зависимостей между сборками без запекания его в слои, `--mount=type=secret` для учётных данных и параллельное выполнение этапов.

### Многоэтапные сборки
- Несколько этапов `FROM` в одном Dockerfile. Сборка идёт на этапе с полным JDK и Maven (`mvn package`), затем в новом этапе для запуска `COPY --from=build` копирует только артефакты. В итоговом образе нет компилятора, исходников и `.m2`.
- Преимущества: маленький образ (JRE вместо JDK, 200 МБ вместо 700 МБ), меньшая поверхность атаки, воспроизводимые сборки, не зависящие от инструментов CI runner. `--target build` собирает только один этап (для запуска тестов в CI).
- Слоистые jar в Spring Boot: `java -Djarmode=tools -jar app.jar extract --layers --destination extracted` (Boot 3.3+; раньше `-Djarmode=layertools`) разбивает fat jar на `dependencies`, `spring-boot-loader`, `snapshot-dependencies`, `application`, так что изменение кода пересобирает только последний маленький слой ([[spring-boot-starters-and-autoconfiguration]]).

### Чек-лист хороших практик
- Фиксируй теги базового образа (`eclipse-temurin:21.0.4_7-jre-jammy`) или дайджесты; `latest` невоспроизводим.
- Запускай от не-root `USER`; где возможно — корневая ФС только для чтения (`--read-only`) с `tmpfs` для `/tmp`.
- Один процесс на контейнер; логи в stdout/stderr ([[logging-stacks]]).
- Используй exec-форму `ENTRYPOINT` и дай JVM читать лимиты контейнера (`-XX:MaxRAMPercentage=75.0`, по умолчанию в JDK 10+).
- Сканируй образы (`docker scout cves`, Trivy) и регулярно пересобирай, чтобы получать исправления базового образа.

## Пример
```dockerfile
# syntax=docker/dockerfile:1.7
# ---- этап сборки: JDK + Maven, закешированное скачивание зависимостей ----
FROM maven:3.9.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
RUN --mount=type=cache,target=/root/.m2 mvn -q -B dependency:go-offline     # в кеше, пока не изменится pom.xml
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -q -B -DskipTests package \
 && java -Djarmode=tools -jar target/*.jar extract --layers --destination /layers

# ---- этап запуска: только JRE, не root, слоистый jar ----
FROM eclipse-temurin:21-jre-jammy
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build /layers/dependencies/ ./
COPY --from=build /layers/spring-boot-loader/ ./
COPY --from=build /layers/snapshot-dependencies/ ./
COPY --from=build /layers/application/ ./                 # только этот слой меняется от релиза к релизу
USER app
EXPOSE 8080
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75.0 -XX:+ExitOnOutOfMemoryError"
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://localhost:8080/actuator/health || exit 1
ENTRYPOINT ["java", "org.springframework.boot.loader.launch.JarLauncher"]
```
```bash
docker build -t registry.example.com/esim/api:1.4.2 .
docker build --target build -t esim-api-build .          # остановиться после этапа сборки (здесь запускаются тесты)
```

## Подводные камни
- `COPY . .` первым шагом копирования инвалидирует кеш зависимостей при каждом изменении исходников; сначала копируй `pom.xml`.
- Shell-форма `ENTRYPOINT java -jar app.jar` оборачивает JVM в `/bin/sh -c`; `SIGTERM` получает оболочка, а не Java, и `docker stop` заканчивается `SIGKILL` после таймаута.
- Секреты, переданные через `ARG` или `ENV`, попадают в историю образа (`docker history`); используй BuildKit `--mount=type=secret` или секреты времени выполнения ([[docker-swarm]]).
- `EXPOSE` без `-p`, и люди удивляются, почему порт недоступен.
- `curl` отсутствует в distroless и некоторых slim-образах; `HEALTHCHECK` молча падает. Используй маленькую проверку на Java или shell либо установи curl в финальном этапе.
- Связанные заметки: [[docker-fundamentals]], [[maven]], [[docker-containers-and-resources]].
